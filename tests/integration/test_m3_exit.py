"""M3 exit criterion: exact links match; missing or ambiguous Evidence makes no link (#65).

One store ingests every fixture through its public interface: the hand-written fixtures
(Airflow parent, `inv-001`, the link-outcome queries) and the anonymized reference workload
sample (#24, #35). Identity Links are derived when asked (spec 03 step D, ADR 0022).
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from data_finops.contracts.workload import LinkOutcome, LinkResolution
from data_finops.workload.store import WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE = FIXTURES / "reference-workload"
HAND_ACCOUNT, SAMPLE_ACCOUNT, REGION = "123456789012", "111122223333", "us-east-1"
DAG_RUN = "00000000-0000-4000-8000-000000000000"
AIRFLOW_TASK = "00000000-0000-4000-8000-000000000001"
DBT_RUN = "00000000-0000-4000-8000-000000000002"
MODEL_RUN = "00000000-0000-4000-8000-000000000003"
SEED_INVOCATION, FIRST_INVOCATION, SECOND_INVOCATION = (
    "ba26dcbd-17ed-4270-82ad-8d8dd8075b81",
    "d54e1d8c-43cb-42f4-a6ab-a055ab074a79",
    "0d9fc611-0f9f-43ea-81bb-ba7bc63be72f",
)
SAMPLE_QUERIES = SAMPLE / "athena" / "query-executions.json"


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[WorkloadStore]:
    with WorkloadStore(tmp_path_factory.mktemp("m3") / "data-finops.sqlite") as opened:
        opened.ingest_events(FIXTURES / "openlineage" / "events.json")
        opened.ingest_events(SAMPLE / "openlineage" / "dbt-events.jsonl")
        for path in (
            FIXTURES / "dbt" / "run_results.json",
            *(SAMPLE / "dbt" / step / "run_results.json" for step in ("1-seed", "2-run", "3-run")),
        ):
            opened.ingest_dbt_artifact(path)
        for name in ("query-executions", "link-outcomes", "link-outcomes-restated"):
            opened.ingest_query_history(
                FIXTURES / "athena" / f"{name}.json", account_id=HAND_ACCOUNT, region=REGION
            )
        opened.ingest_query_history(SAMPLE_QUERIES, account_id=SAMPLE_ACCOUNT, region=REGION)
        yield opened


def _linked(resolution: LinkResolution | None) -> str:
    """The target of a `linked` resolution; fails the test for any other outcome."""
    assert resolution is not None and resolution.outcome == LinkOutcome.LINKED
    assert resolution.link is not None
    return resolution.link.target_id


def _sample_query_ids() -> list[str]:
    history = json.loads(SAMPLE_QUERIES.read_text(encoding="utf-8"))
    return [entry["QueryExecutionId"] for entry in history["QueryExecutions"]]


def test_reference_queries_link_exactly_and_uncommented_ones_stay_unlinked(
    store: WorkloadStore,
) -> None:
    outcomes = {}
    for query_id in _sample_query_ids():
        resolution = store.model_execution_of_query(SAMPLE_ACCOUNT, REGION, query_id)
        assert resolution is not None
        outcomes[query_id] = resolution.outcome
    assert sorted(outcomes.values()) == sorted(
        [LinkOutcome.LINKED] * 17 + [LinkOutcome.NO_EVIDENCE] * 3
    )
    unlinked = [
        store.query_execution(SAMPLE_ACCOUNT, REGION, q)
        for q, outcome in outcomes.items()
        if outcome == LinkOutcome.NO_EVIDENCE
    ]
    assert None not in unlinked
    # OQ-22: DDL scanning nothing, which the fixture README identifies as the `alter table`
    # statements dbt-athena sends without a comment (query text is never stored).
    assert {(q.statement_type, q.scanned_bytes) for q in unlinked if q} == {("DDL", 0)}


def test_every_linked_reference_query_rolls_up_to_exactly_one_model_execution(
    store: WorkloadStore,
) -> None:
    per_execution = {
        (invocation, execution.node_id): [
            q.query_execution_id
            for q in store.query_executions_of_model_execution(invocation, execution.node_id)
        ]
        for invocation in (SEED_INVOCATION, FIRST_INVOCATION, SECOND_INVOCATION)
        for execution in store.model_executions(invocation)
    }
    linked = [q for queries in per_execution.values() for q in queries]
    assert len(linked) == len(set(linked)) == 17
    assert len(per_execution[(SECOND_INVOCATION, "model.reference_workload.daily_orders")]) == 3


def test_reference_invocations_and_model_runs_link_to_their_workload_records(
    store: WorkloadStore,
) -> None:
    seed = store.workload_run_of_invocation(SEED_INVOCATION)
    assert seed is not None and (seed.outcome, seed.link) == (LinkOutcome.UNMATCHED, None)
    for invocation in (FIRST_INVOCATION, SECOND_INVOCATION):
        dbt_run = store.workload_run(_linked(store.workload_run_of_invocation(invocation)))
        assert dbt_run is not None and dbt_run.job_name == "dbt-run-reference_workload"
        model_runs = store.children(dbt_run.run_id)
        assert len(model_runs) == 4
        targets = {_linked(store.model_execution_of_run(run.run_id)) for run in model_runs}
        assert targets == {
            f'["{invocation}","model.reference_workload.{name}"]'
            for name in ("daily_orders", "revenue_by_region", "stg_customers", "stg_orders")
        }


def test_a_hand_written_query_reaches_its_airflow_run(store: WorkloadStore) -> None:
    query = _linked(store.model_execution_of_query(HAND_ACCOUNT, REGION, "q-001"))
    model_run = _linked(store.model_execution_of_run(MODEL_RUN))
    # The query and dbt-ol's model run name the same Model Execution: one unit of work.
    assert query == model_run == '["inv-001","model.demo.orders"]'
    dbt_run = store.workload_run(_linked(store.workload_run_of_invocation("inv-001")))
    assert dbt_run is not None
    assert (dbt_run.run_id, dbt_run.parent_run_id, dbt_run.root_run_id) == (
        DBT_RUN,
        AIRFLOW_TASK,
        DAG_RUN,
    )
    airflow = store.workload_run(AIRFLOW_TASK)
    assert airflow is not None and airflow.job_name == "daily_orders.dbt_run"
    # The fixture has no events for the DAG run itself; the ParentRunFacet names it as root.
    assert (dbt_run.root_job_namespace, dbt_run.root_job_name) == ("demo-airflow", "daily_orders")
    assert store.workload_run(DAG_RUN) is None


def test_missing_or_ambiguous_evidence_makes_no_link(store: WorkloadStore) -> None:
    expected = {"q-003": LinkOutcome.UNMATCHED, "q-004": LinkOutcome.AMBIGUOUS}
    for query_id, outcome in expected.items():
        resolution = store.model_execution_of_query(HAND_ACCOUNT, REGION, query_id)
        assert resolution is not None and (resolution.outcome, resolution.link) == (outcome, None)
    # q-004 once named model.demo.orders, but an ambiguous query never rolls up to it.
    orders = store.query_executions_of_model_execution("inv-001", "model.demo.orders")
    assert [q.query_execution_id for q in orders] == ["q-001", "q-002"]
