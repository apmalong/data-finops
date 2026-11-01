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


def test_an_event_time_without_a_time_zone_is_refused(store: WorkloadStore, tmp_path: Path) -> None:
    path = _write(tmp_path / "events.jsonl", [_event(RUN, "START", "2026-11-01T21:19:00")])
    with pytest.raises(ValueError, match="event 0: eventTime has no time zone"):
        store.ingest_events(path)
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
