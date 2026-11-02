"""Identity Links through the workload store's public interface (M3 seam, #60)."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from data_finops.contracts.workload import IdentityLink, LinkOutcome, LinkResolution
from data_finops.workload.store import WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
EVENTS = FIXTURES / "openlineage" / "events.json"
RUN_RESULTS = FIXTURES / "dbt" / "run_results.json"
SAMPLE = FIXTURES / "reference-workload"
DBT_RUN = "00000000-0000-4000-8000-000000000002"
MODEL_RUN = "00000000-0000-4000-8000-000000000003"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WorkloadStore]:
    with WorkloadStore(tmp_path / "data-finops.sqlite") as opened:
        yield opened


def test_a_dbt_ol_model_run_links_to_the_model_execution_it_reports(
    store: WorkloadStore,
) -> None:
    store.ingest_events(EVENTS)
    store.ingest_dbt_artifact(RUN_RESULTS)
    resolution = store.model_execution_of_run(MODEL_RUN)
    assert resolution is not None and resolution.outcome == LinkOutcome.LINKED
    link = resolution.link
    assert link is not None
    assert (link.source_type, link.source_id) == ("workload_run", MODEL_RUN)
    assert (link.target_type, link.target_id) == (
        "model_execution",
        '["inv-001","model.demo.orders"]',
    )
    assert link.evidence_type == "dbt_ol_facets"
    assert json.loads(link.evidence_value) == {
        "dbt_node.unique_id": "model.demo.orders",
        "dbt_run.invocation_id": "inv-001",
    }
    assert link.policy_version == "identity-links/1"


def _write_events(path: Path, events: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def _model_event(event_type: str, at: str, node: str, invocation: str) -> dict[str, Any]:
    facet = {"_producer": "https://example.invalid/p", "_schemaURL": "https://example.invalid/s"}
    return {
        "eventType": event_type,
        "eventTime": at,
        "producer": "https://example.invalid/producer",
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
        "job": {
            "namespace": "dbt",
            "name": node,
            "facets": {"dbt_node": facet | {"unique_id": node}},
        },
        "run": {
            "runId": MODEL_RUN,
            "facets": {"dbt_run": facet | {"invocation_id": invocation}},
        },
    }


def test_every_reference_workload_model_run_links_to_its_model_execution(
    store: WorkloadStore,
) -> None:
    events = SAMPLE / "openlineage" / "dbt-events.jsonl"
    store.ingest_events(events)
    for step in ("1-seed", "2-run", "3-run"):
        store.ingest_dbt_artifact(SAMPLE / "dbt" / step / "run_results.json")
    model_runs = {
        e["run"]["runId"]
        for e in map(json.loads, events.read_text(encoding="utf-8").splitlines())
        if "dbt_node" in e["job"].get("facets", {})
    }
    targets = set()
    for run_id in model_runs:
        resolution = store.model_execution_of_run(run_id)
        assert resolution is not None and resolution.outcome == LinkOutcome.LINKED
        assert resolution.link is not None
        targets.add(resolution.link.target_id)
    assert targets == {
        f'["{invocation}","model.reference_workload.{model}"]'
        for invocation in (
            "d54e1d8c-43cb-42f4-a6ab-a055ab074a79",
            "0d9fc611-0f9f-43ea-81bb-ba7bc63be72f",
        )
        for model in ("stg_customers", "stg_orders", "daily_orders", "revenue_by_region")
    }


def test_a_model_run_whose_model_execution_nobody_reported_is_unmatched(
    store: WorkloadStore,
) -> None:
    store.ingest_events(EVENTS)
    resolution = store.model_execution_of_run(MODEL_RUN)
    assert resolution is not None
    assert (resolution.outcome, resolution.link) == (LinkOutcome.UNMATCHED, None)


def test_a_model_run_whose_observations_name_different_model_executions_is_ambiguous(
    store: WorkloadStore, tmp_path: Path
) -> None:
    store.ingest_dbt_artifact(RUN_RESULTS)
    start = _model_event("START", "2026-10-01T01:00:00Z", "model.demo.orders", "inv-001")
    end = _model_event("COMPLETE", "2026-10-01T01:00:09Z", "model.demo.customers", "inv-001")
    store.ingest_events(_write_events(tmp_path / "e.jsonl", [start, end]))
    resolution = store.model_execution_of_run(MODEL_RUN)
    assert resolution is not None
    assert (resolution.outcome, resolution.link) == (LinkOutcome.AMBIGUOUS, None)


def test_a_run_without_dbt_ol_model_facets_has_no_evidence(store: WorkloadStore) -> None:
    store.ingest_events(EVENTS)
    store.ingest_dbt_artifact(RUN_RESULTS)
    resolution = store.model_execution_of_run(DBT_RUN)
    assert resolution is not None and resolution.outcome == LinkOutcome.NO_EVIDENCE


def test_an_unknown_run_has_no_resolution(store: WorkloadStore) -> None:
    assert store.model_execution_of_run(MODEL_RUN) is None


def test_run_results_arriving_after_the_events_give_the_same_link(tmp_path: Path) -> None:
    answers = []
    for name, order in (("a", (EVENTS, RUN_RESULTS)), ("b", (RUN_RESULTS, EVENTS))):
        with WorkloadStore(tmp_path / f"{name}.sqlite") as store:
            for path in order:
                if path == EVENTS:
                    store.ingest_events(path)
                else:
                    store.ingest_dbt_artifact(path)
            answers.append(store.model_execution_of_run(MODEL_RUN))
    assert answers[0] == answers[1]
    assert answers[0] is not None and answers[0].outcome == LinkOutcome.LINKED


def test_a_model_run_without_an_invocation_has_no_evidence(
    store: WorkloadStore, tmp_path: Path
) -> None:
    store.ingest_dbt_artifact(RUN_RESULTS)
    event = _model_event("COMPLETE", "2026-10-01T01:00:09Z", "model.demo.orders", "inv-001")
    del event["run"]["facets"]["dbt_run"]
    store.ingest_events(_write_events(tmp_path / "e.jsonl", [event]))
    resolution = store.model_execution_of_run(MODEL_RUN)
    assert resolution is not None and resolution.outcome == LinkOutcome.NO_EVIDENCE


def test_the_same_node_reported_in_another_invocation_does_not_match(
    store: WorkloadStore, tmp_path: Path
) -> None:
    store.ingest_dbt_artifact(RUN_RESULTS)
    event = _model_event("COMPLETE", "2026-10-01T01:00:09Z", "model.demo.orders", "inv-999")
    store.ingest_events(_write_events(tmp_path / "e.jsonl", [event]))
    resolution = store.model_execution_of_run(MODEL_RUN)
    assert resolution is not None and resolution.outcome == LinkOutcome.UNMATCHED


def _run_results_copy(tmp_path: Path, status: str, other_status: str) -> Path:
    """A changed copy of the hand-written run results: one node's and another's status set."""
    document = json.loads(RUN_RESULTS.read_text(encoding="utf-8"))
    for result in document["results"]:
        result["status"] = status if result["unique_id"] == "model.demo.orders" else other_status
    path = tmp_path / "run_results.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_run_results_that_disagree_about_the_model_execution_make_it_ambiguous(
    store: WorkloadStore, tmp_path: Path
) -> None:
    store.ingest_events(EVENTS)
    store.ingest_dbt_artifact(RUN_RESULTS)
    store.ingest_dbt_artifact(_run_results_copy(tmp_path, "error", "success"))
    resolution = store.model_execution_of_run(MODEL_RUN)
    assert resolution is not None and resolution.outcome == LinkOutcome.AMBIGUOUS


def test_run_results_that_agree_about_the_model_execution_still_link(
    store: WorkloadStore, tmp_path: Path
) -> None:
    store.ingest_events(EVENTS)
    store.ingest_dbt_artifact(RUN_RESULTS)
    store.ingest_dbt_artifact(_run_results_copy(tmp_path, "success", "error"))
    resolution = store.model_execution_of_run(MODEL_RUN)
    assert resolution is not None and resolution.outcome == LinkOutcome.LINKED


@pytest.mark.parametrize(
    ("outcome", "with_link"),
    [(LinkOutcome.LINKED, False), (LinkOutcome.UNMATCHED, True)],
)
def test_a_link_is_present_exactly_when_the_outcome_is_linked(
    outcome: LinkOutcome, with_link: bool
) -> None:
    link = IdentityLink(
        source_type="workload_run",
        source_id=MODEL_RUN,
        target_type="model_execution",
        target_id='["inv-001","model.demo.orders"]',
        evidence_type="dbt_ol_facets",
        evidence_value="{}",
        policy_version="identity-links/1",
    )
    with pytest.raises(ValidationError):
        LinkResolution(outcome=outcome, link=link if with_link else None)


QUERIES = FIXTURES / "athena" / "query-executions.json"
OUTCOMES = FIXTURES / "athena" / "link-outcomes.json"
RESTATED = FIXTURES / "athena" / "link-outcomes-restated.json"
ACCOUNT, REGION = "123456789012", "us-east-1"


def _query_store(store: WorkloadStore, *paths: Path) -> WorkloadStore:
    for path in paths:
        if path.parent.name == "athena":
            store.ingest_query_history(path, account_id=ACCOUNT, region=REGION)
        else:
            store.ingest_dbt_artifact(path)
    return store


def _outcome(store: WorkloadStore, query_id: str) -> LinkOutcome | None:
    resolution = store.model_execution_of_query(ACCOUNT, REGION, query_id)
    return resolution.outcome if resolution else None


def test_a_query_links_to_the_model_execution_its_trusted_query_comment_names(
    store: WorkloadStore,
) -> None:
    _query_store(store, QUERIES, RUN_RESULTS)
    resolution = store.model_execution_of_query(ACCOUNT, REGION, "q-001")
    assert resolution is not None and resolution.link is not None
    link = resolution.link
    assert (link.source_type, link.source_id) == (
        "query_execution",
        f'["{ACCOUNT}","{REGION}","q-001"]',
    )
    assert (link.target_type, link.target_id) == (
        "model_execution",
        '["inv-001","model.demo.orders"]',
    )
    assert link.evidence_type == "trusted_query_comment"
    assert json.loads(link.evidence_value) == {
        "invocation_id": "inv-001",
        "node_id": "model.demo.orders",
    }


def test_query_outcomes_on_the_hand_written_fixtures(store: WorkloadStore) -> None:
    _query_store(store, QUERIES, OUTCOMES, RESTATED, RUN_RESULTS)
    assert {q: _outcome(store, q) for q in ("q-001", "q-002", "q-003", "q-004", "q-x")} == {
        "q-001": LinkOutcome.LINKED,
        "q-002": LinkOutcome.LINKED,
        "q-003": LinkOutcome.UNMATCHED,
        "q-004": LinkOutcome.AMBIGUOUS,
        "q-x": None,
    }


def test_the_query_execution_carries_only_the_comment_identifiers(store: WorkloadStore) -> None:
    _query_store(store, QUERIES)
    query = store.query_execution(ACCOUNT, REGION, "q-001")
    assert query is not None and query.correlation is not None
    assert (query.correlation.invocation_id, query.correlation.node_id) == (
        "inv-001",
        "model.demo.orders",
    )
    (stored,) = store.stored_query_executions(ACCOUNT, REGION, "q-001")
    assert "dbt_version" not in stored and "raw_orders" not in stored


def test_the_query_executions_of_a_model_execution(store: WorkloadStore) -> None:
    _query_store(store, QUERIES, RUN_RESULTS)
    queries = store.query_executions_of_model_execution("inv-001", "model.demo.orders")
    assert [q.query_execution_id for q in queries] == ["q-001", "q-002"]


def test_links_do_not_depend_on_arrival_order(tmp_path: Path) -> None:
    answers = []
    files = (QUERIES, OUTCOMES, RESTATED, RUN_RESULTS)
    for name, order in (("a", files), ("b", files[::-1])):
        with WorkloadStore(tmp_path / f"{name}.sqlite") as store:
            _query_store(store, *order)
            ids = ("q-001", "q-002", "q-003", "q-004")
            answers.append(
                (
                    [store.model_execution_of_query(ACCOUNT, REGION, q) for q in ids],
                    store.query_executions_of_model_execution("inv-001", "model.demo.orders"),
                )
            )
    assert answers[0] == answers[1]


def _history(tmp_path: Path, *queries: str) -> Path:
    entries = [
        {
            "QueryExecutionId": f"q-{index}",
            "Query": text,
            "Status": {"State": "SUCCEEDED", "SubmissionDateTime": "2026-10-01T01:00:00Z"},
        }
        for index, text in enumerate(queries)
    ]
    path = tmp_path / "history.json"
    path.write_text(json.dumps({"QueryExecutions": entries}), encoding="utf-8")
    return path


COMMENT = '{"app": "dbt", "invocation_id": "inv-001", "node_id": "model.demo.orders"}'


@pytest.mark.parametrize(
    ("text", "outcome"),
    [
        (f"-- /* {COMMENT} */\ncreate table t as select 1", LinkOutcome.LINKED),
        (f"/* {COMMENT} */\nselect 1", LinkOutcome.LINKED),
        (f"/* {COMMENT} */ select 1", LinkOutcome.LINKED),
        (f"select 1 /* {COMMENT} */", LinkOutcome.NO_EVIDENCE),
        (f"select 1\n-- /* {COMMENT} */", LinkOutcome.NO_EVIDENCE),
        (f"  /* {COMMENT} */\nselect 1", LinkOutcome.NO_EVIDENCE),
        (f"{COMMENT} */\nselect 1", LinkOutcome.NO_EVIDENCE),
        (f"/* {COMMENT.replace(', ', ',\n')} */\nselect 1", LinkOutcome.NO_EVIDENCE),
        (f"/* {COMMENT.replace('dbt', 'other')} */\nselect 1", LinkOutcome.NO_EVIDENCE),
        ('/* {"app": "dbt", "node_id": "model.demo.orders"} */\nselect 1', LinkOutcome.NO_EVIDENCE),
        (
            '/* {"app": "dbt", "invocation_id": "", "node_id": "model.demo.orders"} */\nselect 1',
            LinkOutcome.NO_EVIDENCE,
        ),
        (
            '/* {"app": "dbt", "invocation_id": 1, "node_id": "n"} */\nselect 1',
            LinkOutcome.NO_EVIDENCE,
        ),
        (f"/* {COMMENT}\nselect 1 */", LinkOutcome.NO_EVIDENCE),
        ("/* [1, 2] */\nselect 1", LinkOutcome.NO_EVIDENCE),
        ("/* {not json} */\nselect 1", LinkOutcome.NO_EVIDENCE),
        ("alter table t add columns (c int)", LinkOutcome.NO_EVIDENCE),
        (
            '/* {"app": "dbt", "invocation_id": "inv-001",'
            ' "node_id": "model.demo.orders */ select 1 /* "} */\nselect 1',
            LinkOutcome.NO_EVIDENCE,
        ),
        (
            '/* {"app": "other", "app": "dbt", "invocation_id": "inv-001",'
            ' "node_id": "model.demo.orders"} */\nselect 1',
            LinkOutcome.NO_EVIDENCE,
        ),
        (
            '/* {"app": "dbt", "invocation_id": " ", "node_id": "n"} */\nselect 1',
            LinkOutcome.NO_EVIDENCE,
        ),
        (f"/* {COMMENT} */ select '{'x' * 10_000}'", LinkOutcome.LINKED),
        (f'/* {COMMENT[:-1]}, "pad": "{"x" * 5_000}"}} */\nselect 1', LinkOutcome.NO_EVIDENCE),
    ],
)
def test_only_a_trusted_query_comment_is_evidence(
    store: WorkloadStore, tmp_path: Path, text: str, outcome: LinkOutcome
) -> None:
    store.ingest_dbt_artifact(RUN_RESULTS)
    store.ingest_query_history(_history(tmp_path, text), account_id=ACCOUNT, region=REGION)
    assert _outcome(store, "q-0") == outcome


def test_a_query_reported_once_with_and_once_without_a_comment_is_ambiguous(
    store: WorkloadStore, tmp_path: Path
) -> None:
    store.ingest_dbt_artifact(RUN_RESULTS)
    for name, text in (("a", f"/* {COMMENT} */\nselect 1"), ("b", "select 1")):
        path = _history(tmp_path, text).rename(tmp_path / f"{name}.json")
        store.ingest_query_history(path, account_id=ACCOUNT, region=REGION)
    assert _outcome(store, "q-0") == LinkOutcome.AMBIGUOUS


def test_every_reference_workload_query_has_its_observed_outcome(store: WorkloadStore) -> None:
    history = SAMPLE / "athena" / "query-executions.json"
    store.ingest_query_history(history, account_id="111122223333", region=REGION)
    for step in ("1-seed", "2-run", "3-run"):
        store.ingest_dbt_artifact(SAMPLE / "dbt" / step / "run_results.json")
    ids = [q["QueryExecutionId"] for q in json.loads(history.read_text())["QueryExecutions"]]
    outcomes = [
        resolution.outcome
        for resolution in (
            store.model_execution_of_query("111122223333", REGION, query_id) for query_id in ids
        )
        if resolution
    ]
    assert len(outcomes) == 20
    assert outcomes.count(LinkOutcome.LINKED) == 17
    assert outcomes.count(LinkOutcome.NO_EVIDENCE) == 3
    daily = store.query_executions_of_model_execution(
        "0d9fc611-0f9f-43ea-81bb-ba7bc63be72f", "model.reference_workload.daily_orders"
    )
    assert len(daily) == 3


AIRFLOW_TASK = "00000000-0000-4000-8000-000000000001"
SEED_INVOCATION = "ba26dcbd-17ed-4270-82ad-8d8dd8075b81"
RUN_INVOCATIONS = ("d54e1d8c-43cb-42f4-a6ab-a055ab074a79", "0d9fc611-0f9f-43ea-81bb-ba7bc63be72f")


def test_a_dbt_invocation_links_to_the_workload_run_that_reported_it(
    store: WorkloadStore,
) -> None:
    store.ingest_events(EVENTS)
    store.ingest_dbt_artifact(RUN_RESULTS)
    resolution = store.workload_run_of_invocation("inv-001")
    assert resolution is not None and resolution.link is not None
    link = resolution.link
    assert (link.source_type, link.source_id) == ("dbt_invocation", "inv-001")
    assert (link.target_type, link.target_id) == ("workload_run", DBT_RUN)
    assert link.evidence_type == "dbt_run_facet"
    assert json.loads(link.evidence_value) == {"dbt_run.invocation_id": "inv-001"}


def test_the_invocation_s_workload_run_leads_to_its_airflow_run(store: WorkloadStore) -> None:
    store.ingest_events(EVENTS)
    store.ingest_dbt_artifact(RUN_RESULTS)
    resolution = store.workload_run_of_invocation("inv-001")
    assert resolution is not None and resolution.link is not None
    dbt_run = store.workload_run(resolution.link.target_id)
    assert dbt_run is not None and dbt_run.parent_run_id == AIRFLOW_TASK
    airflow = store.workload_run(AIRFLOW_TASK)
    assert airflow is not None
    assert (airflow.job_namespace, airflow.job_name) == ("demo-airflow", "daily_orders.dbt_run")


def _reference_store(store: WorkloadStore) -> WorkloadStore:
    store.ingest_events(SAMPLE / "openlineage" / "dbt-events.jsonl")
    for step in ("1-seed", "2-run", "3-run"):
        store.ingest_dbt_artifact(SAMPLE / "dbt" / step / "run_results.json")
    return store


def test_reference_workload_invocations_link_to_their_dbt_runs(store: WorkloadStore) -> None:
    _reference_store(store)
    for invocation in RUN_INVOCATIONS:
        resolution = store.workload_run_of_invocation(invocation)
        assert resolution is not None and resolution.link is not None
        run = store.workload_run(resolution.link.target_id)
        assert run is not None and run.job_name == "dbt-run-reference_workload"


def test_an_invocation_no_workload_run_reported_is_unmatched(store: WorkloadStore) -> None:
    _reference_store(store)
    resolution = store.workload_run_of_invocation(SEED_INVOCATION)
    assert resolution is not None
    assert (resolution.outcome, resolution.link) == (LinkOutcome.UNMATCHED, None)


def test_model_runs_are_not_mistaken_for_the_invocation_s_run(store: WorkloadStore) -> None:
    store.ingest_events(SAMPLE / "openlineage" / "dbt-events.jsonl")
    resolution = store.workload_run_of_invocation(RUN_INVOCATIONS[0])
    assert resolution is not None and resolution.outcome == LinkOutcome.LINKED


def _invocation_event(run_id: str, invocation: str) -> dict[str, Any]:
    facet = {"_producer": "https://example.invalid/p", "_schemaURL": "https://example.invalid/s"}
    return {
        "eventType": "START",
        "eventTime": "2026-10-01T01:00:00Z",
        "producer": "https://example.invalid/producer",
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
        "job": {"namespace": "dbt", "name": "dbt-run-demo"},
        "run": {"runId": run_id, "facets": {"dbt_run": facet | {"invocation_id": invocation}}},
    }


def test_an_invocation_reported_by_several_workload_runs_is_ambiguous(
    store: WorkloadStore, tmp_path: Path
) -> None:
    store.ingest_dbt_artifact(RUN_RESULTS)
    other = "00000000-0000-4000-8000-0000000000aa"
    events = [_invocation_event(DBT_RUN, "inv-001"), _invocation_event(other, "inv-001")]
    store.ingest_events(_write_events(tmp_path / "e.jsonl", events))
    resolution = store.workload_run_of_invocation("inv-001")
    assert resolution is not None
    assert (resolution.outcome, resolution.link) == (LinkOutcome.AMBIGUOUS, None)


def test_an_invocation_known_only_from_events_still_links(
    store: WorkloadStore, tmp_path: Path
) -> None:
    store.ingest_events(_write_events(tmp_path / "e.jsonl", [_invocation_event(DBT_RUN, "inv-9")]))
    resolution = store.workload_run_of_invocation("inv-9")
    assert resolution is not None and resolution.outcome == LinkOutcome.LINKED


def test_an_unknown_invocation_has_no_resolution(store: WorkloadStore) -> None:
    store.ingest_events(EVENTS)
    assert store.workload_run_of_invocation("inv-unknown") is None


def test_invocation_links_do_not_depend_on_arrival_order(tmp_path: Path) -> None:
    answers = []
    for name, events_first in (("a", True), ("b", False)):
        with WorkloadStore(tmp_path / f"{name}.sqlite") as store:
            if events_first:
                store.ingest_events(EVENTS)
            store.ingest_dbt_artifact(RUN_RESULTS)
            if not events_first:
                store.ingest_events(EVENTS)
            answers.append(store.workload_run_of_invocation("inv-001"))
    assert answers[0] == answers[1]


def test_an_invocation_known_only_from_its_model_runs_is_unmatched(
    store: WorkloadStore, tmp_path: Path
) -> None:
    event = _model_event("COMPLETE", "2026-10-01T01:00:09Z", "model.demo.orders", "inv-7")
    store.ingest_events(_write_events(tmp_path / "e.jsonl", [event]))
    resolution = store.workload_run_of_invocation("inv-7")
    assert resolution is not None and resolution.outcome == LinkOutcome.UNMATCHED


def test_a_model_run_whose_facets_arrive_on_different_events_still_links(
    store: WorkloadStore, tmp_path: Path
) -> None:
    store.ingest_dbt_artifact(RUN_RESULTS)
    start = _model_event("START", "2026-10-01T01:00:00Z", "model.demo.orders", "inv-001")
    del start["job"]["facets"]["dbt_node"]
    end = _model_event("COMPLETE", "2026-10-01T01:00:09Z", "model.demo.orders", "inv-001")
    del end["run"]["facets"]["dbt_run"]
    store.ingest_events(_write_events(tmp_path / "e.jsonl", [start, end]))
    resolution = store.model_execution_of_run(MODEL_RUN)
    assert resolution is not None and resolution.outcome == LinkOutcome.LINKED
    assert store.workload_run_of_invocation("inv-001") == LinkResolution(
        outcome=LinkOutcome.UNMATCHED
    )


def test_a_blank_dbt_node_is_not_evidence(store: WorkloadStore, tmp_path: Path) -> None:
    store.ingest_dbt_artifact(RUN_RESULTS)
    event = _model_event("COMPLETE", "2026-10-01T01:00:09Z", " ", "inv-001")
    store.ingest_events(_write_events(tmp_path / "e.jsonl", [event]))
    resolution = store.model_execution_of_run(MODEL_RUN)
    assert resolution is not None and resolution.outcome == LinkOutcome.NO_EVIDENCE


def test_a_database_file_from_before_the_m3_columns_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE workload_lineage_event (run_id TEXT, event_type_key TEXT,"
        " event_type TEXT, event_time_us INTEGER, digest TEXT, job_namespace TEXT,"
        " job_name TEXT, parent_run_id TEXT, parent_job_namespace TEXT, parent_job_name TEXT,"
        " root_run_id TEXT, root_job_namespace TEXT, root_job_name TEXT, event_json TEXT)"
    )
    connection.close()
    with pytest.raises(RuntimeError, match="dbt_invocation_id, dbt_node_id"):
        WorkloadStore(path)
