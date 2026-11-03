"""The Calculation Run (#88; spec 03 steps B, E and H, M4 structure 2 and 4).

Driven by fixture files on a temporary SQLite file; asserts only on run results and
snapshots read back. The hand-written fixtures give the golden scenario
`existing-fixtures-complete-coverage`.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from data_finops import calculation
from data_finops.billing.focus import load_export
from data_finops.calculation import run_calculation
from data_finops.contracts.allocation import Allocation
from data_finops.contracts.billing import CostBasis, FocusBillingLine, FocusExport
from data_finops.ledger import AllocationStoreOutcome, Ledger
from data_finops.workload.store import WorkloadStore

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
EXPORT = load_export(FIXTURES / "focus" / "export-2026-10.json")
SCOPE = EXPORT.snapshot.scope
ACCOUNT, REGION = "123456789012", "us-east-1"
GOLDEN = next(
    s
    for s in json.loads((FIXTURES / "golden" / "scenarios.json").read_text())
    if s["id"] == "existing-fixtures-complete-coverage"
)


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[tuple[Ledger, WorkloadStore]]:
    path = tmp_path / "data-finops.sqlite"
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        ledger.import_snapshot(EXPORT)
        for name in ("query-executions.json", "link-outcomes.json"):
            store.ingest_query_history(
                FIXTURES / "athena" / name, account_id=ACCOUNT, region=REGION
            )
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "run_results.json")
        # With the events, q-001's Evidence names its Workload Runs as the oracle lists (#90).
        store.ingest_events(FIXTURES / "openlineage" / "events.json")
        yield ledger, store


def _query_history(tmp_path: Path, query_id: str, **changes: Any) -> Path:
    """A one-query history file: q-001 from the fixture, renamed and changed."""
    history = json.loads((FIXTURES / "athena" / "query-executions.json").read_text())
    query = history["QueryExecutions"][0] | {"QueryExecutionId": query_id}
    for path, value in changes.items():
        section, _, field = path.partition("__")  # `Status__State` is a nested field
        query[section] = query[section] | {field: value} if field else value
    history["QueryExecutions"] = [query]
    target = tmp_path / f"{query_id}.json"
    target.write_text(json.dumps(history), encoding="utf-8")
    return target


def _export(**line_changes: Any) -> FocusExport:
    """A restatement of the fixture export: later publication, Athena line changed."""
    snapshot = EXPORT.snapshot.model_copy(
        update={
            "snapshot_id": "aws-focus-123456789012-2026-10-v2",
            "published_at": datetime(2026, 10, 3, tzinfo=UTC),
        }
    )
    athena = FocusBillingLine.model_validate(EXPORT.lines[0].model_dump() | line_changes)
    return EXPORT.model_copy(update={"snapshot": snapshot, "lines": [athena, *EXPORT.lines[1:]]})


def _billed(ledger: Ledger, run: str) -> list[dict[str, Any]]:
    """BilledCost Allocations of a stored snapshot, without run and policy identifiers."""
    stored = ledger.allocation_snapshot(run)
    assert stored is not None
    return sorted(
        (
            a.model_dump(mode="json", exclude={"calculation_run_id", "policy_version"})
            for a in stored.allocations
            if a.cost_basis == CostBasis.BILLED_COST
        ),
        key=lambda a: (a["billing_line_ref"], a["target_id"]),
    )


def _golden() -> list[dict[str, Any]]:
    return sorted(
        (
            Allocation.model_validate(a).model_dump(
                mode="json", exclude={"calculation_run_id", "policy_version"}
            )
            for a in GOLDEN["allocations"]
        ),
        key=lambda a: (a["billing_line_ref"], a["target_id"]),
    )


def test_a_first_run_stores_the_golden_allocations(
    stores: tuple[Ledger, WorkloadStore],
) -> None:
    ledger, store = stores
    result = run_calculation(ledger, store, SCOPE, new_run_id=lambda: "calc-1")
    assert (result.outcome, result.calculation_run_id) == (AllocationStoreOutcome.NEW, "calc-1")
    assert result.ineligible_lines == ("aws-focus-123456789012-2026-10-v1#1",)
    assert result.unknown_billable_usage == ()
    assert _billed(ledger, "calc-1") == _golden()
    current = ledger.current_allocations(SCOPE)
    assert current is not None
    assert current.snapshot.calculation_run_id == result.calculation_run_id
    assert current.snapshot.input_digest == result.input_digest
    assert {a.policy_version for a in current.allocations} == {calculation.POLICY_VERSION}


def test_the_same_inputs_again_are_a_replay(stores: tuple[Ledger, WorkloadStore]) -> None:
    ledger, store = stores
    first = run_calculation(ledger, store, SCOPE, new_run_id=lambda: "calc-1")
    again = run_calculation(ledger, store, SCOPE, new_run_id=lambda: "calc-2")
    # The replay reports the snapshot it reproduced, not the ID it was offered.
    assert (again.outcome, again.calculation_run_id, again.input_digest) == (
        AllocationStoreOutcome.REPLAY,
        "calc-1",
        first.input_digest,
    )
    assert ledger.allocation_snapshot("calc-2") is None


def test_a_restated_export_snapshot_yields_a_new_snapshot(
    stores: tuple[Ledger, WorkloadStore],
) -> None:
    ledger, store = stores
    first = run_calculation(ledger, store, SCOPE)
    restated = _export(BilledCost=Decimal("7.00"))
    ledger.import_snapshot(restated)
    second = run_calculation(ledger, store, SCOPE)
    assert second.outcome == AllocationStoreOutcome.NEW
    assert second.input_digest != first.input_digest
    athena = [a for a in _billed(ledger, second.calculation_run_id) if a["target_id"] == "q-001"]
    assert [(a["billing_line_ref"], a["amount"]) for a in athena] == [
        ("aws-focus-123456789012-2026-10-v2#0", "7.00")
    ]


def test_new_query_history_in_scope_yields_a_new_snapshot(
    stores: tuple[Ledger, WorkloadStore], tmp_path: Path
) -> None:
    ledger, store = stores
    first = run_calculation(ledger, store, SCOPE)
    # Another 600 MB in workgroup demo the same day: 0.0016 TB known against 0.001 TB billed,
    # beyond the export's half-unit tolerance (0.0005 TB).
    store.ingest_query_history(
        _query_history(tmp_path, "q-005", Statistics__DataScannedInBytes=600_000_000),
        account_id=ACCOUNT,
        region=REGION,
    )
    second = run_calculation(ledger, store, SCOPE)
    assert second.outcome == AllocationStoreOutcome.NEW
    assert second.input_digest != first.input_digest
    athena = [
        a
        for a in _billed(ledger, second.calculation_run_id)
        if a["billing_line_ref"].endswith("#0")
    ]
    assert [(a["target_id"], a["method"], a["coverage"]) for a in athena] == [
        ("unallocated", "coverage_exceeded", "1.6")
    ]


def test_query_history_out_of_scope_changes_nothing(
    stores: tuple[Ledger, WorkloadStore], tmp_path: Path
) -> None:
    ledger, store = stores
    first = run_calculation(ledger, store, SCOPE)
    store.ingest_query_history(
        _query_history(tmp_path, "q-006", Status__CompletionDateTime="2026-10-05T00:00:00Z"),
        account_id=ACCOUNT,
        region=REGION,
    )
    again = run_calculation(ledger, store, SCOPE)
    assert (again.outcome, again.calculation_run_id) == (
        AllocationStoreOutcome.REPLAY,
        first.calculation_run_id,
    )


def test_a_newly_linked_query_yields_a_new_snapshot_with_its_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data-finops.sqlite"
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        ledger.import_snapshot(EXPORT)
        store.ingest_query_history(
            FIXTURES / "athena" / "query-executions.json", account_id=ACCOUNT, region=REGION
        )
        before = run_calculation(ledger, store, SCOPE)
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "run_results.json")
        after = run_calculation(ledger, store, SCOPE)
        assert after.outcome == AllocationStoreOutcome.NEW
        evidence = {
            run: next(a["evidence_refs"] for a in _billed(ledger, run) if a["target_id"] == "q-001")
            for run in (before.calculation_run_id, after.calculation_run_id)
        }
    assert evidence == {
        before.calculation_run_id: ["athena:q-001"],
        after.calculation_run_id: ["athena:q-001", "dbt-query-comment:inv-001:model.demo.orders"],
    }


def test_a_new_policy_version_yields_a_new_snapshot(
    stores: tuple[Ledger, WorkloadStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, store = stores
    first = run_calculation(ledger, store, SCOPE)
    monkeypatch.setattr(calculation, "POLICY_VERSION", "allocation-engine/2+athena/1")
    second = run_calculation(ledger, store, SCOPE)
    assert second.outcome == AllocationStoreOutcome.NEW
    assert second.input_digest != first.input_digest
    stored = ledger.allocation_snapshot(second.calculation_run_id)
    assert stored is not None
    assert stored.snapshot.policy_version == "allocation-engine/2+athena/1"


def test_a_query_without_statistics_is_reported_and_left_out_of_known_usage(
    stores: tuple[Ledger, WorkloadStore], tmp_path: Path
) -> None:
    ledger, store = stores
    history = json.loads(_query_history(tmp_path, "q-007").read_text())
    del history["QueryExecutions"][0]["Statistics"]
    (tmp_path / "q-007.json").write_text(json.dumps(history), encoding="utf-8")
    first = run_calculation(ledger, store, SCOPE)
    store.ingest_query_history(tmp_path / "q-007.json", account_id=ACCOUNT, region=REGION)
    result = run_calculation(ledger, store, SCOPE)
    assert result.unknown_billable_usage == ("q-007",)
    # Nothing to allocate changed, but what the run knows did: a new snapshot.
    assert result.outcome == AllocationStoreOutcome.NEW
    assert result.input_digest != first.input_digest
    # q-001 still covers the line exactly; q-007 receives nothing.
    assert _billed(ledger, result.calculation_run_id) == _golden()


@pytest.mark.parametrize(
    ("changes", "method"),
    [
        ({"ChargeCategory": "Credit", "BilledCost": Decimal("-6.00")}, "adjustment"),
        ({"ChargeCategory": "Adjustment"}, "adjustment"),
        ({"ChargeCategory": "Tax"}, "ineligible"),
        ({"ChargeCategory": "Purchase"}, "ineligible"),
        ({"ChargeClass": "Correction"}, "ineligible"),
        ({"ConsumedQuantity": None}, "ineligible"),
        ({"ConsumedQuantity": Decimal("0")}, "ineligible"),
        ({"ConsumedUnit": "GB"}, "ineligible"),
        ({"ServiceName": "Amazon Redshift"}, "ineligible"),
    ],
    ids=[
        "credit",
        "adjustment",
        "tax",
        "purchase",
        "correction",
        "no-quantity",
        "zero-quantity",
        "unknown-unit",
        "other-service",
    ],
)
def test_lines_that_are_not_athena_on_demand_usage_stay_unallocated(
    stores: tuple[Ledger, WorkloadStore], changes: dict[str, Any], method: str
) -> None:
    ledger, store = stores
    ledger.import_snapshot(_export(**changes))
    result = run_calculation(ledger, store, SCOPE)
    athena = [
        a
        for a in _billed(ledger, result.calculation_run_id)
        if a["billing_line_ref"].endswith("v2#0")
    ]
    assert [(a["target_id"], a["method"]) for a in athena] == [("unallocated", method)]


def test_a_scope_without_an_export_snapshot_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "data-finops.sqlite"
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        with pytest.raises(LookupError, match="no Export Snapshot"):
            run_calculation(ledger, store, SCOPE)
        assert ledger.current_allocations(SCOPE) is None


def test_a_metered_line_without_a_usage_account_is_wholly_a_coverage_remainder(
    stores: tuple[Ledger, WorkloadStore],
) -> None:
    ledger, store = stores
    ledger.import_snapshot(_export(SubAccountId=None))
    result = run_calculation(ledger, store, SCOPE)
    athena = [
        a
        for a in _billed(ledger, result.calculation_run_id)
        if a["billing_line_ref"].endswith("v2#0")
    ]
    assert [(a["target_id"], a["method"], a["coverage"]) for a in athena] == [
        ("unallocated", "coverage_remainder", "0")
    ]


def test_query_states_bill_as_the_athena_policy_says(
    stores: tuple[Ledger, WorkloadStore], tmp_path: Path
) -> None:
    ledger, store = stores
    # Beside q-001's 1,000 MB: a cancelled query that scanned 25.4 MB bills 26 MB; failed,
    # still running and a 0-byte DDL bill nothing. 1,026 MB of 1,000 is within the
    # export's half-unit tolerance, so Coverage is 1 and the $6.00 splits 1000:26.
    for query_id, changes in {
        "q-cancelled": {"Status__State": "CANCELLED", "Statistics__DataScannedInBytes": 25_400_000},
        "q-failed": {"Status__State": "FAILED"},
        "q-ddl": {"StatementType": "DDL", "Statistics__DataScannedInBytes": 0},
    }.items():
        store.ingest_query_history(
            _query_history(tmp_path, query_id, **changes), account_id=ACCOUNT, region=REGION
        )
    running = json.loads(_query_history(tmp_path, "q-running", Status__State="RUNNING").read_text())
    del running["QueryExecutions"][0]["Status"]["CompletionDateTime"]
    (tmp_path / "q-running.json").write_text(json.dumps(running), encoding="utf-8")
    store.ingest_query_history(tmp_path / "q-running.json", account_id=ACCOUNT, region=REGION)
    result = run_calculation(ledger, store, SCOPE)
    athena = {
        a["target_id"]: a["amount"]
        for a in _billed(ledger, result.calculation_run_id)
        if a["billing_line_ref"].endswith("v1#0")
    }
    assert athena == {"q-001": "5.85", "q-cancelled": "0.15"}


def test_allocations_record_every_rule_version_that_shaped_them(
    stores: tuple[Ledger, WorkloadStore],
) -> None:
    ledger, store = stores
    result = run_calculation(ledger, store, SCOPE)
    stored = ledger.allocation_snapshot(result.calculation_run_id)
    assert stored is not None
    assert stored.snapshot.policy_version == (
        "allocation-engine/1+line-classification/1+athena-billable-usage/1+athena-billing-scope/1"
    )


def test_a_restatement_to_no_lines_yields_a_new_empty_snapshot(
    stores: tuple[Ledger, WorkloadStore],
) -> None:
    ledger, store = stores
    run_calculation(ledger, store, SCOPE)
    ledger.import_snapshot(_export().model_copy(update={"lines": []}))
    result = run_calculation(ledger, store, SCOPE)
    assert result.outcome == AllocationStoreOutcome.NEW
    stored = ledger.allocation_snapshot(result.calculation_run_id)
    assert stored is not None and stored.allocations == []
