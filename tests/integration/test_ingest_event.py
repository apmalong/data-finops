"""One OpenLineage event from bytes through the workload store's public interface (#104).

The HTTP receiver (spec 04, M5) passes each request body here; it must screen and store
exactly as a file item does (spec 06, Testing seam 3).
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from data_finops.lineage.validation import RejectionKind
from data_finops.workload.store import WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
EVENTS = FIXTURES / "openlineage" / "events.json"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WorkloadStore]:
    with WorkloadStore(tmp_path / "one-by-one.sqlite") as opened:
        yield opened


@pytest.fixture
def from_file(tmp_path: Path) -> Iterator[WorkloadStore]:
    with WorkloadStore(tmp_path / "from-file.sqlite") as opened:
        opened.ingest_events(EVENTS)
        yield opened


def _bodies(path: Path) -> list[bytes]:
    return [json.dumps(e).encode("utf-8") for e in json.loads(path.read_text(encoding="utf-8"))]


def _run_ids(path: Path) -> set[str]:
    return {e["run"]["runId"] for e in json.loads(path.read_text(encoding="utf-8"))}


def test_events_ingested_one_at_a_time_are_stored_as_from_the_file(
    store: WorkloadStore, from_file: WorkloadStore
) -> None:
    results = [store.ingest_event(body) for body in _bodies(EVENTS)]
    assert [(r.accepted, r.duplicates) for r in results] == [(1, 0)] * 5
    for run_id in _run_ids(EVENTS):
        assert store.stored_events(run_id) == from_file.stored_events(run_id)
        assert store.workload_run(run_id) == from_file.workload_run(run_id)


INVALID = FIXTURES / "openlineage" / "invalid" / "mixed.jsonl"


def test_each_invalid_fixture_line_is_refused_as_in_the_file(
    store: WorkloadStore, tmp_path: Path
) -> None:
    with WorkloadStore(tmp_path / "from-file.sqlite") as file_store:
        expected = file_store.ingest_events(INVALID)
    by_position = {r.position: r for r in (*expected.rejected, *expected.unsupported)}
    lines = INVALID.read_bytes().split(b"\n")
    for position, refused in by_position.items():
        result = store.ingest_event(lines[position - 1])
        got = (*result.rejected, *result.unsupported)
        assert [(r.position, r.kind, r.detail) for r in got] == [(1, refused.kind, refused.detail)]
        assert result.accepted == 0


def _first_event() -> dict[str, Any]:
    event: dict[str, Any] = json.loads(EVENTS.read_text(encoding="utf-8"))[0]
    return event


def test_a_pretty_printed_body_is_one_event(store: WorkloadStore) -> None:
    result = store.ingest_event(json.dumps(_first_event(), indent=2).encode("utf-8"))
    assert (result.accepted, result.rejected) == (1, ())


def test_a_body_over_one_mebibyte_is_too_large_and_not_stored(store: WorkloadStore) -> None:
    event = _first_event()
    event["run"]["facets"]["padding"] = {"_producer": "x", "_schemaURL": "x", "data": "x" * 2**20}
    result = store.ingest_event(json.dumps(event).encode("utf-8"))
    assert [(r.position, r.kind) for r in result.rejected] == [(1, RejectionKind.TOO_LARGE)]
    assert store.workload_run(event["run"]["runId"]) is None


def test_the_same_body_twice_is_a_duplicate(store: WorkloadStore) -> None:
    body = json.dumps(_first_event()).encode("utf-8")
    store.ingest_event(body)
    result = store.ingest_event(body)
    assert (result.accepted, result.duplicates, result.changed_copies) == (0, 1, 0)


def test_other_content_under_the_same_key_is_a_changed_copy(store: WorkloadStore) -> None:
    event = _first_event()
    store.ingest_event(json.dumps(event).encode("utf-8"))
    event["producer"] = "https://example.invalid/another-producer"
    result = store.ingest_event(json.dumps(event).encode("utf-8"))
    assert (result.accepted, result.changed_copies) == (1, 1)


def test_an_array_body_is_not_one_event(store: WorkloadStore) -> None:
    result = store.ingest_event(json.dumps([_first_event()]).encode("utf-8"))
    assert (result.accepted, [r.kind for r in result.rejected]) == (
        0,
        [RejectionKind.INVALID_RUN_EVENT],
    )


def test_a_byte_order_mark_is_ignored_as_in_files(store: WorkloadStore) -> None:
    result = store.ingest_event(b"\xef\xbb\xbf" + json.dumps(_first_event()).encode("utf-8"))
    assert (result.accepted, result.rejected) == (1, ())
