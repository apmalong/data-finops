"""A database filled through the public ingest functions and a Calculation Run (spec 06,
seam 1): every hand-written fixture, as in the M4 exit test."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from data_finops.billing.focus import load_export
from data_finops.calculation import run_calculation
from data_finops.contracts.billing import CostBasis, FocusExport
from data_finops.ledger import Ledger
from data_finops.workload.store import WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
EXPORT = load_export(FIXTURES / "focus" / "export-2026-10.json")
ACCOUNT, REGION = "123456789012", "us-east-1"


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
