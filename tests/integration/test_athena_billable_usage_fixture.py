"""The hand-written fixtures under athena-billable-usage/1 (#82): the golden line's Coverage.

golden-fixtures.md assumes the $6 Athena line's 0.001 TB equals q-001's Billable Usage;
here the policy computes it from the stored Query Executions.
"""

from decimal import Decimal
from pathlib import Path

from data_finops.billing.focus import load_export
from data_finops.providers.athena_billing import billable_bytes, bytes_in_consumed_unit
from data_finops.workload.store import WorkloadStore

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT, REGION = "123456789012", "us-east-1"


def test_the_fixture_queries_cover_the_golden_athena_line_exactly(tmp_path: Path) -> None:
    line = load_export(ROOT / "fixtures" / "focus" / "export-2026-10.json").lines[0]
    assert line.ConsumedUnit is not None
    with WorkloadStore(tmp_path / "data-finops.sqlite") as store:
        store.ingest_query_history(
            ROOT / "fixtures" / "athena" / "query-executions.json",
            account_id=ACCOUNT,
            region=REGION,
        )
        usage = {}
        for query_id in ("q-001", "q-002"):
            query = store.query_execution(ACCOUNT, REGION, query_id)
            assert query is not None
            billable = billable_bytes(query)
            assert billable is not None
            usage[query_id] = bytes_in_consumed_unit(billable, line.ConsumedUnit)
    assert usage == {"q-001": Decimal("0.001"), "q-002": 0}  # q-002: DDL, nothing scanned
    assert sum(usage.values()) == line.ConsumedQuantity
