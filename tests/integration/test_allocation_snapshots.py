"""Allocation Snapshots in the Ledger (#87; spec 03 step H, M4 structure 4; ADR 0018).

Tested only through the Ledger's public interface on a temporary SQLite file. The
Calculation Run (#88) supplies the run ID and input digest; the Ledger keeps every
snapshot and one current snapshot per Snapshot Scope.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from data_finops.contracts.allocation import (
    Allocation,
    AllocationCategory,
    Confidence,
    TargetType,
)
from data_finops.contracts.billing import CostBasis, SnapshotScope
from data_finops.ledger.ledger import (
    AllocationSnapshotConflictError,
    AllocationStoreOutcome,
    AllocationStoreResult,
    Ledger,
)

SCOPE = SnapshotScope(
    billing_account_id="123456789012",
    billing_period_start=datetime(2026, 10, 1, tzinfo=UTC),
    billing_period_end=datetime(2026, 11, 1, tzinfo=UTC),
    source="aws-data-exports:FOCUS_1_2_AWS",
)
OTHER_SCOPE = SCOPE.model_copy(update={"billing_account_id": "210987654321"})


def _allocations(run: str, amount: str = "6.00") -> list[Allocation]:
    return [
        Allocation(
            billing_line_ref="aws-focus-123456789012-2026-10-v1#0",
            pool_id="athena-on-demand:123456789012:us-east-1:demo:2026-10-01",
            target_type=TargetType.QUERY_EXECUTION,
            target_id="q-001",
            amount=Decimal(amount),
            currency="USD",
            cost_basis=CostBasis.BILLED_COST,
            category=AllocationCategory.DIRECT,
            method="estimated_metered_share",
            confidence=Confidence.METERED_ESTIMATE,
            coverage=Decimal("1"),
            policy_version="engine/1",
            calculation_run_id=run,
            evidence_refs=["athena:q-001"],
        ),
        Allocation(
            billing_line_ref="aws-focus-123456789012-2026-10-v1#1",
            pool_id="ineligible",
            target_type=TargetType.UNALLOCATED,
            target_id="unallocated",
            amount=Decimal("4.00"),
            currency="USD",
            cost_basis=CostBasis.BILLED_COST,
            category=AllocationCategory.UNALLOCATED,
            method="ineligible",
            policy_version="engine/1",
            calculation_run_id=run,
        ),
    ]


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "data-finops.sqlite") as opened:
        yield opened


def _store(
    ledger: Ledger, run: str, digest: str, scope: SnapshotScope = SCOPE, amount: str = "6.00"
) -> AllocationStoreResult:
    return ledger.store_allocations(
        scope,
        calculation_run_id=run,
        policy_version="engine/1",
        input_digest=digest,
        allocations=_allocations(run, amount),
    )


def test_a_new_digest_is_stored_and_becomes_current(ledger: Ledger) -> None:
    result = _store(ledger, "run-1", "digest-1")
    assert (result.outcome, result.calculation_run_id) == (AllocationStoreOutcome.NEW, "run-1")
    current = ledger.current_allocations(SCOPE)
    assert current is not None
    assert current.allocations == _allocations("run-1")
    snapshot = current.snapshot
    assert (snapshot.calculation_run_id, snapshot.policy_version, snapshot.input_digest) == (
        "run-1",
        "engine/1",
        "digest-1",
    )
    assert snapshot.is_current


def _current_run(ledger: Ledger, scope: SnapshotScope = SCOPE) -> str | None:
    current = ledger.current_allocations(scope)
    return None if current is None else current.snapshot.calculation_run_id


def test_the_same_digest_again_stores_nothing_and_reports_a_replay(ledger: Ledger) -> None:
    _store(ledger, "run-1", "digest-1")
    result = _store(ledger, "run-2", "digest-1", amount="9.99")
    assert (result.outcome, result.calculation_run_id) == (AllocationStoreOutcome.REPLAY, "run-1")
    assert ledger.allocation_snapshot("run-2") is None
    current = ledger.current_allocations(SCOPE)
    assert current is not None and current.allocations == _allocations("run-1")


def test_a_new_digest_moves_the_current_pointer_and_keeps_earlier_snapshots(
    ledger: Ledger,
) -> None:
    _store(ledger, "run-1", "digest-1")
    result = _store(ledger, "run-2", "digest-2", amount="5.00")
    assert (result.outcome, _current_run(ledger)) == (AllocationStoreOutcome.NEW, "run-2")
    earlier = ledger.allocation_snapshot("run-1")
    assert earlier is not None
    assert earlier.allocations == _allocations("run-1")
    assert not earlier.snapshot.is_current
    later = ledger.allocation_snapshot("run-2")
    assert later is not None and later.snapshot.is_current
    assert later.allocations == _allocations("run-2", "5.00")


def test_returning_to_earlier_inputs_makes_their_snapshot_current_again(ledger: Ledger) -> None:
    _store(ledger, "run-1", "digest-1")
    _store(ledger, "run-2", "digest-2")
    result = _store(ledger, "run-3", "digest-1")
    assert (result.outcome, result.calculation_run_id) == (AllocationStoreOutcome.REPLAY, "run-1")
    assert _current_run(ledger) == "run-1"
    assert ledger.allocation_snapshot("run-3") is None


def test_each_scope_has_its_own_current_snapshot(ledger: Ledger) -> None:
    _store(ledger, "run-1", "digest-1")
    # The same inputs digest in another scope is another snapshot.
    result = _store(ledger, "run-2", "digest-1", scope=OTHER_SCOPE)
    assert result.outcome == AllocationStoreOutcome.NEW
    assert (_current_run(ledger), _current_run(ledger, OTHER_SCOPE)) == ("run-1", "run-2")


def test_nothing_is_current_for_a_scope_never_calculated(ledger: Ledger) -> None:
    assert ledger.current_allocations(SCOPE) is None
    assert ledger.allocation_snapshot("run-1") is None


def test_a_run_id_stored_with_other_inputs_is_refused(ledger: Ledger) -> None:
    _store(ledger, "run-1", "digest-1")
    with pytest.raises(AllocationSnapshotConflictError, match="run-1"):
        _store(ledger, "run-1", "digest-2")
    assert ledger.allocation_snapshot("run-1") is not None
    assert _current_run(ledger) == "run-1"


def test_allocations_naming_another_run_are_refused_and_nothing_is_stored(
    ledger: Ledger,
) -> None:
    with pytest.raises(ValueError, match="run-other"):
        ledger.store_allocations(
            SCOPE,
            calculation_run_id="run-1",
            policy_version="engine/1",
            input_digest="digest-1",
            allocations=_allocations("run-1")[:1] + _allocations("run-other")[1:],
        )
    assert ledger.allocation_snapshot("run-1") is None
    assert ledger.current_allocations(SCOPE) is None


def test_snapshots_are_read_back_exactly_after_reopening(tmp_path: Path) -> None:
    path = tmp_path / "data-finops.sqlite"
    with Ledger(path) as ledger:
        _store(ledger, "run-1", "digest-1", amount="6.000")
        stored = ledger.allocation_snapshot("run-1")
    with Ledger(path) as reopened:
        current = reopened.current_allocations(SCOPE)
    assert stored is not None and current == stored
    # Amounts keep their scale, not just their value.
    assert current.allocations[0].model_dump(mode="json")["amount"] == "6.000"
