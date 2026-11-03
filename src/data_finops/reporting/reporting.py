"""Answers from the Ledger for the API (spec 06), opened per request on a database file."""

import sqlite3
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Self, get_args

from data_finops.attribution.engine import AllocationMethod
from data_finops.attribution.rollups import (
    RollupTotal,
    model_execution_of,
    model_execution_rollups,
    query_execution_of,
    workload_run_rollups,
    workload_runs_of,
)
from data_finops.contracts.allocation import Allocation, AllocationCategory, Confidence
from data_finops.contracts.billing import (
    CostBasis,
    FocusBillingLine,
    SnapshotScope,
    parse_line_ref,
    snapshot_id_of,
)
from data_finops.contracts.workload import QueryExecution
from data_finops.identity.links import ModelExecutionKey
from data_finops.ledger import Ledger, StoredAllocations
from data_finops.reporting.models import (
    AllocatedAmount,
    AllocationCurrencyReconciliation,
    AllocationEntry,
    AllocationLineReconciliation,
    AllocationPage,
    AllocationReconciliation,
    CostExplanation,
    CostSummaries,
    CostSummary,
    CurrencySummary,
    ExplainedModelExecution,
    ExplainedRollup,
    ExplainedWorkloadRun,
    Provenance,
    ReadExportSnapshot,
    RollupAmount,
    RollupEntry,
    RollupLevel,
    RollupPage,
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
        _check_page(limit, offset)
        stored = self._served(calculation_run_id, billing_account_id, billing_period, source)
        run_id = stored.snapshot.calculation_run_id
        matching = [
            AllocationEntry(ref=_allocation_ref(run_id, position), allocation=allocation)
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

    def rollups(
        self,
        *,
        level: RollupLevel,
        calculation_run_id: str | None = None,
        billing_account_id: str | None = None,
        billing_period: date | None = None,
        source: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> RollupPage:
        """One page of one Allocation Snapshot's Roll-ups at `level`, ordered by key: what
        each Model Execution or Workload Run cost, per currency and Cost Basis (ADR 0016).

        The snapshot is chosen as for `allocations`; ValueError for an unknown level or a
        page out of bounds.
        """
        if level not in _ROLLUPS:
            raise ValueError(f"level must be one of {', '.join(get_args(RollupLevel))}")
        _check_page(limit, offset)
        stored = self._served(calculation_run_id, billing_account_id, billing_period, source)
        entries = sorted(
            (
                RollupEntry(
                    key=key,
                    totals=[
                        RollupAmount(currency=t.currency, cost_basis=t.cost_basis, amount=t.amount)
                        for t in totals
                    ],
                )
                for key, totals in _ROLLUPS[level](stored.allocations).items()
            ),
            key=lambda entry: entry.key,
        )
        return RollupPage(
            calculation_run_id=stored.snapshot.calculation_run_id,
            level=level,
            total=len(entries),
            limit=limit,
            offset=offset,
            items=entries[offset : offset + limit],
        )

    def reconciliation(
        self,
        *,
        calculation_run_id: str | None = None,
        billing_account_id: str | None = None,
        billing_period: date | None = None,
        source: str | None = None,
        cost_basis: CostBasis | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> AllocationReconciliation:
        """Allocations against the bill they partition, per Billing Line and Cost Basis, with
        per-currency totals over every line; one page of the lines (GLOSSARY Reconciliation).

        The snapshot is chosen as for `allocations`. Every line of the Export Snapshot the
        run read appears, allocated or not, so nothing missing can hide.
        """
        _check_page(limit, offset)
        stored = self._served(calculation_run_id, billing_account_id, billing_period, source)
        bases = [cost_basis] if cost_basis is not None else list(CostBasis)
        # Keyed by line, basis and currency, from the bill and from the Allocations alike, so
        # an amount allocated outside the bill or in another currency is billed zero and shows.
        billed: dict[tuple[str, CostBasis, str], Decimal] = defaultdict(Decimal)
        allocated: dict[tuple[str, CostBasis, str], Decimal] = defaultdict(Decimal)
        for ref, line in self._bill(stored):
            for basis in bases:
                billed[(ref, basis, line.BillingCurrency)] += line.cost(basis)
        for allocation in stored.allocations:
            basis = CostBasis(allocation.cost_basis)
            if basis in bases:
                key = (allocation.billing_line_ref, basis, allocation.currency)
                allocated[key] += allocation.amount
        rows = [
            AllocationLineReconciliation(
                billing_line_ref=ref,
                currency=currency,
                cost_basis=basis,
                billed=billed[(ref, basis, currency)],
                allocated_amount=allocated[(ref, basis, currency)],
                reconciliation_delta=billed[(ref, basis, currency)]
                - allocated[(ref, basis, currency)],
            )
            for ref, basis, currency in sorted(billed.keys() | allocated.keys())
        ]
        return AllocationReconciliation(
            calculation_run_id=stored.snapshot.calculation_run_id,
            export_snapshot_id=stored.snapshot.export_snapshot_id,
            totals=_totals(rows),
            total=len(rows),
            limit=limit,
            offset=offset,
            items=rows[offset : offset + limit],
        )

    def provenance(
        self,
        *,
        calculation_run_id: str | None = None,
        billing_account_id: str | None = None,
        billing_period: date | None = None,
        source: str | None = None,
    ) -> Provenance:
        """How one Allocation Snapshot was made, chosen as for `allocations`: its inputs and
        policies, the Export Snapshot it read, and whether a later run superseded it."""
        stored = self._served(calculation_run_id, billing_account_id, billing_period, source)
        snapshot = stored.snapshot
        export_id = snapshot.export_snapshot_id or next(
            (snapshot_id_of(a.billing_line_ref) for a in stored.allocations), None
        )
        export = self._ledger.snapshot(export_id) if export_id else None
        digest = self._ledger.content_digest(export_id) if export_id else None
        scope = export.snapshot.scope if export is not None else None
        return Provenance(
            calculation_run_id=snapshot.calculation_run_id,
            scope=scope,
            policy_version=snapshot.policy_version,
            policy_components=snapshot.policy_version.split("+"),
            input_digest=snapshot.input_digest,
            created_at=snapshot.created_at,
            is_current=snapshot.is_current,
            superseded_by=self._superseded_by(snapshot.calculation_run_id, scope),
            export_snapshot=(
                None
                if export is None or digest is None
                else ReadExportSnapshot(
                    snapshot_id=export.snapshot.snapshot_id,
                    content_digest=digest,
                    published_at=export.snapshot.published_at,
                )
            ),
        )

    def _superseded_by(self, calculation_run_id: str, scope: SnapshotScope | None) -> str | None:
        """The next Allocation Snapshot stored for the scope after this one, if any."""
        runs = [
            s.calculation_run_id
            for s in (self._ledger.allocation_snapshots(scope) if scope else [])
        ]
        position = runs.index(calculation_run_id) if calculation_run_id in runs else len(runs)
        return runs[position + 1] if position + 1 < len(runs) else None

    def explanation(self, ref: str) -> CostExplanation:
        """The Cost Explanation of the Allocation at `ref` (`<calculation_run_id>#<position>`),
        current or superseded; NotFound for an unknown or malformed reference."""
        parsed = _parse_allocation_ref(ref)
        stored = self._ledger.allocation_snapshot(parsed[0]) if parsed else None
        if parsed is None or stored is None or parsed[1] >= len(stored.allocations):
            raise NotFound("no such Allocation")
        allocation = stored.allocations[parsed[1]]
        line = self._billing_line(allocation.billing_line_ref)
        model = model_execution_of(allocation)
        runs = workload_runs_of(allocation)
        return CostExplanation(
            ref=ref,
            billing_line_ref=allocation.billing_line_ref,
            billing_line=line,
            allocation=allocation,
            unallocated_reason=(
                allocation.method if allocation.category is AllocationCategory.UNALLOCATED else None
            ),
            query_execution=self._query_execution(allocation, line),
            model_execution=None if model is None else self._model_execution(model),
            workload_runs=[
                ExplainedWorkloadRun(run_id=run, run=self._store.workload_run(run)) for run in runs
            ],
            rollups=_rollups_of(allocation, model, runs, stored.allocations),
        )

    def _billing_line(self, line_ref: str) -> FocusBillingLine:
        """The stored Billing Line an Allocation partitions; a dangling reference is an
        integrity fault in the Ledger, not a missing resource."""
        parsed = parse_line_ref(line_ref)
        export = self._ledger.snapshot(parsed[0]) if parsed else None
        if parsed is None or export is None or parsed[1] >= len(export.lines):
            raise RuntimeError("an Allocation references a Billing Line the Ledger lacks")
        return export.lines[parsed[1]]

    def _query_execution(
        self, allocation: Allocation, line: FocusBillingLine
    ) -> QueryExecution | None:
        """The Query Execution the Allocation's Evidence names, in the line's usage account
        and region (spec 03); None if the Evidence names none or it is not stored."""
        query_execution_id = query_execution_of(allocation)
        if query_execution_id is None or line.SubAccountId is None or line.RegionId is None:
            return None
        return self._store.query_execution(line.SubAccountId, line.RegionId, query_execution_id)

    def _model_execution(self, model: ModelExecutionKey) -> ExplainedModelExecution:
        invocation_id, node_id = model
        return ExplainedModelExecution(
            invocation_id=invocation_id,
            node_id=node_id,
            execution=next(
                (m for m in self._store.model_executions(invocation_id) if m.node_id == node_id),
                None,
            ),
            dbt_node=next(
                (n for n in self._store.manifest_nodes(invocation_id) if n.node_id == node_id),
                None,
            ),
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
        billed: dict[str, Decimal] = defaultdict(Decimal)
        for _, line in self._bill(stored):
            billed[line.BillingCurrency] += line.cost(basis)
        return billed

    def _bill(self, stored: StoredAllocations) -> list[tuple[str, FocusBillingLine]]:
        """The Billing Lines the snapshot partitions, by reference: every line of the Export
        Snapshot its run read. Snapshots stored before M5 record none; their Allocations'
        line references name it."""
        recorded = stored.snapshot.export_snapshot_id
        snapshot_ids = (
            {recorded}
            if recorded is not None
            else {snapshot_id_of(a.billing_line_ref) for a in stored.allocations}
        )
        bill = []
        for snapshot_id in sorted(snapshot_ids):
            export = self._ledger.snapshot(snapshot_id)
            if export is not None:
                bill += [(export.line_ref(i), line) for i, line in enumerate(export.lines)]
        return bill


def _totals(rows: list[AllocationLineReconciliation]) -> list[AllocationCurrencyReconciliation]:
    """Each currency and Cost Basis over every row; never across currencies."""
    groups: dict[tuple[str, CostBasis], list[AllocationLineReconciliation]] = defaultdict(list)
    for row in rows:
        groups[(row.currency, row.cost_basis)].append(row)
    totals = []
    for (currency, basis), group in sorted(groups.items()):
        billed = sum((row.billed for row in group), Decimal(0))
        allocated = sum((row.allocated_amount for row in group), Decimal(0))
        totals.append(
            AllocationCurrencyReconciliation(
                currency=currency,
                cost_basis=basis,
                billed=billed,
                allocated_amount=allocated,
                reconciliation_delta=billed - allocated,
            )
        )
    return totals


def _check_page(limit: int, offset: int) -> None:
    if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
        raise ValueError(f"limit must be 1 to {MAX_PAGE_SIZE} and offset at least 0")


_ROLLUPS: dict[RollupLevel, Callable[[list[Allocation]], dict[str, tuple[RollupTotal, ...]]]] = {
    "model_execution": lambda allocations: {
        key.target_id(): totals for key, totals in model_execution_rollups(allocations).items()
    },
    "workload_run": workload_run_rollups,
}


def _allocation_ref(calculation_run_id: str, position: int) -> str:
    """An Allocation's address, `<calculation_run_id>#<position>` (spec 06)."""
    return f"{calculation_run_id}#{position}"


def _parse_allocation_ref(ref: str) -> tuple[str, int] | None:
    run_id, _, position = ref.rpartition("#")
    return (run_id, int(position)) if run_id and position.isdigit() else None


def _rollups_of(
    allocation: Allocation,
    model: ModelExecutionKey | None,
    runs: list[str],
    allocations: list[Allocation],
) -> list[ExplainedRollup]:
    """The Roll-ups `allocation` contributes to, in its own currency and Cost Basis."""
    keys: dict[RollupLevel, list[str]] = {
        "model_execution": [] if model is None else [model.target_id()],
        "workload_run": runs,
    }
    groups = [
        (level, key, _ROLLUPS[level](allocations)[key])
        for level, level_keys in keys.items()
        for key in level_keys
    ]
    return [
        ExplainedRollup(
            level=level, key=key, currency=t.currency, cost_basis=t.cost_basis, amount=t.amount
        )
        for level, key, totals in groups
        for t in totals
        if (t.currency, t.cost_basis) == (allocation.currency, allocation.cost_basis)
    ]


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
