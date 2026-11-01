"""Minimal immutable contracts; not a finished attribution implementation."""
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

class AllocationKind(str, Enum):
    DIRECT = "direct"
    SHARED = "shared"
    IDLE = "idle"
    UNALLOCATED = "unallocated"

@dataclass(frozen=True)
class Allocation:
    source_record_id: str
    currency: str
    amount: Decimal
    kind: AllocationKind
    target: str
    evidence_id: str | None = None
