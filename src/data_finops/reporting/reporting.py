"""Answers from the Ledger for the API (spec 06), opened per request on a database file."""

import sqlite3
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any, Self, get_args

from data_finops.attribution.dimensions import dimension_totals
from data_finops.attribution.engine import AllocationMethod
from data_finops.attribution.rollups import (
    RollupTotal,
    model_execution_of,
    model_execution_rollups,
    query_execution_of,
    workload_run_rollups,
    workload_runs_of,
)
from data_finops.contracts.allocation import (
    Allocation,
    AllocationCategory,
    Confidence,
    TargetType,
    policy_components,
)
from data_finops.contracts.billing import (
    CostBasis,
    FocusBillingLine,
    FocusExport,
    SnapshotScope,
    parse_line_ref,
    snapshot_id_of,
)
from data_finops.contracts.workload import LinkResolution, QueryExecution
from data_finops.identity.links import ModelExecutionKey
from data_finops.ledger import Ledger, StoredAllocations
from data_finops.reporting.models import (
    AllocatedAmount,
    AllocationCurrencyReconciliation,
    AllocationEntry,
    AllocationLineReconciliation,
    AllocationPage,
    AllocationReconciliation,
    BillingLineDetail,
    CostExplanation,
    CostSummaries,
    CostSummary,
    CurrencySummary,
    DimensionRollupEntry,
    DimensionRollupPage,
    DimensionSourceAmount,
    DimensionTotal,
    ExplainedModelExecution,
    ExplainedRollup,
    ExplainedWorkloadRun,
    ExportSnapshotRead,
    InvocationLink,
    LineageModelExecution,
    MinimalBillingLine,
    Provenance,
    QueryClaim,
    RollupAmount,
    RollupEntry,
    RollupLevel,
    RollupPage,
    RootRunPage,
    RunLineage,
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


LineFilter = Callable[[FocusBillingLine], bool]
"""Which Billing Lines a read model sees."""


class Reporting:
    """The read model over one database file."""

    def __init__(self, path: Path, *, lines: LineFilter | None = None) -> None:
        """Open both stores on an existing file; raises DatabaseUnavailable if there is no
        file or either store refuses it, so a wrong path is reported rather than created.

        With `lines`, every read sees only the Billing Lines it keeps and the Allocations of
        them, which keep their refs (the Demo Snapshot, spec 09, Q82): the summary, the
        Allocations, the Roll-ups, the Cost Dimensions, the reconciliation and each Cost
        Explanation. Provenance and lineage do not read the bill, so it does not change them."""
        self._lines = lines
        # Memos for one instance's life (one request, or one publish): an Export Snapshot is
        # immutable, and so is an Allocation Snapshot.
        self._exports: dict[str, FocusExport | None] = {}
        self._kept_by_run: dict[str, list[tuple[int, Allocation]]] = {}
        self._rollup_maps: dict[tuple[str, str], dict[str, Any]] = {}
        self._allocation_snapshots: dict[str, StoredAllocations] = {}
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
        calculation_run_id: str | None = None,
        billing_account_id: str | None = None,
        billing_period: date | None = None,
        source: str | None = None,
    ) -> CostSummaries:
        """A Cost Summary for the current Allocation Snapshot of each matching scope, or for
        the snapshot of `calculation_run_id` alone, current or superseded (#215).

        A named run fixes its own scope: ConflictingFilters if the caller also gives scope
        filters, NotFound if no such run is stored, as for every snapshot read (spec 06).
        `billing_period` is the Billing Period's first day. The Cost Basis is validated at
        runtime, so an unknown name raises ValueError.
        """
        basis = CostBasis(cost_basis)
        if calculation_run_id is not None:
            named = self._served(calculation_run_id, billing_account_id, billing_period, source)
            if named.snapshot.scope is None:
                raise RuntimeError("the Ledger returned an Allocation Snapshot without its scope")
            return CostSummaries(
                summaries=[self._summary(named.snapshot.scope, named, basis)],
                uncalculated_scopes=[],
            )
        summaries: list[CostSummary] = []
        uncalculated: list[SnapshotScope] = []
        for scope in self._scopes(billing_account_id, billing_period, source):
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
            for position, allocation in self._kept(stored)
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
                    run=self._store.workload_run(key) if level == "workload_run" else None,
                )
                for key, totals in self._rollup_map(stored, level).items()
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

    def dimension_rollups(
        self,
        *,
        name: str,
        calculation_run_id: str | None = None,
        billing_account_id: str | None = None,
        billing_period: date | None = None,
        source: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> DimensionRollupPage:
        """One page of the values of the Cost Dimension `name` in one Allocation Snapshot,
        each with its amounts by currency and Cost Basis and how its sources split (ADR 0031).
        Unassigned cost is the value None, after the assigned values.

        The snapshot is selected as for `allocations`. NotFound when no Allocation of the
        snapshot carries the dimension. ValueError for a page out of bounds.
        """
        _check_page(limit, offset)
        stored = self._served(calculation_run_id, billing_account_id, billing_period, source)
        totals = dimension_totals(self._kept_allocations(stored), name)
        if totals is None:
            raise NotFound("no such Cost Dimension in this Allocation Snapshot")
        entries = [
            DimensionRollupEntry(
                value=value,
                totals=[
                    DimensionTotal(
                        currency=currency,
                        cost_basis=CostBasis(basis),
                        amount=sum(by_source.values(), Decimal(0)),
                        sources=[
                            DimensionSourceAmount(source=origin, amount=amount)
                            for origin, amount in sorted(by_source.items(), key=_none_last)
                        ],
                    )
                    for (currency, basis), by_source in sorted(by_basis.items())
                ],
            )
            for value, by_basis in sorted(totals.items(), key=_none_last)
        ]
        overall: dict[tuple[str, CostBasis], Decimal] = {}
        for entry in entries:
            for value_total in entry.totals:
                key = (value_total.currency, value_total.cost_basis)
                overall[key] = overall.get(key, Decimal(0)) + Decimal(value_total.amount)
        return DimensionRollupPage(
            calculation_run_id=stored.snapshot.calculation_run_id,
            dimension=name,
            totals=[
                RollupAmount(currency=currency, cost_basis=basis, amount=amount)
                for (currency, basis), amount in sorted(overall.items())
            ],
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
        for allocation in self._kept_allocations(stored):
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
        if snapshot.scope is None:
            raise RuntimeError("the Ledger returned an Allocation Snapshot without its scope")
        records = [
            record
            for export_id in _export_snapshot_ids(stored)
            if (record := self._ledger.snapshot_record(export_id)) is not None
        ]
        return Provenance(
            calculation_run_id=snapshot.calculation_run_id,
            scope=snapshot.scope,
            policy_version=snapshot.policy_version,
            policy_components=policy_components(snapshot.policy_version),
            input_digest=snapshot.input_digest,
            created_at=snapshot.created_at,
            is_current=snapshot.is_current,
            superseded_by=self._superseded_by(snapshot.calculation_run_id, snapshot.scope),
            export_snapshot=next(
                (
                    ExportSnapshotRead(
                        snapshot_id=r.snapshot.snapshot_id,
                        content_digest=r.content_digest,
                        published_at=r.snapshot.published_at,
                    )
                    for r in records
                ),
                None,
            ),
        )

    def _superseded_by(self, calculation_run_id: str, scope: SnapshotScope) -> str | None:
        """The next Allocation Snapshot stored for the scope after this one, if any."""
        runs = [s.calculation_run_id for s in self._ledger.allocation_snapshots(scope)]
        if calculation_run_id not in runs:
            return None
        later = runs[runs.index(calculation_run_id) + 1 :]
        return later[0] if later else None

    def root_runs(self, *, limit: int = 100, offset: int = 0) -> RootRunPage:
        """The topmost Workload Runs: each reported run's last ancestor, or the run itself if
        it has none, by run ID. A root known only from a ParentRunFacet is listed too."""
        _check_page(limit, offset)
        roots = self._store.root_runs()
        return RootRunPage(
            total=len(roots),
            limit=limit,
            offset=offset,
            items=[
                ExplainedWorkloadRun(run_id=run_id, run=self._store.workload_run(run_id))
                for run_id in roots[offset : offset + limit]
            ],
        )

    def lineage(self, run_id: str) -> RunLineage:
        """One Workload Run's lineage; NotFound if no event reports or names it. Model and
        Query Executions are reached only through Identity Links and their Link Outcomes."""
        run = self._store.workload_run(run_id)
        children = self._store.children(run_id)
        if run is None and not children:
            raise NotFound("no such Workload Run")
        invocation = self._store.invocation_link_of_run(run_id)
        model_run_link = None if invocation else self._store.model_execution_of_run(run_id)
        linked_invocation = _linked_invocation(invocation, run_id)
        return RunLineage(
            run_id=run_id,
            run=run,
            ancestors=[
                ExplainedWorkloadRun(run_id=a, run=self._store.workload_run(a))
                for a in self._store.ancestors(run_id)
            ],
            children=children,
            data_assets=self._store.data_assets(run_id),
            lineage_edges=self._store.lineage_edges(run_id),
            invocation=(
                None
                if invocation is None
                else InvocationLink(
                    invocation_id=invocation[0],
                    outcome=invocation[1].outcome,
                    linked_run_id=invocation[1].link.target_id if invocation[1].link else None,
                )
            ),
            model_run_link=model_run_link,
            model_executions=self._lineage_model_executions(linked_invocation, model_run_link),
            unmatched_queries=[
                QueryClaim(query_execution=query, outcome=resolution.outcome)
                for query, resolution in (
                    self._store.unmatched_queries_of_invocation(linked_invocation)
                    if linked_invocation
                    else []
                )
            ],
        )

    def _lineage_model_executions(
        self, linked_invocation: str | None, model_run_link: LinkResolution | None
    ) -> list[LineageModelExecution]:
        """The invocation's Model Executions when it links to this run, or the model run's
        linked Model Execution; none otherwise."""
        if linked_invocation is not None:
            executions = self._store.model_executions(linked_invocation)
        elif model_run_link is not None and model_run_link.link is not None:
            key = ModelExecutionKey.from_target_id(model_run_link.link.target_id)
            executions = [
                m
                for m in self._store.model_executions(key.invocation_id)
                if m.node_id == key.node_id
            ]
        else:
            executions = []
        return [
            LineageModelExecution(
                invocation_id=execution.invocation_id,
                node_id=execution.node_id,
                execution=execution,
                queries=[
                    QueryClaim(query_execution=query, outcome=resolution.outcome)
                    for query, resolution in self._store.query_links_of_model_execution(
                        execution.invocation_id, execution.node_id
                    )
                ],
            )
            for execution in executions
        ]

    def explanation(
        self, ref: str, *, detail: BillingLineDetail = BillingLineDetail.FULL
    ) -> CostExplanation:
        """The Cost Explanation of the Allocation at `ref` (`<calculation_run_id>#<position>`),
        current or superseded, its Billing Line shown at `detail` (ADR 0028); NotFound for an
        unknown or malformed reference."""
        parsed = _parse_allocation_ref(ref)
        stored = self._allocation_snapshot(parsed[0]) if parsed else None
        if parsed is None or stored is None or parsed[1] >= len(stored.allocations):
            raise NotFound("no such Allocation")
        allocation = stored.allocations[parsed[1]]
        line = self._billing_line(allocation.billing_line_ref)
        if self._lines is not None and not self._lines(line):
            raise NotFound("no such Allocation")
        model = model_execution_of(allocation)
        runs = workload_runs_of(allocation)
        return CostExplanation(
            ref=ref,
            billing_line_ref=allocation.billing_line_ref,
            billing_line=line if detail is BillingLineDetail.FULL else _declared_columns_only(line),
            billing_line_detail=detail,
            allocation=(
                allocation
                if detail is BillingLineDetail.FULL
                else without_sensitive_dimensions(allocation)
            ),
            unallocated_reason=(
                allocation.method if allocation.category is AllocationCategory.UNALLOCATED else None
            ),
            query_execution=self._query_execution(allocation, line, detail),
            model_execution=None if model is None else self._model_execution(model),
            workload_runs=[
                ExplainedWorkloadRun(run_id=run, run=self._store.workload_run(run)) for run in runs
            ],
            rollups=_rollups_of(
                allocation,
                model,
                runs,
                {level: self._rollup_map(stored, level) for level in get_args(RollupLevel)},
            ),
        )

    def _billing_line(self, line_ref: str) -> FocusBillingLine:
        """The stored Billing Line an Allocation partitions; a dangling reference is an
        integrity fault in the Ledger, not a missing resource."""
        parsed = parse_line_ref(line_ref)
        export = self._export(parsed[0]) if parsed else None
        if parsed is None or export is None or parsed[1] >= len(export.lines):
            raise RuntimeError("an Allocation references a Billing Line the Ledger lacks")
        return export.lines[parsed[1]]

    def _query_execution(
        self, allocation: Allocation, line: FocusBillingLine, detail: BillingLineDetail
    ) -> QueryExecution | None:
        """The Query Execution the Allocation's Evidence names, in the line's usage account
        and region (spec 03); None if the Evidence names none or it is not stored. At the
        minimal detail, its Cost Dimension claims are left out (ADR 0028, ADR 0031)."""
        query_execution_id = query_execution_of(allocation)
        if query_execution_id is None or line.SubAccountId is None or line.RegionId is None:
            return None
        query = self._store.query_execution(line.SubAccountId, line.RegionId, query_execution_id)
        if query is None or detail is BillingLineDetail.FULL:
            return query
        return query.model_copy(update={"claims": {}})

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

    def _allocation_snapshot(self, calculation_run_id: str) -> StoredAllocations | None:
        """A stored Allocation Snapshot, read from the Ledger once for each Calculation Run.
        A stored snapshot never changes, so the copy stays right (#316). An unknown run is not
        kept, so a run stored later is found."""
        if calculation_run_id not in self._allocation_snapshots:
            stored = self._ledger.allocation_snapshot(calculation_run_id)
            if stored is None:
                return None
            self._allocation_snapshots[calculation_run_id] = stored
        return self._allocation_snapshots[calculation_run_id]

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
            named = self._allocation_snapshot(calculation_run_id)
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
        source: str | None,
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
        allocations = [a for _, a in self._kept(stored) if a.cost_basis == basis]
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
        bill = []
        for snapshot_id in _export_snapshot_ids(stored):
            export = self._export(snapshot_id)
            if export is not None:
                bill += [
                    (export.line_ref(i), line)
                    for i, line in enumerate(export.lines)
                    if self._lines is None or self._lines(line)
                ]
        return bill

    def _kept(self, stored: StoredAllocations) -> list[tuple[int, Allocation]]:
        """Each Allocation of the snapshot whose Billing Line the read model sees, with its
        position in the snapshot, which its ref names."""
        run_id = stored.snapshot.calculation_run_id
        if run_id not in self._kept_by_run:
            if self._lines is None:
                kept = list(enumerate(stored.allocations))
            else:
                seen = {ref for ref, _ in self._bill(stored)}
                kept = [
                    (i, a) for i, a in enumerate(stored.allocations) if a.billing_line_ref in seen
                ]
            self._kept_by_run[run_id] = kept
        return self._kept_by_run[run_id]

    def _export(self, snapshot_id: str) -> FocusExport | None:
        if snapshot_id not in self._exports:
            self._exports[snapshot_id] = self._ledger.snapshot(snapshot_id)
        return self._exports[snapshot_id]

    def _rollup_map(self, stored: StoredAllocations, level: RollupLevel) -> dict[str, Any]:
        """The Roll-ups at `level` of the Allocations the read model sees, by key."""
        key = (stored.snapshot.calculation_run_id, level)
        if key not in self._rollup_maps:
            self._rollup_maps[key] = dict(_ROLLUPS[level](self._kept_allocations(stored)))
        return self._rollup_maps[key]

    def _kept_allocations(self, stored: StoredAllocations) -> list[Allocation]:
        return [allocation for _, allocation in self._kept(stored)]


def _export_snapshot_ids(stored: StoredAllocations) -> list[str]:
    """The Export Snapshot a snapshot's run read: recorded since M5; before that, named by its
    Allocations' line references."""
    recorded = stored.snapshot.export_snapshot_id
    if recorded is not None:
        return [recorded]
    return sorted({snapshot_id_of(a.billing_line_ref) for a in stored.allocations})


def _linked_invocation(invocation: tuple[str, LinkResolution] | None, run_id: str) -> str | None:
    """The run's invocation, when its Identity Link targets this run."""
    if invocation is None or invocation[1].link is None:
        return None
    return invocation[0] if invocation[1].link.target_id == run_id else None


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


def _none_last(item: tuple[str | None, Any]) -> tuple[bool, str]:
    """Sorts by key, with the None key (unassigned) last."""
    return (item[0] is None, item[0] or "")


def without_sensitive_dimensions(allocation: Allocation) -> Allocation:
    """The Allocation without its sensitive Cost Dimensions (ADR 0031 point 8)."""
    kept = {name: value for name, value in allocation.dimensions.items() if not value.sensitive}
    return allocation.model_copy(update={"dimensions": kept})


def _declared_columns_only(line: FocusBillingLine) -> MinimalBillingLine:
    """The line with only its declared columns, `Tags` withheld (ADR 0028). Raw values are
    keyed by column path (spec 02); every shown column is a scalar, keyed by its bare name,
    so a path that is not a shown column's name belongs to a withheld one."""
    shown = set(MinimalBillingLine.model_fields) - {"Tags"}
    declared = {name: getattr(line, name) for name in shown}
    raw = line.data_finops_raw_values
    if raw is not None:
        declared["data_finops_raw_values"] = {
            path: value for path, value in raw.items() if path in shown
        }
    return MinimalBillingLine.model_validate(declared)


def _parse_allocation_ref(ref: str) -> tuple[str, int] | None:
    run_id, _, position = ref.rpartition("#")
    return (run_id, int(position)) if run_id and position.isdigit() else None


def _rollups_of(
    allocation: Allocation,
    model: ModelExecutionKey | None,
    runs: list[str],
    maps: dict[str, dict[str, Any]],
) -> list[ExplainedRollup]:
    """The Roll-ups `allocation` contributes to, in its own currency and Cost Basis, from the
    snapshot's Roll-ups by level and key. Roll-ups sum only Allocations to Query Executions, so
    any other Allocation has no Roll-up, though its Evidence can link a Model Execution (#307)."""
    if allocation.target_type != TargetType.QUERY_EXECUTION:
        return []
    keys: dict[RollupLevel, list[str]] = {
        "model_execution": [] if model is None else [model.target_id()],
        "workload_run": runs,
    }
    groups = [
        (level, key, maps[level][key]) for level, level_keys in keys.items() for key in level_keys
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
    coverage: dict[tuple[AllocationCategory, Confidence | None], list[Decimal]] = defaultdict(list)
    unallocated: dict[AllocationMethod, Decimal] = defaultdict(Decimal)
    adjustments = billed * 0  # zero at the bill's own scale, e.g. 0.00
    for a in allocations:
        if a.category is not AllocationCategory.UNALLOCATED:
            allocated[(a.category, a.confidence)] += a.amount
            if a.coverage is not None:
                coverage[(a.category, a.confidence)].append(a.coverage)
        elif a.method == AllocationMethod.ADJUSTMENT:
            adjustments += a.amount
        else:
            unallocated[AllocationMethod(a.method)] += a.amount
    return CurrencySummary(
        currency=currency,
        billed=billed,
        allocated=[
            AllocatedAmount(
                category=category,
                confidence=confidence,
                amount=amount,
                coverage_min=min(coverage[(category, confidence)], default=None),
                coverage_max=max(coverage[(category, confidence)], default=None),
            )
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
