"""The Cost Summary through the `reporting` read model's public interface (#105; spec 06).

The database is filled through the existing public ingest functions and a Calculation Run
from the hand-written fixtures, as in the M4 exit test; expected amounts come from the
golden oracle (`fixtures/golden/scenarios.json`, existing-fixtures scenario): $6.00 metered
to `q-001` and $4.00 Unallocated `ineligible` on a $10.00 bill.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from data_finops.attribution.engine import (
    Adjustment,
    DirectCostPool,
    IdleCostPool,
    Ineligible,
    PooledLine,
    allocate,
)
from data_finops.billing.focus import load_export
from data_finops.calculation import run_calculation
from data_finops.contracts.allocation import AllocationCategory, Confidence
from data_finops.contracts.billing import CostBasis, FocusBillingLine, FocusExport
from data_finops.ledger import Ledger
from data_finops.reporting import Reporting
from data_finops.workload.store import WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
EXPORT = load_export(FIXTURES / "focus" / "export-2026-10.json")
ACCOUNT, REGION = "123456789012", "us-east-1"


def _fill(path: Path) -> str:
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


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    path = tmp_path_factory.mktemp("reporting") / "data-finops.sqlite"
    return path, _fill(path)


@pytest.fixture
def reporting(database: tuple[Path, str]) -> Iterator[Reporting]:
    with Reporting(database[0]) as opened:
        yield opened


def test_the_cost_summary_shows_where_the_bill_went(
    reporting: Reporting, database: tuple[Path, str]
) -> None:
    summary = reporting.cost_summary()
    assert summary.cost_basis == CostBasis.BILLED_COST
    (usd,) = summary.currencies
    assert (usd.currency, usd.billed) == ("USD", Decimal("10.00"))
    assert [(a.category, a.confidence, a.amount) for a in usd.allocated] == [
        (AllocationCategory.DIRECT, Confidence.METERED_ESTIMATE, Decimal("6.00"))
    ]
    assert [(u.reason, u.amount) for u in usd.unallocated] == [("ineligible", Decimal("4.00"))]
    assert (usd.adjustments, usd.reconciliation_delta) == (Decimal("0.00"), Decimal("0.00"))
    (served,) = summary.snapshots
    assert served.calculation_run_id == database[1]
    assert served.policy_version.startswith("allocation-engine/")


def test_filters_keep_only_matching_scopes(reporting: Reporting) -> None:
    october = datetime(2026, 10, 1, tzinfo=UTC)
    assert reporting.cost_summary(billing_account_id=ACCOUNT, billing_period_start=october) == (
        reporting.cost_summary()
    )
    other = reporting.cost_summary(billing_account_id="999999999999")
    assert (other.currencies, other.snapshots) == ([], [])
    assert (
        reporting.cost_summary(billing_period_start=datetime(2026, 9, 1, tzinfo=UTC)).snapshots
        == []
    )


def test_an_unknown_cost_basis_is_refused(reporting: Reporting) -> None:
    with pytest.raises(ValueError):
        reporting.cost_summary(cost_basis="InvoiceCost")  # type: ignore[arg-type]


# A second account, built by hand: its lines are classified as callers do and allocated by
# the engine, then stored in the Ledger, so every amount below is set by the line itself.
MIXED_ACCOUNT = "210987654321"
MIXED_RUN = "calc-mixed"


def _line(currency: str, billed: str, effective: str, category: str) -> FocusBillingLine:
    return FocusBillingLine.model_validate(
        EXPORT.lines[0].model_dump()
        | {"BilledCost": Decimal(billed), "ListCost": Decimal(billed)}
        | {"ContractedCost": Decimal(billed), "EffectiveCost": Decimal(effective)}
        | {"BillingCurrency": currency, "ChargeCategory": category}
        | {"BillingAccountId": MIXED_ACCOUNT, "SubAccountId": MIXED_ACCOUNT}
    )


def _mixed_export() -> FocusExport:
    snapshot = EXPORT.snapshot.model_copy(
        update={"snapshot_id": "mixed-v1", "billing_account_id": MIXED_ACCOUNT}
    )
    lines = [
        _line("USD", "3.00", "2.50", "Usage"),
        _line("EUR", "5.00", "5.00", "Usage"),
        _line("EUR", "-1.00", "-1.00", "Credit"),
        _line("EUR", "2.00", "2.00", "Usage"),
    ]
    return FocusExport(snapshot=snapshot, lines=lines)


@pytest.fixture(scope="module")
def mixed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("mixed") / "data-finops.sqlite"
    export = _mixed_export()
    pools = [
        DirectCostPool("direct", "q-direct", Confidence.DIRECT_VERIFIED),
        IdleCostPool("idle"),
        Adjustment("adjustment"),
        Ineligible("ineligible"),
    ]
    pooled = [PooledLine(export.line_ref(i), line, pools[i]) for i, line in enumerate(export.lines)]
    with Ledger(path) as ledger:
        ledger.import_snapshot(export)
        ledger.store_allocations(
            export.snapshot.scope,
            calculation_run_id=MIXED_RUN,
            policy_version="test",
            input_digest="mixed",
            allocations=allocate(pooled, policy_version="test", calculation_run_id=MIXED_RUN),
        )
    return path


def test_each_currency_is_summarised_apart_with_adjustments_and_idle(mixed: Path) -> None:
    with Reporting(mixed) as reporting:
        eur, usd = reporting.cost_summary().currencies
    assert (eur.currency, eur.billed, eur.adjustments) == ("EUR", Decimal("6.00"), Decimal("-1.00"))
    assert [(a.category, a.confidence, a.amount) for a in eur.allocated] == [
        (AllocationCategory.IDLE, None, Decimal("5.00"))
    ]
    assert [(u.reason, u.amount) for u in eur.unallocated] == [("ineligible", Decimal("2.00"))]
    assert [(a.category, a.confidence, a.amount) for a in usd.allocated] == [
        (AllocationCategory.DIRECT, Confidence.DIRECT_VERIFIED, Decimal("3.00"))
    ]
    assert (eur.reconciliation_delta, usd.reconciliation_delta) == (Decimal(0), Decimal(0))


def test_the_cost_basis_chooses_the_column(mixed: Path) -> None:
    with Reporting(mixed) as reporting:
        summary = reporting.cost_summary(cost_basis=CostBasis.EFFECTIVE_COST)
    usd = summary.currencies[1]
    assert (summary.cost_basis, usd.billed, usd.allocated[0].amount) == (
        CostBasis.EFFECTIVE_COST,
        Decimal("2.50"),
        Decimal("2.50"),
    )


def test_a_scope_never_calculated_is_listed_and_its_bill_not_shown(tmp_path: Path) -> None:
    path = tmp_path / "data-finops.sqlite"
    with Ledger(path) as ledger:
        ledger.import_snapshot(EXPORT)
    with Reporting(path) as reporting:
        summary = reporting.cost_summary()
    assert (summary.currencies, summary.snapshots) == ([], [])
    assert summary.uncalculated_scopes == [EXPORT.snapshot.scope]


def test_allocations_short_of_the_bill_show_a_reconciliation_delta(tmp_path: Path) -> None:
    path = tmp_path / "data-finops.sqlite"
    export = _mixed_export()
    pooled = [PooledLine(export.line_ref(0), export.lines[0], Ineligible("ineligible"))]
    with Ledger(path) as ledger:
        ledger.import_snapshot(export)
        ledger.store_allocations(
            export.snapshot.scope,
            calculation_run_id="calc-short",
            policy_version="test",
            input_digest="short",
            allocations=allocate(pooled, policy_version="test", calculation_run_id="calc-short"),
        )
    with Reporting(path) as reporting:
        eur, usd = reporting.cost_summary().currencies
    assert (eur.billed, eur.reconciliation_delta) == (Decimal("6.00"), Decimal("6.00"))
    assert usd.reconciliation_delta == Decimal(0)
