"""M2 exit criterion: expected fixture lineage and statuses; invalid payload rejected (#48).

Every fixture goes into one store through its public interface: the hand-written fixtures,
the anonymized reference workload sample (#24, #32, #35) and the invalid payloads, including
an oversized event and a manifest of another version generated for this run.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from data_finops.contracts.workload import LineageDirection, WorkloadRun, WorkloadRunStatus
from data_finops.lineage.validation import RejectionKind
from data_finops.workload.store import ArtifactOutcome, IngestResult, WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE = FIXTURES / "reference-workload"
DAG_RUN = "00000000-0000-4000-8000-000000000000"
SEED_RUN = "01a198fa-b471-7a56-9f93-3da0f663f33c"
FIRST_RUN = "01a198fb-0aa1-7313-95ae-5cf9875ce647"
SECOND_RUN = "01a198fb-5bd3-79d5-8157-42a7993bce14"
FAILED_INVOCATION = "00000000-0000-4000-8000-0000000000f1"
INVOCATIONS = {
    "1-seed": "ba26dcbd-17ed-4270-82ad-8d8dd8075b81",
    "2-run": "d54e1d8c-43cb-42f4-a6ab-a055ab074a79",
    "3-run": "0d9fc611-0f9f-43ea-81bb-ba7bc63be72f",
}
MODELS = ["daily_orders", "revenue_by_region", "stg_customers", "stg_orders"]


def _model_name(run: WorkloadRun) -> str:
    """dbt-ol names a model run `<database>.<schema>.<model>`."""
    return run.job_name.rsplit(".", 1)[1]


def _generated_payloads(folder: Path) -> dict[str, Path]:
    """Invalid inputs too large or too specific to keep as committed fixtures."""
    big = {
        "eventType": "START",
        "eventTime": "2026-11-01T21:19:00Z",
        "producer": "https://example.invalid/producer",
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
        "job": {"namespace": "demo", "name": "big"},
        "run": {"runId": "00000000-0000-4000-8000-0000000000e1", "facets": {}},
        "inputs": [{"namespace": "demo", "name": "x" * 1_048_576}],
    }
    oversized = folder / "oversized.jsonl"
    oversized.write_text(json.dumps(big) + "\n", encoding="utf-8")
    manifest = json.loads((SAMPLE / "dbt" / "3-run" / "manifest.json").read_text(encoding="utf-8"))
    manifest["metadata"]["dbt_schema_version"] = "https://schemas.getdbt.com/dbt/manifest/v11.json"
    old_manifest = folder / "manifest-v11.json"
    old_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    results = json.loads(
        (SAMPLE / "dbt" / "2-run" / "run_results.json").read_text(encoding="utf-8")
    )
    results["metadata"]["invocation_id"] = FAILED_INVOCATION
    results["results"][0]["status"] = "error"
    results["results"][1]["status"] = "skipped"
    failed = folder / "run_results-failed.json"
    failed.write_text(json.dumps(results), encoding="utf-8")
    return {"oversized": oversized, "old-manifest": old_manifest, "failed": failed}


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[WorkloadStore]:
    with WorkloadStore(tmp_path_factory.mktemp("m2") / "data-finops.sqlite") as opened:
        yield opened


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return _generated_payloads(tmp_path_factory.mktemp("payloads"))


@pytest.fixture(scope="module")
def ingested(store: WorkloadStore, generated: dict[str, Path]) -> dict[str, IngestResult]:
    events = {
        name: store.ingest_events(path)
        for name, path in [
            ("hand", FIXTURES / "openlineage" / "events.json"),
            ("sample", SAMPLE / "openlineage" / "dbt-events.jsonl"),
            ("invalid-lines", FIXTURES / "openlineage" / "invalid" / "mixed.jsonl"),
            ("invalid-array", FIXTURES / "openlineage" / "invalid" / "array.json"),
            ("oversized", generated["oversized"]),
        ]
    }
    for path in [
        FIXTURES / "dbt" / "run_results.json",
        FIXTURES / "dbt" / "manifest.json",
        *(SAMPLE / "dbt" / step / "run_results.json" for step in INVOCATIONS),
        SAMPLE / "dbt" / "3-run" / "manifest.json",
        generated["failed"],
    ]:
        assert store.ingest_dbt_artifact(path).outcome == ArtifactOutcome.STORED, path
    return events


def test_every_valid_event_is_stored_and_every_invalid_one_rejected(
    ingested: dict[str, IngestResult],
) -> None:
    assert (ingested["hand"].accepted, ingested["sample"].accepted) == (5, 22)
    assert ingested["hand"].rejected == ingested["sample"].rejected == ()
    lines = ingested["invalid-lines"]
    assert lines.accepted == 3
    assert lines.counts() == {
        RejectionKind.NOT_JSON: 1,
        RejectionKind.INVALID_RUN_EVENT: 2,
        RejectionKind.INVALID_PARENT_FACET: 1,
        RejectionKind.UNSUPPORTED_JOB_EVENT: 1,
        RejectionKind.UNSUPPORTED_DATASET_EVENT: 1,
    }
    assert len(lines.unsupported) == 2
    assert ingested["invalid-array"].counts() == {RejectionKind.INVALID_RUN_EVENT: 2}
    assert ingested["oversized"].counts() == {RejectionKind.TOO_LARGE: 1}


def test_a_manifest_of_another_version_is_rejected(
    store: WorkloadStore, ingested: dict[str, IngestResult], generated: dict[str, Path]
) -> None:
    result = store.ingest_dbt_artifact(generated["old-manifest"])
    assert (result.outcome, result.reason) == (ArtifactOutcome.REJECTED, "unsupported manifest v11")


def test_the_hand_written_chain_walks_from_the_dag_to_the_model(
    store: WorkloadStore, ingested: dict[str, IngestResult]
) -> None:
    (task,) = store.children(DAG_RUN)
    (dbt_run,) = store.children(task.run_id)
    (model,) = store.children(dbt_run.run_id)
    assert [run.status for run in (task, dbt_run, model)] == [WorkloadRunStatus.COMPLETED] * 3
    assert (dbt_run.root_run_id, model.root_run_id) == (DAG_RUN, DAG_RUN)
    assert {e.node_id: e.status for e in store.model_executions("inv-001")} == {
        "model.demo.customers": "success",
        "model.demo.orders": "success",
    }


def test_the_sample_dbt_runs_and_their_model_runs(
    store: WorkloadStore, ingested: dict[str, IngestResult]
) -> None:
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
        assert sorted(_model_name(child) for child in children) == MODELS
        assert all(
            c.status == WorkloadRunStatus.COMPLETED and c.root_run_id == run_id for c in children
        )


def test_the_sample_model_executions_and_manifest(
    store: WorkloadStore, ingested: dict[str, IngestResult]
) -> None:
    executions = {step: store.model_executions(inv) for step, inv in INVOCATIONS.items()}
    assert {step: len(found) for step, found in executions.items()} == {
        "1-seed": 2,
        "2-run": 4,
        "3-run": 4,
    }
    assert all(e.status == "success" for found in executions.values() for e in found)
    nodes = {n.node_id: n for n in store.manifest_nodes(INVOCATIONS["3-run"])}
    assert len(nodes) == 6
    assert nodes["model.reference_workload.daily_orders"].materialized == "incremental"


def test_dbt_statuses_other_than_success_are_kept(
    store: WorkloadStore, ingested: dict[str, IngestResult]
) -> None:
    statuses = sorted(e.status for e in store.model_executions(FAILED_INVOCATION))
    assert statuses == ["error", "skipped", "success", "success"]


def test_the_sample_lineage_of_daily_orders(
    store: WorkloadStore, ingested: dict[str, IngestResult]
) -> None:
    daily = next(c for c in store.children(SECOND_RUN) if _model_name(c) == "daily_orders")
    edges = {(e.direction, e.asset.name) for e in store.lineage_edges(daily.run_id)}
    assert edges == {
        (LineageDirection.INPUT, "awsdatacatalog.reference_workload.stg_orders"),
        (LineageDirection.OUTPUT, "awsdatacatalog.reference_workload.daily_orders"),
    }
