"""Reconciliation of Allocations against the bill through the `reporting` read model (#112;
spec 06 seam 1; GLOSSARY Reconciliation, Reconciliation Delta).

Billed amounts come from the hand-written lines themselves: the fixture's $6.00 Athena line
and $4.00 S3 line, and the second account's USD $3.00, EUR $2.00 and EUR -$1.00 credit.
"""

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import pytest
from fixture_database import ACCOUNT, EXPORT, OTHER_ACCOUNT, add_other_account, fill

from data_finops.contracts.billing import CostBasis
from data_finops.ledger import Ledger
from data_finops.reporting import AmbiguousScope, NotFound, Reporting


class Database(NamedTuple):
    path: Path
    run: str


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Database:
    path = tmp_path_factory.mktemp("reconciliation") / "data-finops.sqlite"
    run = fill(path)
    add_other_account(path)
    return Database(path, run)


@pytest.fixture
def reporting(database: Database) -> Iterator[Reporting]:
    with Reporting(database.path) as opened:
        yield opened


def test_every_line_and_cost_basis_reconciles_to_zero(
    reporting: Reporting, database: Database
) -> None:
    report = reporting.reconciliation(billing_account_id=ACCOUNT)
    assert (report.calculation_run_id, report.export_snapshot_id) == (
        database.run,
        EXPORT.snapshot.snapshot_id,
    )
    assert report.total == 8  # two lines, four Cost Bases
    billed = {(line.billing_line_ref, line.cost_basis): line.billed for line in report.items}
    assert billed[(f"{EXPORT.snapshot.snapshot_id}#0", CostBasis.BILLED_COST)] == Decimal("6.00")
    assert billed[(f"{EXPORT.snapshot.snapshot_id}#1", CostBasis.BILLED_COST)] == Decimal("4.00")
    assert all(
        line.allocated_amount == line.billed and line.reconciliation_delta == 0
        for line in report.items
    )
    assert {
        (t.currency, t.cost_basis, t.billed, t.reconciliation_delta) for t in report.totals
    } == {("USD", basis, Decimal("10.00"), Decimal(0)) for basis in CostBasis}


def test_currencies_are_totalled_apart_with_credits_signed(reporting: Reporting) -> None:
    report = reporting.reconciliation(
        billing_account_id=OTHER_ACCOUNT, cost_basis=CostBasis.BILLED_COST
    )
    assert {
        (t.currency, t.billed, t.allocated_amount, t.reconciliation_delta) for t in report.totals
    } == {
        ("USD", Decimal("3.00"), Decimal("3.00"), Decimal(0)),
        ("EUR", Decimal("1.00"), Decimal("1.00"), Decimal(0)),
    }
    assert {line.cost_basis for line in report.items} == {CostBasis.BILLED_COST}
    assert report.total == 3


def test_allocations_short_of_the_bill_show_their_delta_per_line(tmp_path: Path) -> None:
    # A Calculation Run always balances, so a short snapshot is stored through the Ledger:
    # it keeps the Athena line's Allocations and drops the S3 line's.
    path = tmp_path / "data-finops.sqlite"
    run = fill(path)
    with Ledger(path) as ledger:
        stored = ledger.allocation_snapshot(run)
        assert stored is not None
        ledger.store_allocations(
            EXPORT.snapshot.scope,
            calculation_run_id="calc-short",
            policy_version=stored.snapshot.policy_version,
            input_digest="short",
            export_snapshot_id=EXPORT.snapshot.snapshot_id,
            allocations=[
                a.model_copy(update={"calculation_run_id": "calc-short"})
                for a in stored.allocations
                if a.billing_line_ref.endswith("#0")
            ],
        )
    with Reporting(path) as reporting:
        report = reporting.reconciliation(
            billing_account_id=ACCOUNT, cost_basis=CostBasis.BILLED_COST
        )
    deltas = {
        line.billing_line_ref.rpartition("#")[2]: line.reconciliation_delta for line in report.items
    }
    assert deltas == {"0": Decimal(0), "1": Decimal("4.00")}
    (usd,) = report.totals
    assert (usd.billed, usd.allocated_amount, usd.reconciliation_delta) == (
        Decimal("10.00"),
        Decimal("6.00"),
        Decimal("4.00"),
    )


def test_lines_are_paged_but_totals_cover_them_all(reporting: Reporting) -> None:
    first = reporting.reconciliation(billing_account_id=ACCOUNT, limit=3)
    rest = reporting.reconciliation(billing_account_id=ACCOUNT, offset=3)
    assert (len(first.items), first.total, first.totals) == (3, 8, rest.totals)
    assert first.items + rest.items == reporting.reconciliation(billing_account_id=ACCOUNT).items
    with pytest.raises(ValueError):
        reporting.reconciliation(billing_account_id=ACCOUNT, limit=1001)


def test_snapshot_selection_is_as_for_the_other_reads(
    reporting: Reporting, database: Database
) -> None:
    named = reporting.reconciliation(calculation_run_id=database.run)
    assert named.calculation_run_id == database.run
    with pytest.raises(AmbiguousScope):
        reporting.reconciliation()
    with pytest.raises(NotFound):
        reporting.reconciliation(calculation_run_id="calc-unknown")


def test_amounts_allocated_outside_the_bill_or_in_another_currency_show_as_deltas(
    tmp_path: Path,
) -> None:
    # Invented amounts must be visible too: an Allocation to a line the Export Snapshot does
    # not have, and one in a currency other than its line's. Stored through the Ledger, since
    # a Calculation Run never produces them.
    path = tmp_path / "data-finops.sqlite"
    run = fill(path)
    with Ledger(path) as ledger:
        stored = ledger.allocation_snapshot(run)
        assert stored is not None
        allocations = [
            a.model_copy(update={"calculation_run_id": "calc-invented"}) for a in stored.allocations
        ]
        athena = next(
            a
            for a in allocations
            if a.cost_basis == CostBasis.BILLED_COST and a.target_id == "q-001"
        )
        allocations += [
            athena.model_copy(update={"billing_line_ref": f"{EXPORT.snapshot.snapshot_id}#9"}),
            athena.model_copy(update={"currency": "EUR"}),
        ]
        ledger.store_allocations(
            EXPORT.snapshot.scope,
            calculation_run_id="calc-invented",
            policy_version=stored.snapshot.policy_version,
            input_digest="invented",
            export_snapshot_id=EXPORT.snapshot.snapshot_id,
            allocations=allocations,
        )
    with Reporting(path) as reporting:
        report = reporting.reconciliation(
            billing_account_id=ACCOUNT, cost_basis=CostBasis.BILLED_COST
        )
    rows = {
        (line.billing_line_ref.rpartition("#")[2], line.currency): (
            line.billed,
            line.allocated_amount,
            line.reconciliation_delta,
        )
        for line in report.items
    }
    assert rows == {
        ("0", "USD"): (Decimal("6.00"), Decimal("6.00"), Decimal(0)),
        ("1", "USD"): (Decimal("4.00"), Decimal("4.00"), Decimal(0)),
        ("9", "USD"): (Decimal(0), Decimal("6.00"), Decimal("-6.00")),
        ("0", "EUR"): (Decimal(0), Decimal("6.00"), Decimal("-6.00")),
    }
    assert {(t.currency, t.reconciliation_delta) for t in report.totals} == {
        ("USD", Decimal("-6.00")),
        ("EUR", Decimal("-6.00")),
    }
