"""Ledger behaviour through its public interface (seam agreed in #6)."""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from data_finops.billing.focus import load_export
from data_finops.contracts.billing import FocusExport
from data_finops.ledger import Ledger

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "focus" / "export-2026-10.json"


def test_imported_snapshot_reads_back_with_exact_amounts_and_every_column(tmp_path):
    export = load_export(FIXTURE)
    with Ledger.open(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)
        current = ledger.current_snapshot(export.snapshot.scope)

    assert current is not None
    assert current.model_dump(mode="json") == export.model_dump(mode="json")
    athena = current.lines[0]
    assert str(athena.BilledCost) == "6.00"
    assert athena.model_dump(mode="json")["x_ServiceCode"] == "AmazonAthena"
    assert current.line_ref(1) == "aws-focus-123456789012-2026-10-v1#1"


def test_ledger_contents_survive_reopening(tmp_path):
    export = load_export(FIXTURE)
    with Ledger.open(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)

    with Ledger.open(tmp_path / "ledger.db") as reopened:
        current = reopened.current_snapshot(export.snapshot.scope)

    assert current is not None
    assert [str(line.BilledCost) for line in current.lines] == ["6.00", "4.00"]


def test_unknown_scope_has_no_current_snapshot(tmp_path):
    export = load_export(FIXTURE)
    other = export.snapshot.scope.model_copy(update={"billing_account_id": "999999999999"})
    with Ledger.open(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)
        assert ledger.current_snapshot(other) is None


@pytest.mark.parametrize(
    "misfiled",
    [
        {"BillingAccountId": "999999999999"},
        {"BillingPeriodStart": "2026-09-01T00:00:00Z", "BillingPeriodEnd": "2026-10-01T00:00:00Z"},
    ],
)
def test_misfiled_line_is_rejected_and_nothing_is_stored(tmp_path, misfiled):
    fixture = load_export(FIXTURE).model_dump(mode="json")
    fixture["lines"][1].update(misfiled)
    with Ledger.open(tmp_path / "ledger.db") as ledger:
        with pytest.raises(ValidationError):
            ledger.import_snapshot(FocusExport.model_validate(fixture))
        assert ledger.current_snapshot(load_export(FIXTURE).snapshot.scope) is None


def test_identical_rows_in_one_snapshot_stay_distinct_billing_lines(tmp_path):
    fixture = load_export(FIXTURE)
    athena = fixture.lines[0]
    export = FocusExport(snapshot=fixture.snapshot, lines=[athena, athena])
    with Ledger.open(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)
        current = ledger.current_snapshot(export.snapshot.scope)

    assert current is not None
    assert [line.BilledCost for line in current.lines] == [Decimal("6.00"), Decimal("6.00")]
    assert {current.line_ref(0), current.line_ref(1)} == {
        "aws-focus-123456789012-2026-10-v1#0",
        "aws-focus-123456789012-2026-10-v1#1",
    }
