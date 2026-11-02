"""M2 exit criterion: expected fixture lineage and statuses; invalid payload rejected (#48).

Every fixture goes into one store through its public interface: the hand-written fixtures,
the anonymized reference workload sample (#24, #32, #35) and the invalid-payload fixtures.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from data_finops.contracts.workload import LineageDirection, WorkloadRunStatus
from data_finops.lineage.validation import RejectionKind
from data_finops.workload.store import ArtifactOutcome, IngestResult, WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE = FIXTURES / "reference-workload"
SEED_RUN = "01a198fa-b471-7a56-9f93-3da0f663f33c"
FIRST_RUN = "01a198fb-0aa1-7313-95ae-5cf9875ce647"
SECOND_RUN = "01a198fb-5bd3-79d5-8157-42a7993bce14"
INVOCATIONS = {
    "1-seed": "ba26dcbd-17ed-4270-82ad-8d8dd8075b81",
    "2-run": "d54e1d8c-43cb-42f4-a6ab-a055ab074a79",
    "3-run": "0d9fc611-0f9f-43ea-81bb-ba7bc63be72f",
}
MODELS = ["daily_orders", "revenue_by_region", "stg_customers", "stg_orders"]


@pytest.fixture(scope="module")
def loaded(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[WorkloadStore, dict[str, IngestResult]]]:
    with WorkloadStore(tmp_path_factory.mktemp("m2") / "data-finops.sqlite") as store:
        events = {
            name: store.ingest_events(path)
            for name, path in [
                ("hand", FIXTURES / "openlineage" / "events.json"),
                ("sample", SAMPLE / "openlineage" / "dbt-events.jsonl"),
                ("invalid-lines", FIXTURES / "openlineage" / "invalid" / "mixed.jsonl"),
                ("invalid-array", FIXTURES / "openlineage" / "invalid" / "array.json"),
            ]
        }
        artifacts = [
            FIXTURES / "dbt" / "run_results.json",
            FIXTURES / "dbt" / "manifest.json",
            *(SAMPLE / "dbt" / step / "run_results.json" for step in INVOCATIONS),
            SAMPLE / "dbt" / "3-run" / "manifest.json",
        ]
        for path in artifacts:
            assert store.ingest_dbt_artifact(path).outcome == ArtifactOutcome.STORED, path
        yield store, events


def test_every_valid_event_is_stored_and_every_invalid_one_rejected(
    loaded: tuple[WorkloadStore, dict[str, IngestResult]],
) -> None:
    _, events = loaded
    assert (events["hand"].accepted, events["sample"].accepted) == (5, 22)
    assert events["hand"].rejected == events["sample"].rejected == ()
    assert events["invalid-lines"].counts() == {
        RejectionKind.NOT_JSON: 1,
        RejectionKind.INVALID_RUN_EVENT: 2,
        RejectionKind.INVALID_PARENT_FACET: 1,
        RejectionKind.UNSUPPORTED_JOB_EVENT: 1,
        RejectionKind.UNSUPPORTED_DATASET_EVENT: 1,
    }
    assert events["invalid-array"].counts() == {RejectionKind.INVALID_RUN_EVENT: 2}


def test_the_hand_written_chain_walks_from_the_dag_to_the_model(
    loaded: tuple[WorkloadStore, dict[str, IngestResult]],
) -> None:
    store, _ = loaded
    dag = "00000000-0000-4000-8000-000000000000"
    (task,) = store.children(dag)
    (dbt_run,) = store.children(task.run_id)
    (model,) = store.children(dbt_run.run_id)
    assert [run.status for run in (task, dbt_run, model)] == [WorkloadRunStatus.COMPLETED] * 3
    assert (dbt_run.root_run_id, model.root_run_id) == (dag, dag)
    assert {e.node_id: e.status for e in store.model_executions("inv-001")} == {
        "model.demo.customers": "success",
        "model.demo.orders": "success",
    }


def test_the_sample_dbt_runs_and_their_model_runs(
    loaded: tuple[WorkloadStore, dict[str, IngestResult]],
) -> None:
    store, _ = loaded
    for run_id in (SEED_RUN, FIRST_RUN, SECOND_RUN):
        run = store.workload_run(run_id)
        assert run is not None
        assert (run.job_name, run.status, run.flags) == (
            "dbt-run-reference_workload",
            WorkloadRunStatus.COMPLETED,
            (),
        )
    assert store.children(SEED_RUN) == []  # dbt-ol seed emits no per-seed runs
    for run_id in (FIRST_RUN, SECOND_RUN):
        children = store.children(run_id)
        assert sorted(child.job_name.rsplit(".", 1)[1] for child in children) == MODELS
        assert all(
            c.status == WorkloadRunStatus.COMPLETED and c.root_run_id == run_id for c in children
        )


def test_the_sample_model_executions_and_manifest(
    loaded: tuple[WorkloadStore, dict[str, IngestResult]],
) -> None:
    store, _ = loaded
    counts = {step: len(store.model_executions(inv)) for step, inv in INVOCATIONS.items()}
    assert counts == {"1-seed": 2, "2-run": 4, "3-run": 4}
    assert all(
        e.status == "success" for inv in INVOCATIONS.values() for e in store.model_executions(inv)
    )
    nodes = {n.node_id: n for n in store.manifest_nodes(INVOCATIONS["3-run"])}
    assert len(nodes) == 6
    assert nodes["model.reference_workload.daily_orders"].materialized == "incremental"


def test_the_sample_lineage_of_daily_orders(
    loaded: tuple[WorkloadStore, dict[str, IngestResult]],
) -> None:
    store, _ = loaded
    daily = next(c for c in store.children(SECOND_RUN) if c.job_name.endswith(".daily_orders"))
    edges = {(e.direction, e.asset.name) for e in store.lineage_edges(daily.run_id)}
    assert edges == {
        (LineageDirection.INPUT, "awsdatacatalog.reference_workload.stg_orders"),
        (LineageDirection.OUTPUT, "awsdatacatalog.reference_workload.daily_orders"),
    }
