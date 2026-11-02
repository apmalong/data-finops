"""The workload store through its public interface on a temporary SQLite file (M2 seam, #40)."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from data_finops.contracts.workload import WorkloadRunStatus
from data_finops.ledger import Ledger
from data_finops.lineage.validation import RejectionKind
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
    assert result.accepted == 3
    assert [(u.position, u.kind) for u in result.unsupported] == [
        (6, RejectionKind.UNSUPPORTED_JOB_EVENT),
        (7, RejectionKind.UNSUPPORTED_DATASET_EVENT),
    ]
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


ARRAY = FIXTURES / "openlineage" / "invalid" / "array.json"


def _line(event: dict[str, Any]) -> bytes:
    return json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"


@pytest.mark.parametrize("event_type", ["FOO", "complete", None])
def test_any_event_type_is_tolerated(store: WorkloadStore, tmp_path: Path, event_type: Any) -> None:
    event = _event(RUN, "START", "2026-11-01T21:19:00Z") | {"eventType": event_type}
    result = store.ingest_events(_write(tmp_path / "events.jsonl", [event]))
    assert (result.accepted, result.rejected) == (1, ())


def test_one_hostile_line_never_stops_the_file(store: WorkloadStore, tmp_path: Path) -> None:
    deep = b'{"a":' * 100_000 + b"1" + b"}" * 100_000 + b"\n"
    path = tmp_path / "events.jsonl"
    path.write_bytes(
        _line(_event(RUN, "START", "2026-11-01T21:19:00Z"))
        + deep
        + b"\xff\xfe not utf-8\n"
        + b'{"eventTime": NaN}\n'
        + _line(_event(RUN, "COMPLETE", "2026-11-01T21:19:30Z"))
    )
    result = store.ingest_events(path)
    assert result.accepted == 2
    assert [(r.position, r.kind) for r in result.rejected] == [
        (2, RejectionKind.TOO_DEEP),
        (3, RejectionKind.NOT_UTF8),
        (4, RejectionKind.NOT_JSON),
    ]


def test_only_valid_job_and_dataset_events_count_as_unsupported(
    store: WorkloadStore, tmp_path: Path
) -> None:
    fake_job = {"schemaURL": "x#/$defs/JobEvent", "junk": 1}
    run_with_dataset = _event(RUN, "START", "2026-11-01T21:19:00Z") | {"dataset": {}}
    del run_with_dataset["run"]
    result = store.ingest_events(_write(tmp_path / "events.jsonl", [fake_job, run_with_dataset]))
    assert result.unsupported == ()
    assert [r.kind for r in result.rejected] == [
        RejectionKind.INVALID_JOB_EVENT,
        RejectionKind.INVALID_DATASET_EVENT,
    ]


def test_array_elements_are_measured_in_utf8_like_lines(
    store: WorkloadStore, tmp_path: Path
) -> None:
    event = _event(RUN, "START", "2026-11-01T21:19:00Z")
    event["job"]["name"] = "数" * 300_000  # 900 KB in UTF-8, 1.8 MB if escaped to ASCII
    path = tmp_path / "events.json"
    path.write_text(json.dumps([event], ensure_ascii=False), encoding="utf-8")
    assert store.ingest_events(path).accepted == 1


def test_rejections_are_in_file_order_and_counted_by_kind(store: WorkloadStore) -> None:
    result = store.ingest_events(INVALID)
    assert [r.position for r in result.rejected] == sorted(r.position for r in result.rejected)
    counts = result.counts()
    assert counts[RejectionKind.INVALID_RUN_EVENT] == 2 and counts[RejectionKind.NOT_JSON] == 1


def test_invalid_array_elements_are_rejected_by_element(store: WorkloadStore) -> None:
    result = store.ingest_events(ARRAY)
    assert result.accepted == 2
    assert [(r.position, r.kind) for r in result.rejected] == [
        (2, RejectionKind.INVALID_RUN_EVENT),
        (3, RejectionKind.INVALID_RUN_EVENT),
    ]


def test_a_schema_failure_before_an_unreadable_line_keeps_file_order(
    store: WorkloadStore, tmp_path: Path
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(_line({"eventTime": "nope"}) + b"{not json\n")
    assert [r.position for r in store.ingest_events(path).rejected] == [1, 2]


# Run graph (#43)

SAMPLE_EVENTS = FIXTURES / "reference-workload" / "openlineage" / "dbt-events.jsonl"
DAG_RUN = "00000000-0000-4000-8000-000000000000"
SECOND_RUN = "01a198fb-5bd3-79d5-8157-42a7993bce14"  # the sample's second dbt-ol run


def test_parent_and_root_come_from_the_parent_run_facet(store: WorkloadStore) -> None:
    store.ingest_events(EVENTS)
    dbt_run = store.workload_run(DBT_RUN)
    assert dbt_run is not None
    assert (dbt_run.parent_run_id, dbt_run.parent_job_namespace, dbt_run.parent_job_name) == (
        AIRFLOW_TASK,
        "demo-airflow",
        "daily_orders.dbt_run",
    )
    assert (dbt_run.root_run_id, dbt_run.root_job_name) == (DAG_RUN, "daily_orders")


def test_a_run_whose_parent_sent_no_events_still_records_it(store: WorkloadStore) -> None:
    store.ingest_events(EVENTS)
    task = store.workload_run(AIRFLOW_TASK)  # its START names the DAG run; its COMPLETE does not
    assert task is not None and task.parent_run_id == DAG_RUN and task.root_run_id is None
    assert store.workload_run(DAG_RUN) is None


def test_children_walk_from_the_airflow_task_to_the_model_runs(store: WorkloadStore) -> None:
    store.ingest_events(EVENTS)
    assert [run.run_id for run in store.children(AIRFLOW_TASK)] == [DBT_RUN]
    assert [run.run_id for run in store.children(DBT_RUN)] == [MODEL_RUN]
    assert store.children(MODEL_RUN) == []


def test_each_dbt_run_in_the_sample_has_its_four_model_runs(store: WorkloadStore) -> None:
    store.ingest_events(SAMPLE_EVENTS)
    model_runs = store.children(SECOND_RUN)
    assert sorted(run.job_name.rsplit(".", 1)[1] for run in model_runs) == [
        "daily_orders",
        "revenue_by_region",
        "stg_customers",
        "stg_orders",
    ]
    assert all(run.root_run_id == run.parent_run_id for run in model_runs)


def test_the_parent_may_arrive_on_a_later_event(store: WorkloadStore, tmp_path: Path) -> None:
    complete = _event(RUN, "COMPLETE", "2026-11-01T21:19:30Z")
    complete["run"]["facets"]["parent"] = {
        "_producer": "https://example.invalid/producer",
        "_schemaURL": "https://openlineage.io/spec/facets/1-1-0/ParentRunFacet.json#/$defs/ParentRunFacet",
        "run": {"runId": DAG_RUN},
        "job": {"namespace": "demo", "name": "parent-job"},
    }
    store.ingest_events(
        _write(tmp_path / "events.jsonl", [_event(RUN, "START", "2026-11-01T21:19:00Z"), complete])
    )
    run = store.workload_run(RUN)
    assert run is not None and (run.parent_run_id, run.parent_job_name) == (DAG_RUN, "parent-job")


def test_children_are_ordered_by_run_id(store: WorkloadStore) -> None:
    store.ingest_events(SAMPLE_EVENTS)
    ids = [run.run_id for run in store.children(SECOND_RUN)]
    assert len(ids) == 4 and ids == sorted(ids)


def _with_parent(event: dict[str, Any], parent: str, root: str | None = None) -> dict[str, Any]:
    facet: dict[str, Any] = {
        "_producer": "https://example.invalid/producer",
        "_schemaURL": "https://openlineage.io/spec/facets/1-1-0/ParentRunFacet.json#/$defs/ParentRunFacet",
        "run": {"runId": parent},
        "job": {"namespace": "demo", "name": "parent-job"},
    }
    if root:
        facet["root"] = {"run": {"runId": root}, "job": {"namespace": "demo", "name": "root-job"}}
    event["run"]["facets"]["parent"] = facet
    return event


OTHER_PARENT = "00000000-0000-4000-8000-0000000000c2"


def test_children_agree_with_the_parent_a_run_reports(store: WorkloadStore, tmp_path: Path) -> None:
    start = _with_parent(_event(RUN, "START", "2026-11-01T21:19:00Z"), DAG_RUN)
    complete = _with_parent(_event(RUN, "COMPLETE", "2026-11-01T21:19:30Z"), OTHER_PARENT)
    store.ingest_events(_write(tmp_path / "events.jsonl", [start, complete]))
    run = store.workload_run(RUN)
    assert run is not None and run.parent_run_id == DAG_RUN
    assert [r.run_id for r in store.children(DAG_RUN)] == [RUN]
    assert store.children(OTHER_PARENT) == []


def test_the_root_may_arrive_on_a_later_event(store: WorkloadStore, tmp_path: Path) -> None:
    start = _with_parent(_event(RUN, "START", "2026-11-01T21:19:00Z"), DAG_RUN)
    complete = _with_parent(_event(RUN, "COMPLETE", "2026-11-01T21:19:30Z"), DAG_RUN, OTHER_PARENT)
    store.ingest_events(_write(tmp_path / "events.jsonl", [start, complete]))
    run = store.workload_run(RUN)
    assert run is not None and run.root_run_id == OTHER_PARENT


def test_a_run_naming_itself_as_parent_is_rejected(store: WorkloadStore, tmp_path: Path) -> None:
    event = _with_parent(_event(RUN, "START", "2026-11-01T21:19:00Z"), RUN)
    result = store.ingest_events(_write(tmp_path / "events.jsonl", [event]))
    assert [r.kind for r in result.rejected] == [RejectionKind.INVALID_PARENT_FACET]
    assert store.children(RUN) == []


def test_children_of_a_run_that_sent_no_events(store: WorkloadStore) -> None:
    store.ingest_events(EVENTS)
    assert [run.run_id for run in store.children(DAG_RUN)] == [AIRFLOW_TASK]
