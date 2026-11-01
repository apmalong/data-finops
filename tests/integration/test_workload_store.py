"""The workload store through its public interface on a temporary SQLite file (M2 seam, #40)."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from data_finops.contracts.workload import WorkloadRunStatus
from data_finops.ledger import Ledger
from data_finops.workload.store import WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
EVENTS = FIXTURES / "openlineage" / "events.json"
AIRFLOW_TASK = "00000000-0000-4000-8000-000000000001"
DBT_RUN = "00000000-0000-4000-8000-000000000002"
MODEL_RUN = "00000000-0000-4000-8000-000000000003"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WorkloadStore]:
    with WorkloadStore(tmp_path / "data-finops.sqlite") as opened:
        yield opened


def _event(run_id: str, event_type: str, at: str) -> dict[str, Any]:
    return {
        "eventType": event_type,
        "eventTime": at,
        "producer": "https://example.invalid/producer",
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
        "job": {"namespace": "demo", "name": "job"},
        "run": {"runId": run_id, "facets": {}},
    }


def _write(path: Path, events: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def test_ingesting_events_records_workload_runs_with_status_and_times(store: WorkloadStore) -> None:
    result = store.ingest_events(EVENTS)
    assert (result.accepted, result.duplicates) == (5, 0)
    task = store.workload_run(AIRFLOW_TASK)
    assert task is not None
    assert (task.job_namespace, task.job_name) == ("demo-airflow", "daily_orders.dbt_run")
    assert task.status == WorkloadRunStatus.COMPLETED
    assert task.started_at == datetime(2026, 10, 1, 0, 59, 50, tzinfo=UTC)
    assert task.ended_at == datetime(2026, 10, 1, 1, 2, tzinfo=UTC)


def test_a_run_reported_only_by_its_terminal_event_has_no_start(store: WorkloadStore) -> None:
    store.ingest_events(EVENTS)
    model = store.workload_run(MODEL_RUN)
    assert model is not None
    assert (model.status, model.started_at) == (WorkloadRunStatus.COMPLETED, None)


def test_reingesting_the_same_file_changes_nothing(store: WorkloadStore) -> None:
    store.ingest_events(EVENTS)
    before = store.workload_run(DBT_RUN)
    result = store.ingest_events(EVENTS)
    assert (result.accepted, result.duplicates) == (0, 5)
    assert store.workload_run(DBT_RUN) == before


@pytest.mark.parametrize(
    ("terminal", "status"),
    [
        (None, WorkloadRunStatus.RUNNING),
        ("COMPLETE", WorkloadRunStatus.COMPLETED),
        ("FAIL", WorkloadRunStatus.FAILED),
        ("ABORT", WorkloadRunStatus.ABORTED),
    ],
)
def test_status_follows_the_terminal_event(
    store: WorkloadStore, tmp_path: Path, terminal: str | None, status: WorkloadRunStatus
) -> None:
    run_id = "0190f4a1-7a5e-7c1e-9f3a-1b2c3d4e5f60"
    events = [_event(run_id, "START", "2026-11-01T21:19:00Z")]
    if terminal:
        events.append(_event(run_id, terminal, "2026-11-01T21:19:30Z"))
    store.ingest_events(_write(tmp_path / "events.jsonl", events))
    run = store.workload_run(run_id)
    assert run is not None and run.status == status


def test_unknown_runs_are_absent(store: WorkloadStore) -> None:
    assert store.workload_run("0190f4a1-0000-7000-8000-000000000000") is None


def test_store_shares_the_database_file_with_the_ledger(tmp_path: Path) -> None:
    path = tmp_path / "data-finops.sqlite"
    with Ledger(path), WorkloadStore(path) as store:
        assert store.ingest_events(EVENTS).accepted == 5


RUN = "0190f4a1-7a5e-7c1e-9f3a-1b2c3d4e5f60"


def test_two_different_terminal_events_make_the_run_conflicting(
    store: WorkloadStore, tmp_path: Path
) -> None:
    events = [
        _event(RUN, "FAIL", "2026-11-01T21:19:30Z"),
        _event(RUN, "COMPLETE", "2026-11-01T21:19:31Z"),
    ]
    store.ingest_events(_write(tmp_path / "events.jsonl", events))
    run = store.workload_run(RUN)
    assert run is not None and run.status == WorkloadRunStatus.CONFLICTING


def test_the_same_instant_with_another_offset_is_the_same_event(
    store: WorkloadStore, tmp_path: Path
) -> None:
    store.ingest_events(
        _write(tmp_path / "utc.jsonl", [_event(RUN, "START", "2026-11-01T21:19:00Z")])
    )
    again = store.ingest_events(
        _write(tmp_path / "offset.jsonl", [_event(RUN, "START", "2026-11-01T15:19:00-06:00")])
    )
    assert (again.accepted, again.duplicates) == (0, 1)


def test_an_event_time_without_a_time_zone_is_rejected(
    store: WorkloadStore, tmp_path: Path
) -> None:
    path = _write(tmp_path / "events.jsonl", [_event(RUN, "START", "2026-11-01T21:19:00")])
    result = store.ingest_events(path)
    assert [r.position for r in result.rejected] == [1]
    assert store.workload_run(RUN) is None


def test_a_run_reported_under_two_jobs_takes_the_earliest_events_job(
    store: WorkloadStore, tmp_path: Path
) -> None:
    later = {
        **_event(RUN, "COMPLETE", "2026-11-01T21:19:30Z"),
        "job": {"namespace": "demo", "name": "zz"},
    }
    store.ingest_events(
        _write(tmp_path / "events.jsonl", [later, _event(RUN, "START", "2026-11-01T21:19:00Z")])
    )
    run = store.workload_run(RUN)
    assert run is not None and run.job_name == "job"


# Invalid payloads (#42)

INVALID = FIXTURES / "openlineage" / "invalid" / "mixed.jsonl"


def test_invalid_events_are_rejected_one_by_one_and_the_rest_ingested(store: WorkloadStore) -> None:
    result = store.ingest_events(INVALID)
    assert (result.accepted, result.unsupported) == (3, 2)
    reasons = {r.position: r.reason for r in result.rejected}
    assert sorted(reasons) == [2, 3, 4, 5]
    assert reasons[2] == "not valid JSON"
    assert reasons[3].startswith("invalid RunEvent: run/runId")
    assert reasons[4].startswith("invalid RunEvent: eventTime")
    assert reasons[5].startswith("invalid ParentRunFacet")
    completed = store.workload_run("00000000-0000-4000-8000-0000000000a1")
    assert completed is not None and completed.status == WorkloadRunStatus.COMPLETED
    other_type = store.workload_run("00000000-0000-4000-8000-0000000000a7")
    assert other_type is not None  # any eventType value is tolerated


def test_rejected_events_are_never_stored_or_echoed(store: WorkloadStore) -> None:
    result = store.ingest_events(INVALID)
    assert store.workload_run("00000000-0000-4000-8000-0000000000a3") is None
    assert store.workload_run("00000000-0000-4000-8000-0000000000a4") is None
    assert not any("not-a-uuid-secret-value" in r.reason for r in result.rejected)


def test_an_event_over_one_mebibyte_is_rejected(store: WorkloadStore, tmp_path: Path) -> None:
    big = _event(RUN, "START", "2026-11-01T21:19:00Z")
    big["run"]["facets"] = {
        "padding": {"_producer": "x", "_schemaURL": "x", "data": "x" * 1_048_576}
    }
    result = store.ingest_events(_write(tmp_path / "big.jsonl", [big]))
    assert [(r.position, r.reason) for r in result.rejected] == [(1, "too large")]
    assert store.workload_run(RUN) is None


def test_a_json_array_reports_positions_by_element(store: WorkloadStore, tmp_path: Path) -> None:
    events = [_event(RUN, "START", "2026-11-01T21:19:00Z"), {"eventTime": "nope"}]
    path = tmp_path / "events.json"
    path.write_text(json.dumps(events), encoding="utf-8")
    result = store.ingest_events(path)
    assert result.accepted == 1 and [r.position for r in result.rejected] == [2]


def test_a_file_that_is_not_json_at_all_is_one_rejection(
    store: WorkloadStore, tmp_path: Path
) -> None:
    path = tmp_path / "events.json"
    path.write_text("[not json", encoding="utf-8")
    result = store.ingest_events(path)
    assert (result.accepted, [r.reason for r in result.rejected]) == (0, ["not valid JSON"])


def test_bundled_schemas_match_the_upstream_checker_pins() -> None:
    import hashlib
    import importlib.util

    root = FIXTURES.parent
    spec = importlib.util.spec_from_file_location(
        "verify_upstream", root / "scripts" / "verify_upstream.py"
    )
    assert spec and spec.loader
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    bundled = root / "src" / "data_finops" / "lineage" / "schemas"
    for name, file in [
        ("openlineage-2-0-2", "OpenLineage-2-0-2.json"),
        ("openlineage-parent-run-1-1-0", "ParentRunFacet-1-1-0.json"),
    ]:
        assert hashlib.sha256((bundled / file).read_bytes()).hexdigest() == checker.PINNED[name][1]
