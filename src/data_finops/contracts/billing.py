"""Billing contracts: FOCUS 1.2 rows grouped into Export Snapshots (ADR 0015).

Field names are FOCUS column names. Only the columns the product relies on are
declared; every other column, including provider `x_` extensions, is preserved.
This is not a claim of full FOCUS conformance (see the standards matrix).
"""

from enum import StrEnum

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
            source=self.source,
        )


class FocusExport(BaseModel):
    """An Export Snapshot with its lines. A line is referenced as `<snapshot_id>#<index>`."""

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

    def line_ref(self, index: int) -> str:
        if not 0 <= index < len(self.lines):
            raise IndexError(index)
        return f"{self.snapshot.snapshot_id}#{index}"
