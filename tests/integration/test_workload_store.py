"""The workload store through its public interface on a temporary SQLite file (M2 seam, #40)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from data_finops.contracts.workload import WorkloadRunStatus
from data_finops.workload.store import WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
EVENTS = FIXTURES / "openlineage" / "events.json"
AIRFLOW_TASK = "00000000-0000-4000-8000-000000000001"
DBT_RUN = "00000000-0000-4000-8000-000000000002"
MODEL_RUN = "00000000-0000-4000-8000-000000000003"


@pytest.fixture
def store(tmp_path: Path):  # type: ignore[no-untyped-def]
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
    from data_finops.ledger.ledger import Ledger

    path = tmp_path / "data-finops.sqlite"
    with Ledger(path), WorkloadStore(path) as store:
        assert store.ingest_events(EVENTS).accepted == 5
