"""Response models of the read model (spec 06). Amounts are Decimal strings, never floats,
and never summed across currencies."""

from pydantic import BaseModel, ConfigDict

from data_finops.attribution.engine import AllocationMethod
from data_finops.contracts.allocation import Allocation, AllocationCategory, Confidence
from data_finops.contracts.billing import CostBasis, SnapshotScope
from data_finops.contracts.types import CurrencyCode, DecimalString, UtcDatetime


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


class CostSummaries(_Response):
    """The Cost Summary of each matching Snapshot Scope's current Allocation Snapshot."""

    summaries: list[CostSummary]
    uncalculated_scopes: list[SnapshotScope]
    """Scopes with an Export Snapshot but no Allocation Snapshot yet: their bill is not shown."""
