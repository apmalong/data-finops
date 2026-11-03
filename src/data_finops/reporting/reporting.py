"""Answers from the Ledger for the API (spec 06), opened per request on a database file."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Self

from data_finops.contracts.allocation import Allocation, AllocationCategory
from data_finops.contracts.billing import CostBasis, SnapshotScope
from data_finops.ledger import Ledger, StoredAllocations
from data_finops.reporting.models import (
    AllocatedAmount,
    CostSummary,
    CurrencySummary,
    ServedSnapshot,
    UnallocatedAmount,
)

_ADJUSTMENT = "adjustment"
"""The Unallocated reason the engine gives Adjustment lines (spec 03)."""
_ZERO = Decimal("0.00")


class Reporting:
    """The read model over one database file."""

    def __init__(self, path: Path) -> None:
        self._ledger = Ledger(path)

    def close(self) -> None:
        self._ledger.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def cost_summary(
        self,
        *,
        cost_basis: CostBasis = CostBasis.BILLED_COST,
        billing_account_id: str | None = None,
        billing_period_start: datetime | None = None,
    ) -> CostSummary:
        """Where the current Allocation Snapshot of each matching scope put the bill.

        The Cost Basis is validated at runtime, so an unknown name raises ValueError.
        """
        basis = CostBasis(cost_basis)
        scopes = [
            scope
            for scope in self._ledger.scopes()
            if billing_account_id in (None, scope.billing_account_id)
            and billing_period_start in (None, scope.billing_period_start)
        ]
        served: list[tuple[SnapshotScope, StoredAllocations]] = []
        uncalculated: list[SnapshotScope] = []
        for scope in scopes:
            stored = self._ledger.current_allocations(scope)
            if stored is None:
                uncalculated.append(scope)
            else:
                served.append((scope, stored))
        allocations = [
            a for _, stored in served for a in stored.allocations if a.cost_basis == basis
        ]
        return CostSummary(
            cost_basis=basis,
            currencies=self._by_currency(allocations, basis),
            snapshots=[
                ServedSnapshot(
                    scope=scope,
                    calculation_run_id=stored.snapshot.calculation_run_id,
                    policy_version=stored.snapshot.policy_version,
                    created_at=stored.snapshot.created_at,
                    is_current=stored.snapshot.is_current,
                )
                for scope, stored in served
            ],
            uncalculated_scopes=uncalculated,
        )

    def _by_currency(
        self, allocations: list[Allocation], basis: CostBasis
    ) -> list[CurrencySummary]:
        billed = self._billed(allocations, basis)
        by_currency: dict[str, list[Allocation]] = defaultdict(list)
        for allocation in allocations:
            by_currency[allocation.currency].append(allocation)
        summaries = []
        for currency in sorted(billed.keys() | by_currency.keys()):
            allocated: dict[tuple[AllocationCategory, object], Decimal] = defaultdict(lambda: _ZERO)
            unallocated: dict[str, Decimal] = defaultdict(lambda: _ZERO)
            adjustments = _ZERO
            for a in by_currency[currency]:
                if a.category is not AllocationCategory.UNALLOCATED:
                    allocated[(a.category, a.confidence)] += a.amount
                elif a.method == _ADJUSTMENT:
                    adjustments += a.amount
                else:
                    unallocated[a.method] += a.amount
            total = sum((a.amount for a in by_currency[currency]), _ZERO)
            summaries.append(
                CurrencySummary(
                    currency=currency,
                    billed=billed.get(currency, _ZERO),
                    allocated=[
                        AllocatedAmount(category=category, confidence=confidence, amount=amount)
                        for (category, confidence), amount in sorted(
                            allocated.items(), key=lambda item: (item[0][0], str(item[0][1]))
                        )
                    ],
                    unallocated=[
                        UnallocatedAmount(reason=reason, amount=amount)
                        for reason, amount in sorted(unallocated.items())
                    ],
                    adjustments=adjustments,
                    reconciliation_delta=billed.get(currency, _ZERO) - total,
                )
            )
        return summaries

    def _billed(self, allocations: list[Allocation], basis: CostBasis) -> dict[str, Decimal]:
        """The bill the Allocations partition: every line of the Export Snapshots they read."""
        snapshot_ids = {a.billing_line_ref.rpartition("#")[0] for a in allocations}
        billed: dict[str, Decimal] = defaultdict(lambda: _ZERO)
        for snapshot_id in sorted(snapshot_ids):
            export = self._ledger.snapshot(snapshot_id)
            if export is None:
                continue
            for line in export.lines:
                billed[line.BillingCurrency] += line.cost(basis)
        return billed
