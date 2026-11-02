"""The workload store through its public interface on a temporary SQLite file (M2 seam, #40)."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from data_finops.contracts.workload import (
    DbtObservationFlag,
    LineageDirection,
    WorkloadRunFlag,
    WorkloadRunStatus,
)
from data_finops.dbt import artifacts as dbt_artifacts
from data_finops.ledger import Ledger
from data_finops.lineage.validation import RejectionKind
from data_finops.workload.store import ArtifactOutcome, WorkloadStore

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


# Observations, conflicts and sensitive facets (#44)


def test_events_in_reverse_order_give_the_same_run(store: WorkloadStore, tmp_path: Path) -> None:
    start, complete = (
        _event(RUN, "START", "2026-11-01T21:19:00Z"),
        _event(RUN, "COMPLETE", "2026-11-01T21:19:30Z"),
    )
    store.ingest_events(_write(tmp_path / "complete.jsonl", [complete]))
    store.ingest_events(_write(tmp_path / "start.jsonl", [start]))
    reversed_order = store.workload_run(RUN)
    with WorkloadStore(tmp_path / "normal.sqlite") as normal:
        normal.ingest_events(_write(tmp_path / "both.jsonl", [start, complete]))
        assert reversed_order == normal.workload_run(RUN)


def test_a_changed_copy_of_an_event_is_kept_and_flagged(
    store: WorkloadStore, tmp_path: Path
) -> None:
    original = _event(RUN, "START", "2026-11-01T21:19:00Z")
    changed = _event(RUN, "START", "2026-11-01T21:19:00Z") | {
        "producer": "https://example.invalid/other"
    }
    store.ingest_events(_write(tmp_path / "a.jsonl", [original]))
    result = store.ingest_events(_write(tmp_path / "b.jsonl", [changed, original]))
    assert (result.accepted, result.duplicates, result.changed_copies) == (1, 1, 1)
    run = store.workload_run(RUN)
    assert run is not None and WorkloadRunFlag.CONFLICTING_OBSERVATIONS in run.flags


def test_an_absent_and_an_empty_event_type_are_not_one_key(
    store: WorkloadStore, tmp_path: Path
) -> None:
    absent = _event(RUN, "START", "2026-11-01T21:19:00Z")
    del absent["eventType"]
    result = store.ingest_events(
        _write(tmp_path / "events.jsonl", [absent, _event(RUN, "", "2026-11-01T21:19:00Z")])
    )
    assert result.changed_copies == 0
    run = store.workload_run(RUN)
    assert run is not None and run.flags == ()


def test_runs_reported_under_two_jobs_or_parents_are_flagged(
    store: WorkloadStore, tmp_path: Path
) -> None:
    later_job = _event(RUN, "COMPLETE", "2026-11-01T21:19:30Z") | {
        "job": {"namespace": "demo", "name": "zz"}
    }
    start = _with_parent(_event(RUN, "START", "2026-11-01T21:19:00Z"), DAG_RUN)
    other = _with_parent(_event(RUN, "OTHER", "2026-11-01T21:19:10Z"), OTHER_PARENT)
    store.ingest_events(_write(tmp_path / "events.jsonl", [start, other, later_job]))
    run = store.workload_run(RUN)
    assert run is not None
    assert set(run.flags) == {WorkloadRunFlag.CONFLICTING_JOBS, WorkloadRunFlag.CONFLICTING_PARENTS}


def test_sql_text_and_stack_traces_are_not_stored(store: WorkloadStore, tmp_path: Path) -> None:
    event = _event(RUN, "FAIL", "2026-11-01T21:19:30Z")
    event["job"]["facets"] = {
        "sql": {
            "_producer": "x",
            "_schemaURL": "x",
            "query": "select secret_column",
            "dialect": "athena",
        }
    }
    event["run"]["facets"]["errorMessage"] = {
        "_producer": "x",
        "_schemaURL": "x",
        "message": "boom",
        "programmingLanguage": "python",
        "stackTrace": "Traceback: secret_frame",
    }
    path = tmp_path / "events.jsonl"
    store.ingest_events(_write(path, [event]))
    stored = store.stored_events(RUN)
    assert len(stored) == 1
    assert "secret_column" not in stored[0] and "secret_frame" not in stored[0]
    assert '"dialect":"athena"' in stored[0] and '"message":"boom"' in stored[0]


def test_events_differing_only_in_removed_text_are_duplicates(
    store: WorkloadStore, tmp_path: Path
) -> None:
    def failed(query: str) -> dict[str, Any]:
        event = _event(RUN, "FAIL", "2026-11-01T21:19:30Z")
        event["job"]["facets"] = {"sql": {"_producer": "x", "_schemaURL": "x", "query": query}}
        return event

    store.ingest_events(_write(tmp_path / "a.jsonl", [failed("select 1")]))
    result = store.ingest_events(_write(tmp_path / "b.jsonl", [failed("select 2")]))
    assert (result.accepted, result.duplicates, result.changed_copies) == (0, 1, 0)


def test_events_differing_only_in_a_stack_trace_are_duplicates(
    store: WorkloadStore, tmp_path: Path
) -> None:
    def failed(trace: str) -> dict[str, Any]:
        event = _event(RUN, "FAIL", "2026-11-01T21:19:30Z")
        event["run"]["facets"]["errorMessage"] = {
            "_producer": "x",
            "_schemaURL": "x",
            "message": "boom",
            "programmingLanguage": "python",
            "stackTrace": trace,
        }
        return event

    store.ingest_events(_write(tmp_path / "a.jsonl", [failed("frame 1")]))
    result = store.ingest_events(_write(tmp_path / "b.jsonl", [failed("frame 2")]))
    assert (result.accepted, result.duplicates) == (0, 1)


def test_sql_and_stack_traces_elsewhere_are_not_stored(
    store: WorkloadStore, tmp_path: Path
) -> None:
    event = _event(RUN, "FAIL", "2026-11-01T21:19:30Z")
    sql = {"_producer": "x", "_schemaURL": "x", "query": "select secret_column"}
    event["run"]["facets"]["sql"] = sql
    event["run"]["facets"]["extractionError"] = {
        "_producer": "x",
        "_schemaURL": "x",
        "totalTasks": 1,
        "failedTasks": 1,
        "errors": [{"errorMessage": "failed", "stackTrace": "Traceback: secret_frame"}],
    }
    event["outputs"] = [
        {"namespace": "demo", "name": "t", "facets": {"sql": dict(sql)}, "outputFacets": {}}
    ]
    store.ingest_events(_write(tmp_path / "events.jsonl", [event]))
    stored = store.stored_events(RUN)
    assert stored and "secret_column" not in stored[0] and "secret_frame" not in stored[0]


def test_a_parent_named_with_two_jobs_is_flagged(store: WorkloadStore, tmp_path: Path) -> None:
    start = _with_parent(_event(RUN, "START", "2026-11-01T21:19:00Z"), DAG_RUN)
    other = _with_parent(_event(RUN, "COMPLETE", "2026-11-01T21:19:30Z"), DAG_RUN)
    other["run"]["facets"]["parent"]["job"]["name"] = "another-job"
    store.ingest_events(_write(tmp_path / "events.jsonl", [start, other]))
    run = store.workload_run(RUN)
    assert run is not None and WorkloadRunFlag.CONFLICTING_PARENTS in run.flags


# Data Assets and Lineage Edges (#45)

DAILY_ORDERS_RUN = "01a198fb-baa3-7325-bbb0-5695a9814454"  # daily_orders in the sample's second run
ATHENA = "awsathena://athena.us-east-1.amazonaws.com"


def test_a_model_run_reads_and_writes_assets_named_exactly(store: WorkloadStore) -> None:
    store.ingest_events(SAMPLE_EVENTS)
    assets = store.data_assets(DAILY_ORDERS_RUN)
    assert [(a.namespace, a.name) for a in assets] == [
        (ATHENA, "awsdatacatalog.reference_workload.daily_orders"),
        (ATHENA, "awsdatacatalog.reference_workload.stg_orders"),
    ]


def test_each_observation_records_its_edges(store: WorkloadStore) -> None:
    store.ingest_events(SAMPLE_EVENTS)
    edges = store.lineage_edges(DAILY_ORDERS_RUN)
    assert len(edges) == 4  # START and COMPLETE each report one input and one output
    assert {(e.direction, e.asset.name) for e in edges} == {
        (LineageDirection.INPUT, "awsdatacatalog.reference_workload.stg_orders"),
        (LineageDirection.OUTPUT, "awsdatacatalog.reference_workload.daily_orders"),
    }
    assert all(e.run_id == DAILY_ORDERS_RUN for e in edges)
    assert len({e.observed_at for e in edges}) == 2


def test_reingesting_adds_no_edges(store: WorkloadStore) -> None:
    store.ingest_events(SAMPLE_EVENTS)
    store.ingest_events(SAMPLE_EVENTS)
    assert len(store.lineage_edges(DAILY_ORDERS_RUN)) == 4


def test_names_are_kept_exactly_as_reported(store: WorkloadStore, tmp_path: Path) -> None:
    event = _event(RUN, "COMPLETE", "2026-11-01T21:19:30Z")
    event["outputs"] = [
        {"namespace": "S3://Mixed-Case/", "name": '"Quoted"."Table" ', "facets": {}}
    ]
    store.ingest_events(_write(tmp_path / "events.jsonl", [event]))
    (asset,) = store.data_assets(RUN)
    assert (asset.namespace, asset.name) == ("S3://Mixed-Case/", '"Quoted"."Table" ')


def test_a_run_without_datasets_has_no_assets(store: WorkloadStore) -> None:
    store.ingest_events(EVENTS)
    assert (store.data_assets(AIRFLOW_TASK), store.lineage_edges(AIRFLOW_TASK)) == ([], [])


def test_changed_copies_give_edges_told_apart_by_provenance(
    store: WorkloadStore, tmp_path: Path
) -> None:
    def complete(producer: str) -> dict[str, Any]:
        event = _event(RUN, "COMPLETE", "2026-11-01T21:19:30Z") | {"producer": producer}
        event["outputs"] = [{"namespace": "demo", "name": "t", "facets": {}}]
        return event

    store.ingest_events(_write(tmp_path / "a.jsonl", [complete("https://example.invalid/a")]))
    store.ingest_events(_write(tmp_path / "b.jsonl", [complete("https://example.invalid/b")]))
    edges = store.lineage_edges(RUN)
    assert len(edges) == 2 and len({e.provenance for e in edges}) == 2
    assert len(store.data_assets(RUN)) == 1


def test_a_dataset_listed_twice_in_one_event_is_one_edge(
    store: WorkloadStore, tmp_path: Path
) -> None:
    event = _event(RUN, "COMPLETE", "2026-11-01T21:19:30Z")
    dataset = {"namespace": "demo", "name": "t", "facets": {}}
    event["inputs"] = [dataset, dict(dataset)]
    store.ingest_events(_write(tmp_path / "events.jsonl", [event]))
    assert len(store.lineage_edges(RUN)) == 1


def test_dataset_facets_create_no_edges(store: WorkloadStore, tmp_path: Path) -> None:
    event = _event(RUN, "COMPLETE", "2026-11-01T21:19:30Z")
    event["outputs"] = [
        {
            "namespace": "demo",
            "name": "t",
            "facets": {
                "symlinks": {
                    "_producer": "x",
                    "_schemaURL": "x",
                    "identifiers": [{"namespace": "other", "name": "symlinked", "type": "TABLE"}],
                }
            },
            "outputFacets": {},
        }
    ]
    store.ingest_events(_write(tmp_path / "events.jsonl", [event]))
    assert [(a.namespace, a.name) for a in store.data_assets(RUN)] == [("demo", "t")]


# dbt run results into Model Executions (#46)

SAMPLE_DBT = FIXTURES / "reference-workload" / "dbt"
FIRST_RUN_INVOCATION = "d54e1d8c-43cb-42f4-a6ab-a055ab074a79"


def _edited_copy(source: Path, target: Path, change: Any) -> Path:
    document = json.loads(source.read_text(encoding="utf-8"))
    change(document)
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


def test_run_results_become_model_executions(store: WorkloadStore) -> None:
    result = store.ingest_dbt_artifact(SAMPLE_DBT / "2-run" / "run_results.json")
    assert (result.outcome, result.invocation_id) == (ArtifactOutcome.STORED, FIRST_RUN_INVOCATION)
    executions = store.model_executions(FIRST_RUN_INVOCATION)
    assert [(e.node_id, e.status) for e in executions] == [
        ("model.reference_workload.daily_orders", "success"),
        ("model.reference_workload.revenue_by_region", "success"),
        ("model.reference_workload.stg_customers", "success"),
        ("model.reference_workload.stg_orders", "success"),
    ]
    daily = executions[0]
    assert daily.invocation_id == FIRST_RUN_INVOCATION
    assert daily.started_at == datetime(2026, 11, 1, 21, 19, 18, 756670, tzinfo=UTC)
    assert daily.completed_at is not None and daily.completed_at > daily.started_at


def test_each_sample_step_yields_its_executions(store: WorkloadStore) -> None:
    counts = []
    for step in ("1-seed", "2-run", "3-run"):
        result = store.ingest_dbt_artifact(SAMPLE_DBT / step / "run_results.json")
        assert result.invocation_id is not None
        counts.append(len(store.model_executions(result.invocation_id)))
    assert counts == [2, 4, 4]


def test_the_hand_written_run_results_keep_dbt_statuses(store: WorkloadStore) -> None:
    store.ingest_dbt_artifact(FIXTURES / "dbt" / "run_results.json")
    assert {e.node_id: e.status for e in store.model_executions("inv-001")} == {
        "model.demo.customers": "success",
        "model.demo.orders": "success",
    }


def test_reingesting_run_results_is_a_duplicate(store: WorkloadStore) -> None:
    path = SAMPLE_DBT / "2-run" / "run_results.json"
    store.ingest_dbt_artifact(path)
    assert store.ingest_dbt_artifact(path).outcome == ArtifactOutcome.DUPLICATE
    assert len(store.model_executions(FIRST_RUN_INVOCATION)) == 4


def test_a_changed_file_for_the_same_invocation_is_kept_and_flagged(
    store: WorkloadStore, tmp_path: Path
) -> None:
    original = SAMPLE_DBT / "2-run" / "run_results.json"

    def failed(document: dict[str, Any]) -> None:
        document["results"][0]["status"] = "error"

    store.ingest_dbt_artifact(original)
    changed = _edited_copy(original, tmp_path / "run_results.json", failed)
    assert store.ingest_dbt_artifact(changed).outcome == ArtifactOutcome.CHANGED_COPY
    executions = store.model_executions(FIRST_RUN_INVOCATION)
    assert len(executions) == 8 and len({e.provenance for e in executions}) == 2
    assert all(DbtObservationFlag.CONFLICTING_OBSERVATIONS in e.flags for e in executions)


def test_other_run_results_versions_are_rejected(store: WorkloadStore, tmp_path: Path) -> None:
    def older(document: dict[str, Any]) -> None:
        document["metadata"]["dbt_schema_version"] = (
            "https://schemas.getdbt.com/dbt/run-results/v5.json"
        )

    path = _edited_copy(SAMPLE_DBT / "2-run" / "run_results.json", tmp_path / "rr.json", older)
    result = store.ingest_dbt_artifact(path)
    assert (result.outcome, result.reason) == (
        ArtifactOutcome.REJECTED,
        "unsupported run-results v5",
    )
    assert store.model_executions(FIRST_RUN_INVOCATION) == []


def test_an_oversized_artifact_is_rejected(
    store: WorkloadStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dbt_artifacts, "MAX_ARTIFACT_BYTES", 1024)
    result = store.ingest_dbt_artifact(SAMPLE_DBT / "2-run" / "run_results.json")
    assert (result.outcome, result.reason) == (ArtifactOutcome.REJECTED, "too large")


def test_run_results_without_an_invocation_are_rejected(
    store: WorkloadStore, tmp_path: Path
) -> None:
    def anonymous(document: dict[str, Any]) -> None:
        document["metadata"]["invocation_id"] = None

    path = _edited_copy(SAMPLE_DBT / "2-run" / "run_results.json", tmp_path / "rr.json", anonymous)
    assert store.ingest_dbt_artifact(path).outcome == ArtifactOutcome.REJECTED


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            lambda d: d["metadata"].update(dbt_schema_version="AKIA_SECRET_VALUE"),
            "not a dbt artifact",
        ),
        (
            lambda d: d["results"][0]["timing"][0].update(started_at="2026-11-01T21:19:00"),
            "does not match run-results v6",
        ),
        (lambda d: d["results"].append(dict(d["results"][0])), "a node appears more than once"),
    ],
)
def test_bad_run_results_are_rejected_with_a_fixed_reason(
    store: WorkloadStore, tmp_path: Path, change: Any, reason: str
) -> None:
    path = _edited_copy(SAMPLE_DBT / "2-run" / "run_results.json", tmp_path / "rr.json", change)
    result = store.ingest_dbt_artifact(path)
    assert (result.outcome, result.reason) == (ArtifactOutcome.REJECTED, reason)
    assert store.model_executions(FIRST_RUN_INVOCATION) == []


def test_deeply_nested_json_is_rejected(store: WorkloadStore, tmp_path: Path) -> None:
    path = tmp_path / "rr.json"
    path.write_bytes(b"[" * 100_000 + b"]" * 100_000)
    assert store.ingest_dbt_artifact(path).reason == "not a dbt artifact"


def test_a_single_run_results_file_raises_no_flag(store: WorkloadStore) -> None:
    store.ingest_dbt_artifact(SAMPLE_DBT / "2-run" / "run_results.json")
    assert all(e.flags == () for e in store.model_executions(FIRST_RUN_INVOCATION))


# dbt manifest: model catalog and dependencies (#47)

SECOND_RUN_INVOCATION = "0d9fc611-0f9f-43ea-81bb-ba7bc63be72f"
SAMPLE_MANIFEST = SAMPLE_DBT / "3-run" / "manifest.json"


def test_the_manifest_records_each_node_and_its_dependencies(store: WorkloadStore) -> None:
    result = store.ingest_dbt_artifact(SAMPLE_MANIFEST)
    assert (result.outcome, result.invocation_id) == (ArtifactOutcome.STORED, SECOND_RUN_INVOCATION)
    nodes = {n.node_id: n for n in store.manifest_nodes(SECOND_RUN_INVOCATION)}
    assert sorted(nodes) == [
        "model.reference_workload.daily_orders",
        "model.reference_workload.revenue_by_region",
        "model.reference_workload.stg_customers",
        "model.reference_workload.stg_orders",
        "seed.reference_workload.raw_customers",
        "seed.reference_workload.raw_orders",
    ]
    daily = nodes["model.reference_workload.daily_orders"]
    assert (daily.resource_type, daily.name, daily.package_name, daily.materialized) == (
        "model",
        "daily_orders",
        "reference_workload",
        "incremental",
    )
    assert daily.relation_name == '"awsdatacatalog"."reference_workload"."daily_orders"'
    assert daily.depends_on == ("model.reference_workload.stg_orders",)
    assert nodes["model.reference_workload.revenue_by_region"].depends_on == (
        "model.reference_workload.stg_customers",
        "model.reference_workload.stg_orders",
    )
    assert nodes["seed.reference_workload.raw_orders"].depends_on == ()


def test_the_hand_written_manifest_has_optional_fields_absent(store: WorkloadStore) -> None:
    store.ingest_dbt_artifact(FIXTURES / "dbt" / "manifest.json")
    nodes = {n.node_id: n for n in store.manifest_nodes("inv-001")}
    customers = nodes["model.demo.customers"]
    assert (customers.relation_name, customers.materialized) == (None, None)
    assert customers.depends_on == ("model.demo.orders",)


def test_no_sql_descriptions_or_macros_are_kept(store: WorkloadStore) -> None:
    store.ingest_dbt_artifact(SAMPLE_MANIFEST)
    nodes = store.manifest_nodes(SECOND_RUN_INVOCATION)
    text = json.dumps([n.model_dump(mode="json") for n in nodes]).lower()
    assert "select" not in text and "macro." not in text and "description" not in text


def test_reingesting_a_manifest_is_a_duplicate_and_a_change_is_flagged(
    store: WorkloadStore, tmp_path: Path
) -> None:
    store.ingest_dbt_artifact(SAMPLE_MANIFEST)
    assert store.ingest_dbt_artifact(SAMPLE_MANIFEST).outcome == ArtifactOutcome.DUPLICATE

    def renamed(document: dict[str, Any]) -> None:
        document["nodes"]["model.reference_workload.daily_orders"]["name"] = "daily_orders_v2"

    changed = _edited_copy(SAMPLE_MANIFEST, tmp_path / "manifest.json", renamed)
    assert store.ingest_dbt_artifact(changed).outcome == ArtifactOutcome.CHANGED_COPY
    nodes = store.manifest_nodes(SECOND_RUN_INVOCATION)
    assert len(nodes) == 12
    assert all(DbtObservationFlag.CONFLICTING_OBSERVATIONS in n.flags for n in nodes)


def test_a_manifest_and_run_results_of_one_invocation_are_separate_artifacts(
    store: WorkloadStore,
) -> None:
    store.ingest_dbt_artifact(SAMPLE_DBT / "3-run" / "run_results.json")
    assert store.ingest_dbt_artifact(SAMPLE_MANIFEST).outcome == ArtifactOutcome.STORED
    assert all(e.flags == () for e in store.model_executions(SECOND_RUN_INVOCATION))


def test_other_manifest_versions_are_rejected(store: WorkloadStore, tmp_path: Path) -> None:
    def older(document: dict[str, Any]) -> None:
        document["metadata"]["dbt_schema_version"] = (
            "https://schemas.getdbt.com/dbt/manifest/v11.json"
        )

    path = _edited_copy(SAMPLE_MANIFEST, tmp_path / "manifest.json", older)
    result = store.ingest_dbt_artifact(path)
    assert (result.outcome, result.reason) == (ArtifactOutcome.REJECTED, "unsupported manifest v11")
    assert store.manifest_nodes(SECOND_RUN_INVOCATION) == []
