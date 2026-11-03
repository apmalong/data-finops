"""Response models of the read model (spec 06). Amounts are Decimal strings, never floats,
and never summed across currencies."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from data_finops.attribution.engine import AllocationMethod
from data_finops.contracts.allocation import Allocation, AllocationCategory, Confidence
from data_finops.contracts.billing import CostBasis, FocusBillingLine, SnapshotScope
from data_finops.contracts.types import CurrencyCode, DecimalString, UtcDatetime
from data_finops.contracts.workload import (
    DbtNode,
    ModelExecution,
    QueryExecution,
    WorkloadRun,
)


class _Response(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ServedSnapshot(_Response):
    """The Allocation Snapshot a read served, and the Calculation Run that made it."""

    calculation_run_id: str
    policy_version: str
    created_at: UtcDatetime
    is_current: bool
    export_snapshot_id: str | None
    """The Export Snapshot the run read; None for snapshots stored before M5."""


class AllocatedAmount(_Response):
    """Allocations of one Allocation Category and Confidence (none for Idle)."""

    category: AllocationCategory
    confidence: Confidence | None
    amount: DecimalString


class UnallocatedAmount(_Response):
    """Unallocated amounts for one reason (the Allocation's method), Adjustments aside."""

    reason: AllocationMethod
    amount: DecimalString


class CurrencySummary(_Response):
    """One currency's part of a Cost Summary; never added to another currency's."""

    currency: CurrencyCode
    billed: DecimalString
    """The Billing Lines' total for the Cost Basis."""
    allocated: list[AllocatedAmount]
    unallocated: list[UnallocatedAmount]
    adjustments: DecimalString
    """Adjustment lines (credits, refunds, discounts), Unallocated and shown apart (OQ-18)."""
    reconciliation_delta: DecimalString
    """Billed minus every Allocation; zero when Reconciliation holds."""


class CostSummary(_Response):
    """Where one Allocation Snapshot put its Snapshot Scope's bill, for one Cost Basis."""

    scope: SnapshotScope
    cost_basis: CostBasis
    snapshot: ServedSnapshot
    currencies: list[CurrencySummary]


class AllocationEntry(_Response):
    """One Allocation and its address, `<calculation_run_id>#<position>` (spec 06)."""

    ref: str
    allocation: Allocation


class AllocationPage(_Response):
    """One page of a single Allocation Snapshot's Allocations; offsets are stable because
    the snapshot never changes."""

    calculation_run_id: str
    total: int
    """Allocations matching the filters, across every page."""
    limit: int
    offset: int
    items: list[AllocationEntry]


class AllocationLineReconciliation(_Response):
    """One Billing Line, Cost Basis and currency: what was billed, what the Allocations hold,
    and the Reconciliation Delta between them (zero when Reconciliation holds). A line the bill
    lacks, or a currency other than the line's, is billed zero, so invented amounts show."""

    billing_line_ref: str
    currency: CurrencyCode
    cost_basis: CostBasis
    billed: DecimalString
    allocated_amount: DecimalString
    reconciliation_delta: DecimalString


class AllocationCurrencyReconciliation(_Response):
    """Every line of one currency and Cost Basis; never added to another currency's."""

    currency: CurrencyCode
    cost_basis: CostBasis
    billed: DecimalString
    allocated_amount: DecimalString
    reconciliation_delta: DecimalString


class AllocationReconciliation(_Response):
    """One Allocation Snapshot's Allocations against the Export Snapshot its Calculation Run
    read: per-currency totals over every line, and one page of the lines."""

    calculation_run_id: str
    export_snapshot_id: str | None
    totals: list[AllocationCurrencyReconciliation]
    total: int
    """Rows (Billing Line, Cost Basis and currency) across every page."""
    limit: int
    offset: int
    items: list[AllocationLineReconciliation]


RollupLevel = Literal["model_execution", "workload_run"]
"""What a Roll-up sums to (ADR 0016)."""


class RollupAmount(_Response):
    """One Roll-up total, in one currency and Cost Basis."""

    currency: CurrencyCode
    cost_basis: CostBasis
    amount: DecimalString


class RollupEntry(_Response):
    """What one Model Execution or Workload Run cost: a view over Allocations, never an
    Allocation (GLOSSARY Roll-up)."""

    key: str
    """The Model Execution as `["<invocation>","<node>"]`, or the Workload Run's ID."""
    totals: list[RollupAmount]


class RollupPage(_Response):
    """One page of one Allocation Snapshot's Roll-ups at one level, ordered by key."""

    calculation_run_id: str
    level: RollupLevel
    total: int
    limit: int
    offset: int
    items: list[RollupEntry]


class ExplainedModelExecution(_Response):
    """The Model Execution an Allocation's Evidence links, as the workload store holds it;
    `execution` or `dbt_node` is None when that part was never ingested."""

    invocation_id: str
    node_id: str
    execution: ModelExecution | None
    dbt_node: DbtNode | None


class ExplainedWorkloadRun(_Response):
    """A Workload Run an Allocation's Evidence names; `run` is None if never reported."""

    run_id: str
    run: WorkloadRun | None
    """None when the store holds no event for it: a run known only as another run's parent
    or root (ParentRunFacet) has no status of its own."""


class ExplainedRollup(_Response):
    """A Roll-up the Allocation contributes to, in the Allocation's currency and Cost Basis."""

    level: RollupLevel
    key: str
    """The Model Execution as `["<invocation>","<node>"]`, or the Workload Run's ID."""
    currency: CurrencyCode
    cost_basis: CostBasis
    amount: DecimalString


class CostExplanation(_Response):
    """The chain behind one Allocation (GLOSSARY): its Billing Line, the Allocation, its
    Evidence resolved, and the Roll-ups it contributes to."""

    ref: str
    billing_line_ref: str
    billing_line: FocusBillingLine
    """Every FOCUS column as exported, with the provider's raw values."""
    allocation: Allocation
    unallocated_reason: str | None
    """Why an Unallocated amount was not allocated (its method); None otherwise."""
    query_execution: QueryExecution | None
    model_execution: ExplainedModelExecution | None
    workload_runs: list[ExplainedWorkloadRun]
    """Nearest first, then parents, then the root, as the Evidence records them."""
    rollups: list[ExplainedRollup]


class CostSummaries(_Response):
    """The Cost Summary of each matching Snapshot Scope's current Allocation Snapshot."""

    summaries: list[CostSummary]
    uncalculated_scopes: list[SnapshotScope]
    """Scopes with an Export Snapshot but no Allocation Snapshot yet: their bill is not shown."""
