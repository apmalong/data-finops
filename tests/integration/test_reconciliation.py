"""Reconciliation through the Ledger's public interface (seam agreed in #6)."""

from decimal import Decimal
from pathlib import Path

import pytest

from data_finops.billing.focus import load_export
from data_finops.contracts.billing import ExportSnapshot, FocusChargeCategory, FocusExport
from data_finops.ledger import Ledger

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "focus" / "export-2026-10.json"


@pytest.mark.parametrize("cost_basis", ["BilledCost", "EffectiveCost"])
def test_fixture_reconciles_with_zero_delta_on_every_line(tmp_path, cost_basis):
    export = load_export(FIXTURE)
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)
        report = ledger.reconcile(export, cost_basis=cost_basis)

    assert report.cost_basis == cost_basis
    assert [(line.ref, str(line.source_amount), line.delta) for line in report.lines] == [
        ("aws-focus-123456789012-2026-10-v1#0", "6.00", Decimal("0")),
        ("aws-focus-123456789012-2026-10-v1#1", "4.00", Decimal("0")),
    ]
    assert {c: (str(t.source_amount), t.delta) for c, t in report.totals.items()} == {
        "USD": ("10.00", Decimal("0"))
    }
    assert report.balanced


def test_each_currency_gets_its_own_total_and_none_are_combined(tmp_path):
    export = load_export(FIXTURE)
    eur = export.lines[1].model_copy(update={"BillingCurrency": "EUR"})
    mixed = FocusExport(snapshot=export.snapshot, lines=[export.lines[0], eur])
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(mixed)
        report = ledger.reconcile(mixed)

    assert {c: str(t.source_amount) for c, t in report.totals.items()} == {
        "USD": "6.00",
        "EUR": "4.00",
    }


def test_negative_sub_cent_credit_reconciles_exactly_and_stays_signed(tmp_path):
    export = load_export(FIXTURE)
    credit = export.lines[0].model_copy(
        update={
            "ChargeCategory": FocusChargeCategory.CREDIT,
            "BilledCost": Decimal("-0.0000123"),
            "EffectiveCost": Decimal("-0.0000123"),
        }
    )
    with_credit = FocusExport(snapshot=export.snapshot, lines=[*export.lines, credit])
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(with_credit)
        report = ledger.reconcile(with_credit)

    assert str(report.lines[2].source_amount) == "-0.0000123"
    assert report.lines[2].delta == 0
    assert str(report.totals["USD"].source_amount) == "9.9999877"
    assert report.balanced


def test_unknown_cost_basis_is_rejected(tmp_path):
    export = load_export(FIXTURE)
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)
        with pytest.raises(ValueError, match="NetCost"):
            ledger.reconcile(export, cost_basis="NetCost")


def test_current_totals_exclude_a_superseded_snapshot(tmp_path):
    a = load_export(FIXTURE)
    b = FocusExport(
        snapshot=ExportSnapshot.model_validate(
            {
                **a.snapshot.model_dump(mode="json"),
                "snapshot_id": "aws-focus-123456789012-2026-10-v2",
                "published_at": "2026-10-03T06:00:00Z",
            }
        ),
        lines=a.lines[:1],
    )
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(a)
        ledger.import_snapshot(b)
        current = ledger.current_snapshot(a.snapshot.scope)
        assert current is not None
        report = ledger.reconcile(current)

    assert report.snapshot_id == "aws-focus-123456789012-2026-10-v2"
    assert {c: str(t.ledger_amount) for c, t in report.totals.items()} == {"USD": "6.00"}


def test_a_source_that_differs_from_the_ledger_shows_a_nonzero_delta(tmp_path):
    export = load_export(FIXTURE)
    changed = export.lines[1].model_copy(update={"BilledCost": Decimal("4.50")})
    different_source = FocusExport(
        snapshot=export.snapshot, lines=[export.lines[0], changed, export.lines[1]]
    )
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)
        report = ledger.reconcile(different_source)

    assert [str(line.delta) for line in report.lines] == ["0.00", "0.50", "4.00"]
    assert str(report.totals["USD"].delta) == "4.50"
    assert not report.balanced
