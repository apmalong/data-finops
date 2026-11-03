"""Fixtures load through the readers and keep the evidence chain M3 must resolve."""

import json
from pathlib import Path

from data_finops.billing.focus import load_export
from data_finops.contracts.billing import CostBasis
from data_finops.dbt.artifacts import load_manifest, load_run_results
from data_finops.lineage.openlineage import load_events
from data_finops.providers.athena import load_query_executions

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"


def test_focus_export_is_one_snapshot_of_focus_rows():
    export = load_export(FIXTURES / "focus" / "export-2026-10.json")
    assert export.snapshot.focus_version == "1.2"
    assert len(export.lines) == 2
    athena = export.lines[0]
    assert athena.ServiceName == "Amazon Athena"
    assert athena.ChargeCategory == "Usage"
    assert athena.ConsumedQuantity is not None
    assert export.line_ref(0) == f"{export.snapshot.snapshot_id}#0"


def test_focus_rows_preserve_provider_extension_columns():
    export = load_export(FIXTURES / "focus" / "export-2026-10.json")
    athena = export.lines[0].model_dump(mode="json")
    assert athena["x_ServiceCode"] == "AmazonAthena"
    assert athena["x_Operation"] is not None


def test_athena_history_is_provider_native_and_carries_query_comments():
    history = load_query_executions(FIXTURES / "athena" / "query-executions.json")
    by_id = {q.QueryExecutionId: q for q in history.QueryExecutions}
    assert set(by_id) == {"q-001", "q-002"}
    for q in by_id.values():
        assert '"node_id": "model.demo.orders"' in q.Query
        assert '"invocation_id": "inv-001"' in q.Query
    assert by_id["q-002"].StatementType == "DDL"


def test_openlineage_events_link_dbt_invocation_to_airflow_run():
    events = load_events(FIXTURES / "openlineage" / "events.json")
    dbt_runs = [e for e in events if "dbt_run" in e.run.facets]
    assert dbt_runs, "a dbt run event must carry the dbt_run facet"
    dbt_run = dbt_runs[0]
    assert dbt_run.run.facets["dbt_run"]["invocation_id"] == "inv-001"
    parent = dbt_run.run.facets["parent"]
    airflow = [e for e in events if e.job.name == "daily_orders.dbt_run"]
    assert airflow and parent["run"]["runId"] == airflow[0].run.runId


def test_dbt_artifacts_share_the_invocation():
    manifest = load_manifest(FIXTURES / "dbt" / "manifest.json")
    results = load_run_results(FIXTURES / "dbt" / "run_results.json")
    assert manifest.metadata.invocation_id == results.metadata.invocation_id == "inv-001"
    assert {r.unique_id for r in results.results} <= set(manifest.nodes)


def test_fixture_files_are_utf8_json():
    for path in FIXTURES.rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))


def test_the_restated_export_differs_only_by_publication_and_the_s3_line():
    # A later publication of the same scope (ADR 0020) restating one line: the exit
    # walkthrough and the provenance tests rely on exactly this difference (#113).
    original = load_export(FIXTURES / "focus" / "export-2026-10.json")
    restated = load_export(FIXTURES / "focus" / "export-2026-10-restated.json")
    assert restated.snapshot.scope == original.snapshot.scope
    assert restated.snapshot.published_at > original.snapshot.published_at
    assert restated.snapshot.snapshot_id != original.snapshot.snapshot_id
    assert restated.lines[0] == original.lines[0]
    s3 = {"BilledCost", "EffectiveCost", "ListCost", "ContractedCost"}
    assert restated.lines[1].model_dump(exclude=s3) == original.lines[1].model_dump(exclude=s3)
    assert {str(restated.lines[1].cost(basis)) for basis in CostBasis} == {"5.00"}
