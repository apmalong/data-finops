"""The Cost Summary through the `reporting` read model's public interface (#105; spec 06).

Databases are filled through the existing public ingest functions and Calculation Runs.
The fixture scenario's amounts come from the golden oracle (`fixtures/golden/scenarios.json`,
existing-fixtures scenario); the second account's come from its own hand-written lines.
"""

import json
import sqlite3
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from data_finops.billing.focus import load_export
from data_finops.calculation import run_calculation
from data_finops.contracts.allocation import AllocationCategory, Confidence
from data_finops.contracts.billing import CostBasis, FocusBillingLine, FocusExport
from data_finops.ledger import Ledger
from data_finops.reporting import CostSummary, DatabaseUnavailable, Reporting
from data_finops.workload.store import WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
EXPORT = load_export(FIXTURES / "focus" / "export-2026-10.json")
ACCOUNT, REGION = "123456789012", "us-east-1"
ORACLE = next(
    s
    for s in json.loads((FIXTURES / "golden" / "scenarios.json").read_text(encoding="utf-8"))
    if s["id"] == "existing-fixtures-complete-coverage"
)


def _oracle_total(**match: str) -> Decimal:
    """The oracle's BilledCost Allocations matching every given field, summed."""
    return sum(
        (
            Decimal(a["amount"])
            for a in ORACLE["allocations"]
            if a["cost_basis"] == "BilledCost" and all(a[k] == v for k, v in match.items())
        ),
        Decimal(0),
    )


class Database(NamedTuple):
    path: Path
    calculation_run_id: str


def _ingest_fixtures(ledger: Ledger, store: WorkloadStore) -> None:
    ledger.import_snapshot(EXPORT)
    for name in ("query-executions.json", "link-outcomes.json", "link-outcomes-restated.json"):
        store.ingest_query_history(FIXTURES / "athena" / name, account_id=ACCOUNT, region=REGION)
    store.ingest_events(FIXTURES / "openlineage" / "events.json")
    store.ingest_dbt_artifact(FIXTURES / "dbt" / "manifest.json")
    store.ingest_dbt_artifact(FIXTURES / "dbt" / "run_results.json")


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Database:
    path = tmp_path_factory.mktemp("reporting") / "data-finops.sqlite"
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        _ingest_fixtures(ledger, store)
        run = run_calculation(ledger, store, EXPORT.snapshot.scope)
    return Database(path, run.calculation_run_id)


@pytest.fixture
def reporting(database: Database) -> Iterator[Reporting]:
    with Reporting(database.path) as opened:
        yield opened


def test_the_cost_summary_shows_where_the_bill_went(
    reporting: Reporting, database: Database
) -> None:
    (summary,) = reporting.cost_summary().summaries
    assert (summary.scope, summary.cost_basis) == (EXPORT.snapshot.scope, CostBasis.BILLED_COST)
    (usd,) = summary.currencies
    billed = sum((Decimal(line["amount"]) for line in ORACLE["billing_lines"]), Decimal(0))
    assert (usd.currency, usd.billed) == ("USD", billed)
    assert [(a.category, a.confidence, a.amount) for a in usd.allocated] == [
        (
            AllocationCategory.DIRECT,
            Confidence.METERED_ESTIMATE,
            _oracle_total(category="direct", confidence="metered_estimate"),
        )
    ]
    assert [(u.reason, u.amount) for u in usd.unallocated] == [
        ("ineligible", _oracle_total(category="unallocated", method="ineligible"))
    ]
    assert (usd.adjustments, usd.reconciliation_delta) == (Decimal(0), Decimal(0))
    snapshot = summary.snapshot
    assert (snapshot.calculation_run_id, snapshot.is_current) == (database.calculation_run_id, True)
    assert snapshot.export_snapshot_id == EXPORT.snapshot.snapshot_id
    assert snapshot.policy_version.startswith("allocation-engine/")


def test_filters_keep_only_matching_scopes(reporting: Reporting) -> None:
    everything = reporting.cost_summary()
    assert (
        reporting.cost_summary(billing_account_id=ACCOUNT, billing_period=date(2026, 10, 1))
        == everything
    )
    assert reporting.cost_summary(billing_account_id="999999999999").summaries == []
    assert reporting.cost_summary(billing_period=date(2026, 9, 1)).summaries == []


def test_an_unknown_cost_basis_is_refused(reporting: Reporting) -> None:
    with pytest.raises(ValueError):
        reporting.cost_summary(cost_basis="InvoiceCost")  # type: ignore[arg-type]


# A second account with hand-written lines: an S3 line in USD whose EffectiveCost differs
# from its BilledCost, and in EUR an S3 line and a credit. No Athena line, so the
# Calculation Run makes every line Unallocated: `ineligible`, or `adjustment` for the credit.
OTHER_ACCOUNT = "210987654321"


def _line(currency: str, billed: str, effective: str, category: str) -> FocusBillingLine:
    costs: dict[str, Any] = {basis.value: Decimal(billed) for basis in CostBasis}
    return FocusBillingLine.model_validate(
        EXPORT.lines[1].model_dump()
        | costs
        | {"EffectiveCost": Decimal(effective), "BillingCurrency": currency}
        | {"ChargeCategory": category}
        | {"BillingAccountId": OTHER_ACCOUNT, "SubAccountId": OTHER_ACCOUNT}
    )


OTHER_EXPORT = FocusExport(
    snapshot=EXPORT.snapshot.model_copy(
        update={"snapshot_id": "other-account-v1", "billing_account_id": OTHER_ACCOUNT}
    ),
    lines=[
        _line("USD", "3.00", "2.50", "Usage"),
        _line("EUR", "2.00", "2.00", "Usage"),
        _line("EUR", "-1.00", "-1.00", "Credit"),
    ],
)


@pytest.fixture(scope="module")
def two_accounts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("two-accounts") / "data-finops.sqlite"
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        _ingest_fixtures(ledger, store)
        ledger.import_snapshot(OTHER_EXPORT)
        run_calculation(ledger, store, EXPORT.snapshot.scope)
        run_calculation(ledger, store, OTHER_EXPORT.snapshot.scope)
    return path


def _summary_of(path: Path, account: str, basis: CostBasis = CostBasis.BILLED_COST) -> CostSummary:
    with Reporting(path) as reporting:
        (summary,) = reporting.cost_summary(cost_basis=basis, billing_account_id=account).summaries
    return summary


def test_each_scope_is_summarised_apart(two_accounts: Path) -> None:
    with Reporting(two_accounts) as reporting:
        summaries = reporting.cost_summary().summaries
    assert [s.scope for s in summaries] == [EXPORT.snapshot.scope, OTHER_EXPORT.snapshot.scope]
    assert len({s.snapshot.calculation_run_id for s in summaries}) == 2
    assert summaries[0] == _summary_of(two_accounts, ACCOUNT)


def test_each_currency_is_summarised_apart_with_adjustments_shown_apart(
    two_accounts: Path,
) -> None:
    eur, usd = _summary_of(two_accounts, OTHER_ACCOUNT).currencies
    assert (eur.currency, eur.billed, eur.adjustments) == ("EUR", Decimal("1.00"), Decimal("-1.00"))
    assert (eur.allocated, [(u.reason, u.amount) for u in eur.unallocated]) == (
        [],
        [("ineligible", Decimal("2.00"))],
    )
    assert (usd.currency, usd.billed, usd.adjustments) == ("USD", Decimal("3.00"), Decimal(0))
    assert (eur.reconciliation_delta, usd.reconciliation_delta) == (Decimal(0), Decimal(0))


def test_the_cost_basis_chooses_the_column(two_accounts: Path) -> None:
    summary = _summary_of(two_accounts, OTHER_ACCOUNT, CostBasis.EFFECTIVE_COST)
    usd = summary.currencies[1]
    assert (summary.cost_basis, usd.billed, usd.unallocated[0].amount) == (
        CostBasis.EFFECTIVE_COST,
        Decimal("2.50"),
        Decimal("2.50"),
    )


def test_a_scope_never_calculated_is_listed_and_its_bill_not_shown(tmp_path: Path) -> None:
    path = tmp_path / "data-finops.sqlite"
    with Ledger(path) as ledger:
        ledger.import_snapshot(EXPORT)
    with Reporting(path) as reporting:
        result = reporting.cost_summary()
    assert (result.summaries, result.uncalculated_scopes) == ([], [EXPORT.snapshot.scope])


def test_the_bill_comes_from_the_export_snapshot_the_run_read(tmp_path: Path) -> None:
    # An Allocation Snapshot short of its bill cannot come from a Calculation Run, so this
    # one is stored through the Ledger directly: it allocates nothing, and the whole bill
    # shows as Reconciliation Delta instead of disappearing.
    path = tmp_path / "data-finops.sqlite"
    with Ledger(path) as ledger:
        ledger.import_snapshot(OTHER_EXPORT)
        ledger.store_allocations(
            OTHER_EXPORT.snapshot.scope,
            calculation_run_id="calc-empty",
            policy_version="test",
            input_digest="empty",
            export_snapshot_id=OTHER_EXPORT.snapshot.snapshot_id,
            allocations=[],
        )
    eur, usd = _summary_of(path, OTHER_ACCOUNT).currencies
    assert (eur.billed, eur.reconciliation_delta) == (Decimal("1.00"), Decimal("1.00"))
    assert (usd.billed, usd.reconciliation_delta) == (Decimal("3.00"), Decimal("3.00"))


def test_a_snapshot_stored_before_m5_still_shows_its_bill(tmp_path: Path) -> None:
    # A database file from M4 has no column for the Export Snapshot a run read; opening it
    # adds the column, and the bill is found through the Allocations' line references.
    path = tmp_path / "data-finops.sqlite"
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        _ingest_fixtures(ledger, store)
        run_calculation(ledger, store, EXPORT.snapshot.scope)
    with sqlite3.connect(path) as m4_file:
        m4_file.execute("ALTER TABLE allocation_snapshot DROP COLUMN export_snapshot_id")
    with Reporting(path) as reporting:
        (summary,) = reporting.cost_summary().summaries
    assert summary.snapshot.export_snapshot_id is None
    assert (summary.currencies[0].billed, summary.currencies[0].reconciliation_delta) == (
        Decimal("10.00"),
        Decimal(0),
    )


def test_a_file_the_stores_refuse_is_unavailable(tmp_path: Path) -> None:
    not_a_database = tmp_path / "notes.txt"
    not_a_database.write_text("not a database", encoding="utf-8")
    before_m3 = tmp_path / "m2.sqlite"
    with sqlite3.connect(before_m3) as m2_file:  # M2's table, without the columns M3 added
        m2_file.execute("CREATE TABLE workload_lineage_event (run_id TEXT)")
    missing = tmp_path / "typo.sqlite"
    for path in (not_a_database, before_m3, missing):
        with pytest.raises(DatabaseUnavailable):
            Reporting(path)
    assert not missing.exists()
