"""One OpenLineage event from bytes through the workload store's public interface (#104).

The HTTP receiver (spec 04, M5) passes each request body here; it must screen and store
exactly as a file item does (spec 06, Testing seam 3).
"""

import codecs
import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from data_finops.lineage.validation import RejectionKind
from data_finops.workload.store import IngestResult, WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
EVENTS = FIXTURES / "openlineage" / "events.json"
INVALID_LINES = FIXTURES / "openlineage" / "invalid" / "mixed.jsonl"
INVALID_ARRAY = FIXTURES / "openlineage" / "invalid" / "array.json"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WorkloadStore]:
    with WorkloadStore(tmp_path / "one-by-one.sqlite") as opened:
        yield opened


@pytest.fixture
def file_store(tmp_path: Path) -> Iterator[WorkloadStore]:
    with WorkloadStore(tmp_path / "whole-file.sqlite") as opened:
        yield opened


def _fixture_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = json.loads(EVENTS.read_text(encoding="utf-8"))
    return events


def _items(path: Path) -> list[bytes]:
    """A fixture file's items as request bodies: each line, or each array element."""
    if path.suffix == ".jsonl":
        return [line for line in path.read_bytes().split(b"\n") if line.strip()]
    elements = json.loads(path.read_text(encoding="utf-8"))
    return [json.dumps(e, ensure_ascii=False).encode("utf-8") for e in elements]


def _run_ids(bodies: list[bytes]) -> set[str]:
    """Every runId a body names, whether or not the body is valid."""
    run_ids = set()
    for body in bodies:
        try:
            run_ids.add(json.loads(body)["run"]["runId"])
        except (ValueError, TypeError, KeyError):
            continue
    return run_ids


def _totals(results: list[IngestResult]) -> tuple[int, int, int, Counter[RejectionKind]]:
    kinds: Counter[RejectionKind] = Counter()
    for result in results:
        kinds += result.counts()
    return (
        sum(r.accepted for r in results),
        sum(r.duplicates for r in results),
        sum(r.changed_copies for r in results),
        kinds,
    )


@pytest.mark.parametrize("path", [EVENTS, INVALID_LINES, INVALID_ARRAY], ids=lambda p: p.name)
def test_a_files_items_ingested_one_at_a_time_match_the_whole_file(
    store: WorkloadStore, file_store: WorkloadStore, path: Path
) -> None:
    bodies = _items(path)
    whole = file_store.ingest_events(path)
    one_by_one = [store.ingest_event(body) for body in bodies]
    assert _totals(one_by_one) == _totals([whole])
    for run_id in _run_ids(bodies):
        assert store.stored_events(run_id) == file_store.stored_events(run_id)
        assert store.workload_run(run_id) == file_store.workload_run(run_id)


@pytest.mark.parametrize("path", [INVALID_LINES, INVALID_ARRAY], ids=lambda p: p.name)
def test_each_invalid_item_is_rejected_or_unsupported_with_the_files_kind_and_detail(
    store: WorkloadStore, file_store: WorkloadStore, path: Path
) -> None:
    whole = file_store.ingest_events(path)
    expected = {r.position: r for r in (*whole.rejected, *whole.unsupported)}
    bodies = _items(path) if path.suffix != ".jsonl" else path.read_bytes().split(b"\n")
    for position, rejection in expected.items():
        result = store.ingest_event(bodies[position - 1])
        got = (*result.rejected, *result.unsupported)
        assert [(r.position, r.kind, r.detail) for r in got] == [
            (1, rejection.kind, rejection.detail)
        ]
        assert result.accepted == 0


def test_sql_text_and_stack_traces_are_removed_before_storage(store: WorkloadStore) -> None:
    event = _fixture_events()[0]
    facet = {"_producer": "https://example.invalid/p", "_schemaURL": "https://example.invalid/s"}
    event["job"]["facets"] = {"sql": facet | {"query": "SELECT secret_column FROM t"}}
    event["run"]["facets"]["errorMessage"] = facet | {
        "message": "failed",
        "programmingLanguage": "python",
        "stackTrace": "Traceback: secret_frame",
    }
    assert store.ingest_event(json.dumps(event).encode("utf-8")).accepted == 1
    (stored,) = store.stored_events(event["run"]["runId"])
    assert "secret_column" not in stored and "secret_frame" not in stored
    assert '"message":"failed"' in stored


def test_a_pretty_printed_body_is_one_event(store: WorkloadStore) -> None:
    result = store.ingest_event(json.dumps(_fixture_events()[0], indent=2).encode("utf-8"))
    assert (result.accepted, result.rejected) == (1, ())


def test_a_body_over_one_mebibyte_is_too_large_and_not_stored(store: WorkloadStore) -> None:
    event = _fixture_events()[0]
    event["run"]["facets"]["padding"] = {"_producer": "x", "_schemaURL": "x", "data": "x" * 2**20}
    result = store.ingest_event(json.dumps(event).encode("utf-8"))
    assert [(r.position, r.kind) for r in result.rejected] == [(1, RejectionKind.TOO_LARGE)]
    assert store.workload_run(event["run"]["runId"]) is None


def test_the_same_body_twice_is_a_duplicate(store: WorkloadStore) -> None:
    body = json.dumps(_fixture_events()[0]).encode("utf-8")
    store.ingest_event(body)
    result = store.ingest_event(body)
    assert (result.accepted, result.duplicates, result.changed_copies) == (0, 1, 0)


def test_other_content_under_the_same_key_is_a_changed_copy(store: WorkloadStore) -> None:
    event = _fixture_events()[0]
    store.ingest_event(json.dumps(event).encode("utf-8"))
    event["producer"] = "https://example.invalid/another-producer"
    result = store.ingest_event(json.dumps(event).encode("utf-8"))
    assert (result.accepted, result.changed_copies) == (1, 1)


def test_an_array_body_is_not_one_event(store: WorkloadStore) -> None:
    result = store.ingest_event(json.dumps(_fixture_events()[:1]).encode("utf-8"))
    assert (result.accepted, [r.kind for r in result.rejected]) == (
        0,
        [RejectionKind.INVALID_RUN_EVENT],
    )


def test_a_byte_order_mark_is_ignored_as_in_files(store: WorkloadStore) -> None:
    body = codecs.BOM_UTF8 + json.dumps(_fixture_events()[0]).encode("utf-8")
    result = store.ingest_event(body)
    assert (result.accepted, result.rejected) == (1, ())
