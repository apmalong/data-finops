"""Databases filled through the public ingest functions and Calculation Runs (spec 06,
seam 1): every hand-written fixture, as in the M4 exit test, and hand-written variations."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from data_finops.billing.focus import load_export
from data_finops.calculation import run_calculation
from data_finops.contracts.billing import CostBasis, FocusBillingLine, FocusExport
from data_finops.ledger import Ledger
from data_finops.workload.store import WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
EXPORT = load_export(FIXTURES / "focus" / "export-2026-10.json")
ACCOUNT, REGION = "123456789012", "us-east-1"
OTHER_ACCOUNT = "210987654321"


def fill(path: Path) -> str:
    """Ingest every fixture and run one Calculation Run; returns its ID."""
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        ledger.import_snapshot(EXPORT)
        for name in ("query-executions.json", "link-outcomes.json", "link-outcomes-restated.json"):
            store.ingest_query_history(
                FIXTURES / "athena" / name, account_id=ACCOUNT, region=REGION
            )
        store.ingest_events(FIXTURES / "openlineage" / "events.json")
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "manifest.json")
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "run_results.json")
        return run_calculation(ledger, store, EXPORT.snapshot.scope).calculation_run_id


def restate(path: Path) -> str:
    """Import a later publication of the fixture export, its S3 line restated from $4.00 to
    $5.00, and recalculate; returns the new Calculation Run's ID."""
    lines = [*EXPORT.lines]
    lines[1] = lines[1].model_copy(update={basis.value: Decimal("5.00") for basis in CostBasis})
    restated = FocusExport(
        snapshot=EXPORT.snapshot.model_copy(
            update={
                "snapshot_id": "aws-focus-123456789012-2026-10-v2",
                "published_at": EXPORT.snapshot.published_at + timedelta(days=1),
            }
        ),
        lines=lines,
    )
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        ledger.import_snapshot(restated)
        return run_calculation(ledger, store, EXPORT.snapshot.scope).calculation_run_id


def _other_line(currency: str, amount: str, category: str) -> FocusBillingLine:
    costs: dict[str, Any] = {basis.value: Decimal(amount) for basis in CostBasis}
    return FocusBillingLine.model_validate(
        EXPORT.lines[1].model_dump()
        | costs
        | {"BillingCurrency": currency, "ChargeCategory": category}
        | {"BillingAccountId": OTHER_ACCOUNT, "SubAccountId": OTHER_ACCOUNT}
    )


OTHER_EXPORT = FocusExport(
    snapshot=EXPORT.snapshot.model_copy(
        update={"snapshot_id": "other-account-v1", "billing_account_id": OTHER_ACCOUNT}
    ),
    lines=[
        _other_line("USD", "3.00", "Usage"),
        _other_line("EUR", "2.00", "Usage"),
        _other_line("EUR", "-1.00", "Credit"),
    ],
)


def add_other_account(path: Path) -> str:
    """A second Snapshot Scope with S3 lines in USD and EUR and a EUR credit, calculated (every
    line Unallocated); returns its Calculation Run's ID."""
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        ledger.import_snapshot(OTHER_EXPORT)
        return run_calculation(ledger, store, OTHER_EXPORT.snapshot.scope).calculation_run_id
