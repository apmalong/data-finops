"""Ledger behaviour through its public interface (seam agreed in #6)."""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from data_finops.billing.focus import load_export
from data_finops.contracts.billing import FocusBillingLine, FocusExport
from data_finops.ledger import ImportOutcome, Ledger, SnapshotConflictError

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "focus" / "export-2026-10.json"
COST_COLUMNS = ("BilledCost", "EffectiveCost", "ListCost", "ContractedCost")


def test_imported_snapshot_reads_back_with_exact_amounts_and_every_column(tmp_path):
    export = load_export(FIXTURE)
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)
        current = ledger.current_snapshot(export.snapshot.scope)

    assert current is not None
    athena, storage = (line.model_dump(mode="json") for line in current.lines)
    assert [athena[c] for c in COST_COLUMNS] == ["6.00", "6.00", "6.00", "6.00"]
    assert [storage[c] for c in COST_COLUMNS] == ["4.00", "4.00", "4.00", "4.00"]
    assert (athena["x_ServiceCode"], athena["x_Operation"]) == ("AmazonAthena", "QueryExecution")
    assert (storage["x_ServiceCode"], storage["x_Operation"]) == ("AmazonS3", "StandardStorage")
    assert athena["BillingCurrency"] == "USD"
    assert current.model_dump(mode="json") == export.model_dump(mode="json")
    assert current.line_ref(1) == "aws-focus-123456789012-2026-10-v1#1"


def test_ledger_contents_survive_reopening(tmp_path):
    export = load_export(FIXTURE)
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)

    with Ledger(tmp_path / "ledger.db") as reopened:
        current = reopened.current_snapshot(export.snapshot.scope)

    assert current is not None
    assert [str(line.BilledCost) for line in current.lines] == ["6.00", "4.00"]


def test_unknown_scope_has_no_current_snapshot(tmp_path):
    export = load_export(FIXTURE)
    other_account = export.snapshot.scope.model_copy(update={"billing_account_id": "999999999999"})
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)
        assert ledger.current_snapshot(other_account) is None


@pytest.mark.parametrize(
    "misfiled",
    [
        {"BillingAccountId": "999999999999"},
        {"BillingPeriodStart": "2026-09-01T00:00:00Z", "BillingPeriodEnd": "2026-10-01T00:00:00Z"},
    ],
)
def test_misfiled_line_is_rejected_by_the_contract_so_the_ledger_stays_empty(tmp_path, misfiled):
    # The Ledger only accepts a validated FocusExport; snapshot consistency is enforced
    # by that contract (reader seam in #6), so a misfiled export never reaches it.
    export_json = load_export(FIXTURE).model_dump(mode="json")
    export_json["lines"][1].update(misfiled)
    with pytest.raises(ValidationError, match="differs from snapshot"):
        FocusExport.model_validate(export_json)

    with Ledger(tmp_path / "ledger.db") as ledger:
        assert ledger.current_snapshot(load_export(FIXTURE).snapshot.scope) is None


def test_identical_rows_in_one_snapshot_stay_distinct_billing_lines(tmp_path):
    source_export = load_export(FIXTURE)
    athena = source_export.lines[0]
    export = FocusExport(snapshot=source_export.snapshot, lines=[athena, athena])
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)
        current = ledger.current_snapshot(export.snapshot.scope)

    assert current is not None
    assert [line.BilledCost for line in current.lines] == [Decimal("6.00"), Decimal("6.00")]
    assert [current.line_ref(0), current.line_ref(1)] == [
        "aws-focus-123456789012-2026-10-v1#0",
        "aws-focus-123456789012-2026-10-v1#1",
    ]


def test_reimporting_the_same_snapshot_is_a_reported_no_op(tmp_path):
    export = load_export(FIXTURE)
    with Ledger(tmp_path / "ledger.db") as ledger:
        first = ledger.import_snapshot(export)
        before = ledger.current_snapshot(export.snapshot.scope)
        second = ledger.import_snapshot(export)
        after = ledger.current_snapshot(export.snapshot.scope)

    assert first.outcome is ImportOutcome.NEW
    assert second.outcome is ImportOutcome.REPLAY
    assert second.snapshot_id == "aws-focus-123456789012-2026-10-v1"
    assert before is not None and after is not None
    assert after.model_dump(mode="json") == before.model_dump(mode="json")
    assert [str(line.BilledCost) for line in after.lines] == ["6.00", "4.00"]


def test_same_content_redelivered_under_a_new_id_is_a_replay_of_the_original(tmp_path):
    export = load_export(FIXTURE)
    redelivered = _republished(
        export, "aws-focus-123456789012-2026-10-redelivered", lines=export.lines
    )
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)
        result = ledger.import_snapshot(redelivered)

    assert result.outcome is ImportOutcome.REPLAY
    assert result.snapshot_id == "aws-focus-123456789012-2026-10-v1"


def test_reused_snapshot_id_with_different_content_is_rejected(tmp_path):
    export = load_export(FIXTURE)
    altered = export.model_copy(update={"lines": export.lines[:1]})
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)
        with pytest.raises(SnapshotConflictError, match="aws-focus-123456789012-2026-10-v1"):
            ledger.import_snapshot(altered)
        current = ledger.current_snapshot(export.snapshot.scope)

    assert current is not None
    assert len(current.lines) == 2


def test_old_content_republished_after_a_restatement_becomes_current(tmp_path):
    a = load_export(FIXTURE)
    b = _republished(a, "aws-focus-123456789012-2026-10-v2", lines=a.lines[:1])
    a_again = _republished(a, "aws-focus-123456789012-2026-10-v3", lines=a.lines)
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(a)
        ledger.import_snapshot(b)
        result = ledger.import_snapshot(a_again)
        current = ledger.current_snapshot(a.snapshot.scope)

    assert result.outcome is ImportOutcome.NEW
    assert current is not None
    assert current.snapshot.snapshot_id == "aws-focus-123456789012-2026-10-v3"
    assert [str(line.BilledCost) for line in current.lines] == ["6.00", "4.00"]


def test_reimporting_a_superseded_snapshot_under_its_own_id_changes_nothing(tmp_path):
    # Pins current behaviour; #9 decides whether this should restore the old snapshot.
    a = load_export(FIXTURE)
    b = _republished(a, "aws-focus-123456789012-2026-10-v2", lines=a.lines[:1])
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(a)
        ledger.import_snapshot(b)
        result = ledger.import_snapshot(a)
        current = ledger.current_snapshot(a.snapshot.scope)

    assert result.outcome is ImportOutcome.REPLAY
    assert result.snapshot_id == "aws-focus-123456789012-2026-10-v1"
    assert current is not None
    assert current.snapshot.snapshot_id == "aws-focus-123456789012-2026-10-v2"


def _republished(
    export: FocusExport, snapshot_id: str, lines: list[FocusBillingLine]
) -> FocusExport:
    snapshot = export.snapshot.model_copy(update={"snapshot_id": snapshot_id})
    return FocusExport(snapshot=snapshot, lines=list(lines))
