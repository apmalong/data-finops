"""Answers from the Ledger for the API (spec 06), opened per request on a database file."""

import sqlite3
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
    AllocationEntry,
    AllocationPage,
    CostSummaries,
    CostSummary,
    CurrencySummary,
    ServedSnapshot,
    UnallocatedAmount,
)
from data_finops.workload.store import UnusableDatabaseError, WorkloadStore

MAX_PAGE_SIZE = 1000
"""The largest page a read serves (spec 06)."""


class NotFound(LookupError):
    """Nothing matches: an unknown Calculation Run, or no calculated scope for the filters."""


class AmbiguousScope(ValueError):
    """The filters match several Snapshot Scopes where a read serves one snapshot."""


class ConflictingFilters(ValueError):
    """A named Calculation Run together with scope filters: the run already fixes its scope."""


class DatabaseUnavailable(Exception):
    """The file cannot be opened as a current data-finops database; never names the file."""


class Reporting:
    """The read model over one database file."""

    def __init__(self, path: Path) -> None:
        """Open both stores on an existing file; raises DatabaseUnavailable if there is no
        file or either store refuses it, so a wrong path is reported rather than created."""
        if not path.is_file():
            raise DatabaseUnavailable
        try:
            self._ledger = Ledger(path)
        except sqlite3.DatabaseError:
            raise DatabaseUnavailable from None
        try:
            # Held open from the start, so a stale workload schema is reported here, by
            # `/health`, rather than by the first read that needs the store.
            self._store = WorkloadStore.open_existing(path)
        except UnusableDatabaseError:
            self._ledger.close()
            raise DatabaseUnavailable from None

    def close(self) -> None:
        self._store.close()
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
        for scope in self._scopes(billing_account_id, billing_period):
            stored = self._ledger.current_allocations(scope)
            if stored is None:
                uncalculated.append(scope)
            else:
                summaries.append(self._summary(scope, stored, basis))
        return CostSummaries(summaries=summaries, uncalculated_scopes=uncalculated)

    def allocations(
        self,
        *,
        calculation_run_id: str | None = None,
        billing_account_id: str | None = None,
        billing_period: date | None = None,
        source: str | None = None,
        target_id: str | None = None,
        category: AllocationCategory | None = None,
        billing_line_ref: str | None = None,
        cost_basis: CostBasis | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> AllocationPage:
        """One page of one Allocation Snapshot's Allocations, in stored order.

        The snapshot is `calculation_run_id`'s (ConflictingFilters if scope filters are also
        given), or else the current one of the single Snapshot Scope the filters select:
        NotFound if none is calculated, AmbiguousScope if several are. `limit` is 1 to
        MAX_PAGE_SIZE and `offset` at least 0, else ValueError.
        """
        if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
            raise ValueError(f"limit must be 1 to {MAX_PAGE_SIZE} and offset at least 0")
        stored = self._served(calculation_run_id, billing_account_id, billing_period, source)
        run_id = stored.snapshot.calculation_run_id
        matching = [
            AllocationEntry(ref=f"{run_id}#{position}", allocation=allocation)
            for position, allocation in enumerate(stored.allocations)
            if target_id in (None, allocation.target_id)
            and category in (None, allocation.category)
            and billing_line_ref in (None, allocation.billing_line_ref)
            and cost_basis in (None, allocation.cost_basis)
        ]
        return AllocationPage(
            calculation_run_id=run_id,
            total=len(matching),
            limit=limit,
            offset=offset,
            items=matching[offset : offset + limit],
        )

    def _served(
        self,
        calculation_run_id: str | None,
        billing_account_id: str | None,
        billing_period: date | None,
        source: str | None,
    ) -> StoredAllocations:
        """The one Allocation Snapshot a read serves (spec 06)."""
        if calculation_run_id is not None:
            if (billing_account_id, billing_period, source) != (None, None, None):
                raise ConflictingFilters("a named Calculation Run fixes its own scope")
            named = self._ledger.allocation_snapshot(calculation_run_id)
            if named is None:
                raise NotFound("no such Calculation Run")
            return named
        current = [
            stored
            for scope in self._scopes(billing_account_id, billing_period, source)
            if (stored := self._ledger.current_allocations(scope)) is not None
        ]
        if not current:
            raise NotFound("no calculated Snapshot Scope matches")
        if len(current) > 1:
            raise AmbiguousScope("several Snapshot Scopes match")
        return current[0]

    def _scopes(
        self,
        billing_account_id: str | None,
        billing_period: date | None,
        source: str | None = None,
    ) -> list[SnapshotScope]:
        return [
            scope
            for scope in self._ledger.scopes()
            if billing_account_id in (None, scope.billing_account_id)
            and billing_period in (None, scope.billing_period_start.date())
            and source in (None, scope.source)
        ]

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
