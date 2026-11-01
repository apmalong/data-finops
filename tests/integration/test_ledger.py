"""Ledger behaviour through its public interface (seam agreed in #6)."""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from data_finops.billing.focus import load_export
from data_finops.contracts.billing import ExportSnapshot, FocusBillingLine, FocusExport
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
        export, "aws-focus-123456789012-2026-10-redelivered", export.lines, "2026-10-03T06:00:00Z"
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
    b = _republished(a, "aws-focus-123456789012-2026-10-v2", a.lines[:1], "2026-10-03T06:00:00Z")
    a_again = _republished(a, "aws-focus-123456789012-2026-10-v3", a.lines, "2026-10-04T06:00:00Z")
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
    # The current snapshot is the latest publication, so re-importing an older one
    # never restores it, whatever the import order.
    a = load_export(FIXTURE)
    b = _republished(a, "aws-focus-123456789012-2026-10-v2", a.lines[:1], "2026-10-03T06:00:00Z")
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(a)
        ledger.import_snapshot(b)
        result = ledger.import_snapshot(a)
        current = ledger.current_snapshot(a.snapshot.scope)

    assert result.outcome is ImportOutcome.REPLAY
    assert result.snapshot_id == "aws-focus-123456789012-2026-10-v1"
    assert current is not None
    assert current.snapshot.snapshot_id == "aws-focus-123456789012-2026-10-v2"


def test_previous_snapshot_stays_readable_by_id_after_a_restatement(tmp_path):
    a = load_export(FIXTURE)
    b = _republished(a, "aws-focus-123456789012-2026-10-v2", a.lines[:1], "2026-10-03T06:00:00Z")
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(a)
        ledger.import_snapshot(b)
        current = ledger.current_snapshot(a.snapshot.scope)
        previous = ledger.snapshot("aws-focus-123456789012-2026-10-v1")

    assert current is not None and previous is not None
    assert [str(line.BilledCost) for line in current.lines] == ["6.00"]
    assert [str(line.BilledCost) for line in previous.lines] == ["6.00", "4.00"]
    assert previous.snapshot.published_at.isoformat() == "2026-10-02T06:00:00+00:00"


def test_late_arriving_older_publication_is_kept_but_not_current(tmp_path):
    a = load_export(FIXTURE)
    b = _republished(a, "aws-focus-123456789012-2026-10-v2", a.lines[:1], "2026-10-03T06:00:00Z")
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(b)
        result = ledger.import_snapshot(a)
        current = ledger.current_snapshot(a.snapshot.scope)
        kept = ledger.snapshot("aws-focus-123456789012-2026-10-v1")

    assert result.outcome is ImportOutcome.SUPERSEDED
    assert current is not None and kept is not None
    assert current.snapshot.snapshot_id == "aws-focus-123456789012-2026-10-v2"
    assert [str(line.BilledCost) for line in kept.lines] == ["6.00", "4.00"]


@pytest.mark.parametrize("order", [("x", "y"), ("y", "x")])
def test_simultaneous_publications_resolve_to_the_same_current_in_any_order(tmp_path, order):
    a = load_export(FIXTURE)
    exports = {
        "x": _republished(
            a, "aws-focus-123456789012-2026-10-x", a.lines[:1], "2026-10-03T06:00:00Z"
        ),
        "y": _republished(a, "aws-focus-123456789012-2026-10-y", a.lines, "2026-10-03T06:00:00Z"),
    }
    with Ledger(tmp_path / "ledger.db") as ledger:
        for key in order:
            ledger.import_snapshot(exports[key])
        current = ledger.current_snapshot(a.snapshot.scope)

    assert current is not None
    assert current.snapshot.snapshot_id == "aws-focus-123456789012-2026-10-y"


def test_restating_one_scope_leaves_other_scopes_untouched(tmp_path):
    a = load_export(FIXTURE)
    other_source = _republished(
        a,
        "other-source-2026-10-v1",
        a.lines,
        "2026-10-02T06:00:00Z",
        source="aws-data-exports:FOCUS_1_2_AWS:second-export",
    )
    b = _republished(a, "aws-focus-123456789012-2026-10-v2", a.lines[:1], "2026-10-03T06:00:00Z")
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(a)
        ledger.import_snapshot(other_source)
        ledger.import_snapshot(b)
        untouched = ledger.current_snapshot(other_source.snapshot.scope)

    assert untouched is not None
    assert untouched.snapshot.snapshot_id == "other-source-2026-10-v1"
    assert [str(line.BilledCost) for line in untouched.lines] == ["6.00", "4.00"]


def test_publication_order_respects_fractional_seconds(tmp_path):
    a = load_export(FIXTURE)
    earlier = _republished(
        a, "aws-focus-123456789012-2026-10-e", a.lines[:1], "2026-10-03T06:00:00Z"
    )
    later = _republished(a, "aws-focus-123456789012-2026-10-d", a.lines, "2026-10-03T06:00:00.500Z")
    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(later)
        ledger.import_snapshot(earlier)
        current = ledger.current_snapshot(a.snapshot.scope)

    assert current is not None
    assert current.snapshot.snapshot_id == "aws-focus-123456789012-2026-10-d"


def _republished(
    export: FocusExport,
    snapshot_id: str,
    lines: list[FocusBillingLine],
    published_at: str,
    source: str | None = None,
) -> FocusExport:
    snapshot = ExportSnapshot.model_validate(
        {
            **export.snapshot.model_dump(mode="json"),
            "snapshot_id": snapshot_id,
            "published_at": published_at,
            "source": source or export.snapshot.source,
        }
    )
    return FocusExport(snapshot=snapshot, lines=list(lines))
