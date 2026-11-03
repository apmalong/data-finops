"""Answers from the Ledger for the API (spec 06), opened per request on a database file."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Self

from data_finops.attribution.engine import AllocationMethod
from data_finops.contracts.allocation import Allocation, AllocationCategory, Confidence
from data_finops.contracts.billing import CostBasis, SnapshotScope, snapshot_id_of
from data_finops.ledger import Ledger, StoredAllocations
from data_finops.reporting.models import (
    AllocatedAmount,
    CostSummaries,
    CostSummary,
    CurrencySummary,
    ServedSnapshot,
    UnallocatedAmount,
)


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
        billing_period: date | None = None,
    ) -> CostSummaries:
        """A Cost Summary for the current Allocation Snapshot of each matching scope.

        `billing_period` is the Billing Period's first day. The Cost Basis is validated at
        runtime, so an unknown name raises ValueError.
        """
        basis = CostBasis(cost_basis)
        summaries: list[CostSummary] = []
        uncalculated: list[SnapshotScope] = []
        for scope in self._ledger.scopes():
            if billing_account_id not in (None, scope.billing_account_id) or billing_period not in (
                None,
                scope.billing_period_start.date(),
            ):
                continue
            stored = self._ledger.current_allocations(scope)
            if stored is None:
                uncalculated.append(scope)
            else:
                summaries.append(self._summary(scope, stored, basis))
        return CostSummaries(summaries=summaries, uncalculated_scopes=uncalculated)

    def _summary(
        self, scope: SnapshotScope, stored: StoredAllocations, basis: CostBasis
    ) -> CostSummary:
        snapshot = stored.snapshot
        allocations = [a for a in stored.allocations if a.cost_basis == basis]
        billed = self._billed(stored, basis)
        by_currency: dict[str, list[Allocation]] = defaultdict(list)
        for allocation in allocations:
            by_currency[allocation.currency].append(allocation)
        return CostSummary(
            scope=scope,
            cost_basis=basis,
            snapshot=ServedSnapshot(
                calculation_run_id=snapshot.calculation_run_id,
                policy_version=snapshot.policy_version,
                created_at=snapshot.created_at,
                is_current=snapshot.is_current,
                export_snapshot_id=snapshot.export_snapshot_id,
            ),
            currencies=[
                _currency_summary(currency, billed.get(currency, Decimal(0)), by_currency[currency])
                for currency in sorted(billed.keys() | by_currency.keys())
            ],
        )

    def _billed(self, stored: StoredAllocations, basis: CostBasis) -> dict[str, Decimal]:
        """The bill the snapshot partitions: every line of the Export Snapshot its run read.

        Snapshots stored before M5 record none; their Allocations' line references name it.
        """
        recorded = stored.snapshot.export_snapshot_id
        snapshot_ids = (
            {recorded}
            if recorded is not None
            else {snapshot_id_of(a.billing_line_ref) for a in stored.allocations}
        )
        billed: dict[str, Decimal] = defaultdict(Decimal)
        for snapshot_id in sorted(snapshot_ids):
            export = self._ledger.snapshot(snapshot_id)
            for line in export.lines if export is not None else []:
                billed[line.BillingCurrency] += line.cost(basis)
        return billed


def _currency_summary(
    currency: str, billed: Decimal, allocations: list[Allocation]
) -> CurrencySummary:
    allocated: dict[tuple[AllocationCategory, Confidence | None], Decimal] = defaultdict(Decimal)
    unallocated: dict[AllocationMethod, Decimal] = defaultdict(Decimal)
    adjustments = Decimal(0)
    for a in allocations:
        if a.category is not AllocationCategory.UNALLOCATED:
            allocated[(a.category, a.confidence)] += a.amount
        elif a.method == AllocationMethod.ADJUSTMENT:
            adjustments += a.amount
        else:
            unallocated[AllocationMethod(a.method)] += a.amount
    return CurrencySummary(
        currency=currency,
        billed=billed,
        allocated=[
            AllocatedAmount(category=category, confidence=confidence, amount=amount)
            for (category, confidence), amount in sorted(
                allocated.items(), key=lambda item: (item[0][0], item[0][1] or "")
            )
        ],
        unallocated=[
            UnallocatedAmount(reason=reason, amount=amount)
            for reason, amount in sorted(unallocated.items())
        ],
        adjustments=adjustments,
        reconciliation_delta=billed - sum((a.amount for a in allocations), Decimal(0)),
    )
