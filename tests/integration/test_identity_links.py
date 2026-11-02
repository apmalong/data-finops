"""Identity Links through the workload store's public interface (M3 seam, #60)."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from data_finops.contracts.workload import LinkOutcome
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
        assert resolution is not None and resolution.link is not None
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
