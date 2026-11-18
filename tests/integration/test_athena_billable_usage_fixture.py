"""The hand-written fixtures under athena-billable-usage (#82, #91, #310).

golden-fixtures.md assumes the $6 Athena line's 0.001 TB equals q-001's Billable Usage
within the line's rounding; here the policy computes it from the stored Query Executions.
"""

from decimal import Decimal
from pathlib import Path

from data_finops.billing.focus import load_export
from data_finops.providers.athena_billing import BILLABLE_USAGE_V2, billable_quantities
from data_finops.workload.store import WorkloadStore

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT, REGION = "123456789012", "us-east-1"


def test_the_fixture_queries_cover_the_golden_athena_line(tmp_path: Path) -> None:
    line = load_export(ROOT / "fixtures" / "focus" / "export-2026-10.json").lines[0]
    assert line.ConsumedUnit is not None
    with WorkloadStore(tmp_path / "data-finops.sqlite") as store:
        store.ingest_query_history(
            ROOT / "fixtures" / "athena" / "query-executions.json",
            account_id=ACCOUNT,
            region=REGION,
        )
        queries = [store.query_execution(ACCOUNT, REGION, q) for q in ("q-001", "q-002")]
    assert None not in queries
    current = billable_quantities(queries, line.ConsumedUnit)  # type: ignore[arg-type]
    decimal = billable_quantities(queries, line.ConsumedUnit, BILLABLE_USAGE_V2)  # type: ignore[arg-type]
    # q-002: DDL, nothing scanned. Under /3, 10^9 / 2^40 TB is 0.00090949, rounded down.
    assert current == [Decimal("0.000905"), 0]
    assert line.ConsumedQuantity is not None
    assert abs(sum(q for q in current if q) - line.ConsumedQuantity) <= Decimal("0.0005")
    assert decimal == [Decimal("0.001"), 0]  # /2: exactly the line
