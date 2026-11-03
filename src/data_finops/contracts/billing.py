"""Billing contracts: FOCUS 1.2 rows grouped into Export Snapshots (ADR 0015).

Field names are FOCUS column names. Only the columns the product relies on are
declared; every other column, including provider `x_` extensions, is preserved.
This is not a claim of full FOCUS conformance (see the standards matrix).
"""

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_finops.contracts.types import CurrencyCode, DecimalString, UtcDatetime

FOCUS_VERSION = "1.2"


class FocusChargeCategory(StrEnum):
    USAGE = "Usage"
    PURCHASE = "Purchase"
    TAX = "Tax"
    CREDIT = "Credit"
    ADJUSTMENT = "Adjustment"


class FocusChargeClass(StrEnum):
    CORRECTION = "Correction"


class CostBasis(StrEnum):
    """The FOCUS cost column a figure is taken from; bases are never added together."""

    BILLED_COST = "BilledCost"
    EFFECTIVE_COST = "EffectiveCost"
    LIST_COST = "ListCost"
    CONTRACTED_COST = "ContractedCost"


class FocusBillingLine(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow", title="BillingLine")

    BilledCost: DecimalString
    EffectiveCost: DecimalString
    ListCost: DecimalString
    ContractedCost: DecimalString
    BillingAccountId: str
    BillingCurrency: CurrencyCode
    BillingPeriodStart: UtcDatetime
    BillingPeriodEnd: UtcDatetime
    ChargePeriodStart: UtcDatetime
    ChargePeriodEnd: UtcDatetime
    ChargeCategory: FocusChargeCategory
    ChargeClass: FocusChargeClass | None = None
    ProviderName: str
    ServiceName: str
    ServiceCategory: str | None = None
    SkuId: str | None = None
    RegionId: str | None = None
    ResourceId: str | None = None
    SubAccountId: str | None = None
    ConsumedQuantity: DecimalString | None = None
    ConsumedUnit: str | None = None
    PricingQuantity: DecimalString | None = None
    PricingUnit: str | None = None
    Tags: dict[str, str] | None = None
    data_finops_raw_values: dict[str, str] | None = None
    """Provenance, not a FOCUS column (spec 02, OQ-21): the provider's raw double for
    every amount converted to a decimal, as its exact repr() string, keyed by column
    path (`column`, `column.field`, `column[index]`). Never compute with these."""

    def cost(self, basis: CostBasis) -> Decimal:
        """The line's amount in the given cost column."""
        return {
            CostBasis.BILLED_COST: self.BilledCost,
            CostBasis.EFFECTIVE_COST: self.EffectiveCost,
            CostBasis.LIST_COST: self.ListCost,
            CostBasis.CONTRACTED_COST: self.ContractedCost,
        }[basis]

    @model_validator(mode="after")
    def _periods_are_ordered(self) -> "FocusBillingLine":
        if not self.BillingPeriodStart < self.BillingPeriodEnd:
            raise ValueError("BillingPeriodStart must precede BillingPeriodEnd")
        if not self.ChargePeriodStart < self.ChargePeriodEnd:
            raise ValueError("ChargePeriodStart must precede ChargePeriodEnd")
        return self


class SnapshotFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SnapshotScope(BaseModel):
    """What an Export Snapshot covers; a restatement replaces the snapshot for its scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    billing_account_id: str
    billing_period_start: UtcDatetime
    billing_period_end: UtcDatetime
    source: str


class ExportSnapshot(BaseModel):
    """One provider publication of all lines for a billing account and Billing Period."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str = Field(pattern=r"^[A-Za-z0-9._:-]+$")
    source: str
    focus_version: str
    billing_account_id: str
    billing_period_start: UtcDatetime
    billing_period_end: UtcDatetime
    published_at: UtcDatetime
    files: list[SnapshotFile] = []

    @property
    def scope(self) -> SnapshotScope:
        return SnapshotScope(
            billing_account_id=self.billing_account_id,
            billing_period_start=self.billing_period_start,
            billing_period_end=self.billing_period_end,
            source=self.source,
        )


class FocusExport(BaseModel):
    """An Export Snapshot with its lines. A line is referenced as `<snapshot_id>#<position>`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot: ExportSnapshot
    lines: list[FocusBillingLine]

    @model_validator(mode="after")
    def _lines_belong_to_snapshot(self) -> "FocusExport":
        s = self.snapshot
        for i, line in enumerate(self.lines):
            if line.BillingAccountId != s.billing_account_id:
                raise ValueError(f"line {i}: billing account differs from snapshot")
            if (line.BillingPeriodStart, line.BillingPeriodEnd) != (
                s.billing_period_start,
                s.billing_period_end,
            ):
                raise ValueError(f"line {i}: Billing Period differs from snapshot")
        return self

    def billing_content(self) -> dict[str, Any]:
        """The billing data itself: Snapshot Scope, FOCUS version and every line in order.

        Publication metadata (snapshot ID, publish time, files) is excluded, so the same
        content delivered twice can be recognised as a replay (ADR 0019).
        """
        return {
            "scope": self.snapshot.scope.model_dump(mode="json"),
            "focus_version": self.snapshot.focus_version,
            "lines": [line.model_dump(mode="json") for line in self.lines],
        }

    def line_ref(self, position: int) -> str:
        if not 0 <= position < len(self.lines):
            raise IndexError(position)
        return f"{self.snapshot.snapshot_id}#{position}"


def snapshot_id_of(line_ref: str) -> str:
    """The Export Snapshot a Billing Line reference (`<snapshot_id>#<position>`) points into."""
    return line_ref.rpartition("#")[0]
