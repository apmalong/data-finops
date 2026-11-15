"""Allocation contracts (spec 02, ADRs 0016 and 0017)."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator
from pydantic.config import JsonDict

from data_finops.contracts.billing import SnapshotScope
from data_finops.contracts.types import CurrencyCode, DecimalString, UtcDatetime

POLICY_SEPARATOR = "+"
"""Joins the engine and policy versions a Calculation Run applied into its policy version."""


def policy_components(policy_version: str) -> list[str]:
    """The engine and each policy in a policy version, e.g. `allocation-engine/1`."""
    return policy_version.split(POLICY_SEPARATOR)


class AllocationCategory(StrEnum):
    DIRECT = "direct"
    SHARED = "shared"
    IDLE = "idle"
    UNALLOCATED = "unallocated"


class Confidence(StrEnum):
    DIRECT_VERIFIED = "direct_verified"
    METERED_ESTIMATE = "metered_estimate"
    HEURISTIC_ESTIMATE = "heuristic_estimate"


class TargetType(StrEnum):
    QUERY_EXECUTION = "query_execution"
    IDLE = "idle"
    UNALLOCATED = "unallocated"


def _when(category: str | list[JsonValue], then: JsonDict) -> JsonDict:
    match: JsonDict = {"const": category} if isinstance(category, str) else {"enum": category}
    return {"if": {"properties": {"category": match}}, "then": then}


# The same rules as `_category_rules`, published so non-Python consumers enforce them.
_CATEGORY_RULES: list[JsonValue] = [
    _when(
        "unallocated",
        {
            "properties": {
                "target_type": {"const": "unallocated"},
                "target_id": {"const": "unallocated"},
                "confidence": {"type": "null"},
            }
        },
    ),
    _when(
        "idle",
        {"properties": {"target_type": {"const": "idle"}, "target_id": {"const": "idle"}}},
    ),
    _when(
        ["direct", "shared"],
        {
            "required": ["confidence"],
            "properties": {
                "target_type": {"const": "query_execution"},
                "confidence": {"enum": [c.value for c in Confidence]},
            },
        },
    ),
]


class DimensionValue(BaseModel):
    """The value of one Cost Dimension on an Allocation, and the source that gave it (ADR 0031).
    A value is a claim, never Evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: str | None
    """None: unassigned. No source gave a value."""
    source: str | None
    """The source that gave the value, for example `query_comment.team`. None when
    unassigned."""
    sensitive: bool = False
    """The dimension was marked sensitive: the minimal Cost Explanation leaves it out."""


class Allocation(BaseModel):
    """The part of one Billing Line assigned to one Allocation Target."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_extra={"allOf": _CATEGORY_RULES}
    )

    billing_line_ref: str
    pool_id: str
    target_type: TargetType
    target_id: str
    amount: DecimalString
    currency: CurrencyCode
    cost_basis: str
    category: AllocationCategory
    method: str
    confidence: Confidence | None = None
    coverage: DecimalString | None = None
    policy_version: str
    calculation_run_id: str
    evidence_refs: list[str] = []
    as_of: UtcDatetime | None = None
    dimensions: dict[str, DimensionValue] = {}
    """Each configured Cost Dimension, by name (ADR 0031). Empty without a configuration."""

    @model_validator(mode="after")
    def _category_rules(self) -> "Allocation":
        synthetic = {
            AllocationCategory.IDLE: TargetType.IDLE,
            AllocationCategory.UNALLOCATED: TargetType.UNALLOCATED,
        }
        if self.category in synthetic:
            target = synthetic[self.category]
            if (self.target_type, self.target_id) != (target, target.value):
                raise ValueError(f"{self.category} amounts must target '{target.value}'")
        elif self.target_type is not TargetType.QUERY_EXECUTION:
            raise ValueError(f"{self.category} amounts must target a Query Execution")

        if self.category is AllocationCategory.UNALLOCATED and self.confidence is not None:
            raise ValueError("Unallocated amounts carry no confidence")
        if (
            self.category in (AllocationCategory.DIRECT, AllocationCategory.SHARED)
            and self.confidence is None
        ):
            raise ValueError(f"{self.category} amounts require a confidence")
        if self.coverage is not None and self.coverage < 0:
            raise ValueError("coverage cannot be negative")
        return self


class AllocationSnapshot(BaseModel):
    """Metadata for the complete set of Allocations from one Calculation Run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calculation_run_id: str
    policy_version: str
    input_digest: str
    created_at: UtcDatetime
    is_current: bool
    export_snapshot_id: str | None = None
    """The Export Snapshot the Calculation Run read; None for snapshots stored before M5."""
    dimensions_sha256: str | None = None
    """The SHA-256 of the Cost Dimensions configuration the run applied. It is not part of the
    input digest (Q72). None without a configuration."""
    scope: SnapshotScope | None = None
    """The Snapshot Scope it was calculated for, as the Ledger records it."""
