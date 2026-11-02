"""The hand-written fixtures under the Athena billing scope (#83; ADR 0023).

The link-outcome queries (M3) run in the same account, region and day as the golden
Athena line but in workgroup `demo-link-outcomes`; the line names workgroup `demo`, so
only q-001 and q-002 count toward its Coverage and golden Coverage stays 1.
"""

from pathlib import Path

from data_finops.billing.focus import load_export
from data_finops.providers.athena_billing import in_billing_scope
from data_finops.workload.store import WorkloadStore

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT, REGION = "123456789012", "us-east-1"


def test_only_the_demo_workgroup_queries_fall_in_the_golden_lines_scope(tmp_path: Path) -> None:
    line = load_export(ROOT / "fixtures" / "focus" / "export-2026-10.json").lines[0]
    with WorkloadStore(tmp_path / "data-finops.sqlite") as store:
        for name in ("query-executions.json", "link-outcomes.json"):
            store.ingest_query_history(
                ROOT / "fixtures" / "athena" / name, account_id=ACCOUNT, region=REGION
            )
        queries = {
            query_id: store.query_execution(ACCOUNT, REGION, query_id)
            for query_id in ("q-001", "q-002", "q-003", "q-004")
        }
    in_scope = set()
    for query_id, query in queries.items():
        assert query is not None, query_id
        if in_billing_scope(query, line):
            in_scope.add(query_id)
    assert in_scope == {"q-001", "q-002"}
