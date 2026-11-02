"""Identity Links through the workload store's public interface (M3 seam, #60)."""

import json
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
ACCOUNT, REGION = "123456789012", "us-east-1"


def _query_store(store: WorkloadStore, *paths: Path) -> WorkloadStore:
    for path in paths:
        if path == QUERIES:
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
    _query_store(store, QUERIES, RUN_RESULTS)
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
    for name, order in (("a", (QUERIES, RUN_RESULTS)), ("b", (RUN_RESULTS, QUERIES))):
        with WorkloadStore(tmp_path / f"{name}.sqlite") as store:
            _query_store(store, *order)
            answers.append(
                [store.model_execution_of_query(ACCOUNT, REGION, q) for q in ("q-001", "q-003")]
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
