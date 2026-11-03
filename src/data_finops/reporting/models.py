"""Response models of the read model (spec 06). Amounts are Decimal strings, never floats,
and never summed across currencies."""

from pydantic import BaseModel, ConfigDict

from data_finops.contracts.allocation import AllocationCategory, Confidence
from data_finops.contracts.billing import CostBasis, SnapshotScope
from data_finops.contracts.types import CurrencyCode, DecimalString, UtcDatetime


class _Response(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ServedSnapshot(_Response):
    """The Allocation Snapshot a read served for one Snapshot Scope."""

    scope: SnapshotScope
    calculation_run_id: str
    policy_version: str
    created_at: UtcDatetime
    is_current: bool


class AllocatedAmount(_Response):
    """Allocations of one Allocation Category and Confidence (none for Idle)."""

    category: AllocationCategory
    confidence: Confidence | None
    amount: DecimalString


class UnallocatedAmount(_Response):
    """Unallocated amounts for one reason (the Allocation's method), Adjustments aside."""

    reason: str
    amount: DecimalString


class CurrencySummary(_Response):
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
    """Where the served Allocation Snapshots put the bill, for one Cost Basis."""

    cost_basis: CostBasis
    currencies: list[CurrencySummary]
    snapshots: list[ServedSnapshot]
    uncalculated_scopes: list[SnapshotScope]
    """Scopes with an Export Snapshot but no Allocation Snapshot yet: their bill is not shown."""
