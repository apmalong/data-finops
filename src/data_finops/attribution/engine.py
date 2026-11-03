"""The allocation engine (spec 03, ADRs 0016 and 0017): Billing Lines in, Allocations out.

Pure: no I/O, clock or provider knowledge. Callers classify each Billing Line into a pool
(metered, direct, shared, idle, Ineligible or Adjustment) and give each Query Execution's
Billable Usage in the line's own unit; the engine only splits money.
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, localcontext
from enum import StrEnum
from typing import NamedTuple, assert_never

from data_finops.contracts.allocation import (
    Allocation,
    AllocationCategory,
    Confidence,
    TargetType,
)
from data_finops.contracts.billing import CostBasis, FocusBillingLine

# ISO 4217 list one (published 2026-09-17): minor units other than 2; all others have 2.
_MINOR_UNITS = {
    **dict.fromkeys(
        "BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX UYI VND VUV XAF XOF XPF".split(), 0
    ),
    **dict.fromkeys("BHD IQD JOD KWD LYD OMR TND".split(), 3),
    **dict.fromkeys("CLF UYW".split(), 4),
}


class _Target(NamedTuple):
    """A split target. Keying by type and ID keeps the synthetic targets apart from any
    Query Execution, whatever its ID; ties in a split go to the lowest (type, ID)."""

    type: TargetType
    id: str


_UNALLOCATED = _Target(TargetType.UNALLOCATED, TargetType.UNALLOCATED.value)
_IDLE = _Target(TargetType.IDLE, TargetType.IDLE.value)


def _query(query_execution_id: str) -> _Target:
    return _Target(TargetType.QUERY_EXECUTION, query_execution_id)


class AllocationMethod(StrEnum):
    """How an amount was allocated; for Unallocated amounts, why (spec 03, M4 structure 10)."""

    ESTIMATED_METERED_SHARE = "estimated_metered_share"
    PROVIDER_LINE_MATCH = "provider_line_match"
    WEIGHTED_SHARE = "weighted_share"
    NO_OBSERVED_ACTIVITY = "no_observed_activity"
    INELIGIBLE = "ineligible"
    ADJUSTMENT = "adjustment"
    COVERAGE_REMAINDER = "coverage_remainder"
    COVERAGE_EXCEEDED = "coverage_exceeded"
    NO_EVIDENCE = "no_evidence"


@dataclass(frozen=True)
class MeteredUsage:
    """One Query Execution's Billable Usage, in the line's ConsumedUnit, with its Evidence."""

    query_execution_id: str
    quantity: Decimal
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class MeteredCostPool:
    """An aggregated line split by the Billable Usage of the Query Executions in its scope."""

    pool_id: str
    usages: tuple[MeteredUsage, ...]


@dataclass(frozen=True)
class DirectCostPool:
    """A line the provider bills to exactly one Query Execution."""

    pool_id: str
    query_execution_id: str
    confidence: Confidence
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class SharedWeight:
    """One Query Execution's documented, nonnegative weight in a shared Cost Pool."""

    query_execution_id: str
    weight: Decimal


@dataclass(frozen=True)
class SharedCostPool:
    """A line shared among Query Executions by explicit weights (spec 03 step F)."""

    pool_id: str
    weights: tuple[SharedWeight, ...]
    confidence: Confidence
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdleCostPool:
    """A line paying for capacity with no observed activity."""

    pool_id: str


@dataclass(frozen=True)
class Ineligible:
    """A line no MVP policy allocates; it stays Unallocated under `pool_id`."""

    pool_id: str


@dataclass(frozen=True)
class Adjustment:
    """A credit, refund or discount line: Unallocated in the MVP, signed, shown as its own
    labelled amount (ADR 0017)."""

    pool_id: str


Pool = MeteredCostPool | DirectCostPool | SharedCostPool | IdleCostPool | Ineligible | Adjustment


@dataclass(frozen=True)
class PooledLine:
    """A Billing Line, its reference (`<snapshot_id>#<position>`) and how it is classified."""

    ref: str
    line: FocusBillingLine
    pool: Pool


def allocate(
    lines: Iterable[PooledLine], *, policy_version: str, calculation_run_id: str
) -> list[Allocation]:
    """Partition every line, for every Cost Basis, among its Allocation Targets.

    Every pool kind becomes weighted shares, split by largest remainder per Cost Basis:
    - metered, by Coverage (ADR 0017): the covered fraction among the Query Executions by
      Billable Usage, the rest Unallocated `coverage_remainder`; Coverage above one makes
      the whole line Unallocated `coverage_exceeded`, with the conflicting Evidence. Every
      Allocation of a metered line records its Coverage;
    - direct: the whole line to its Query Execution;
    - shared: by explicit weights; with no positive weight, Unallocated `no_evidence`;
    - idle: the whole line to Idle;
    - Ineligible and Adjustment: wholly Unallocated with that reason, sign kept.

    Raises ValueError for a metered line without a positive ConsumedQuantity, a negative
    shared weight, or a pool naming one Query Execution more than once.
    """
    allocations: list[Allocation] = []
    for pooled in lines:
        line = pooled.line
        shares = _shares(pooled)
        weights = {share.target: share.weight for share in shares}
        if len(weights) != len(shares):
            counts = Counter(share.target.id for share in shares)
            repeated = sorted(target for target, count in counts.items() if count > 1)
            raise ValueError(f"{pooled.ref}: pool names {', '.join(repeated)} more than once")
        for basis in CostBasis:
            amount = line.cost(basis)
            parts = _split(amount, weights, _scale(amount, line.BillingCurrency))
            allocations += [
                Allocation(
                    billing_line_ref=pooled.ref,
                    pool_id=pooled.pool.pool_id,
                    target_type=share.target.type,
                    target_id=share.target.id,
                    amount=parts[share.target],
                    currency=line.BillingCurrency,
                    cost_basis=basis,
                    category=share.category,
                    method=share.method,
                    confidence=share.confidence,
                    coverage=share.coverage,
                    policy_version=policy_version,
                    calculation_run_id=calculation_run_id,
                    evidence_refs=list(share.evidence_refs),
                )
                for share in shares
            ]
    return allocations


@dataclass(frozen=True)
class _Share:
    """One Allocation Target's weight in a line, and how its Allocations are described."""

    target: _Target
    weight: Decimal
    category: AllocationCategory
    method: AllocationMethod
    confidence: Confidence | None = None
    coverage: Decimal | None = None
    evidence_refs: tuple[str, ...] = ()


def _unallocated(
    reason: AllocationMethod,
    weight: Decimal = Decimal(1),
    *,
    coverage: Decimal | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> _Share:
    return _Share(
        _UNALLOCATED,
        weight,
        AllocationCategory.UNALLOCATED,
        reason,
        coverage=coverage,
        evidence_refs=evidence_refs,
    )


def _shares(pooled: PooledLine) -> list[_Share]:
    pool = pooled.pool
    match pool:
        case MeteredCostPool():
            return _metered_shares(pooled.ref, pooled.line, pool)
        case DirectCostPool():
            return [
                _Share(
                    _query(pool.query_execution_id),
                    Decimal(1),
                    AllocationCategory.DIRECT,
                    AllocationMethod.PROVIDER_LINE_MATCH,
                    confidence=pool.confidence,
                    evidence_refs=pool.evidence_refs,
                )
            ]
        case SharedCostPool():
            return _weighted_shares(pooled.ref, pool)
        case IdleCostPool():
            return [
                _Share(
                    _IDLE,
                    Decimal(1),
                    AllocationCategory.IDLE,
                    AllocationMethod.NO_OBSERVED_ACTIVITY,
                )
            ]
        case Ineligible():
            return [_unallocated(AllocationMethod.INELIGIBLE)]
        case Adjustment():
            return [_unallocated(AllocationMethod.ADJUSTMENT)]
    assert_never(pool)


def _weighted_shares(ref: str, pool: SharedCostPool) -> list[_Share]:
    """Shares by explicit nonnegative weights; zero weights receive nothing, and a pool with
    no positive weight is Unallocated `no_evidence`, never divided."""
    if any(w.weight < 0 for w in pool.weights):
        raise ValueError(f"{ref}: shared weights must be nonnegative")
    positive = [w for w in pool.weights if w.weight > 0]
    if not positive:
        return [_unallocated(AllocationMethod.NO_EVIDENCE, evidence_refs=pool.evidence_refs)]
    return [
        _Share(
            _query(w.query_execution_id),
            w.weight,
            AllocationCategory.SHARED,
            AllocationMethod.WEIGHTED_SHARE,
            confidence=pool.confidence,
            evidence_refs=pool.evidence_refs,
        )
        for w in positive
    ]


def _metered_shares(ref: str, line: FocusBillingLine, pool: MeteredCostPool) -> list[_Share]:
    """Shares of a metered line by Coverage (ADR 0017).

    Coverage is known over billed usage in the line's unit, to 28 significant digits, and
    counts as exactly 1 within half a unit in the last digit ConsumedQuantity was exported
    with. Below one, the uncovered quantity weighs on Unallocated; above one, Unallocated
    takes everything and keeps the conflicting Query Executions' Evidence. Query
    Executions with no Billable Usage receive nothing.
    """
    billed = line.ConsumedQuantity
    if billed is None or billed <= 0:
        raise ValueError(f"{ref}: a metered line needs a positive ConsumedQuantity for Coverage")
    usages = [u for u in pool.usages if u.quantity > 0]
    known = sum((u.quantity for u in usages), Decimal(0))
    if abs(known - billed) <= Decimal(5).scaleb(-_places(billed) - 1):
        coverage = Decimal(1)
    else:
        with localcontext() as context:
            context.prec = 28
            coverage = known / billed
    if coverage > 1:
        evidence = tuple(dict.fromkeys(ref for u in usages for ref in u.evidence_refs))
        return [
            _unallocated(
                AllocationMethod.COVERAGE_EXCEEDED, coverage=coverage, evidence_refs=evidence
            )
        ]
    shares = [
        _Share(
            _query(u.query_execution_id),
            u.quantity,
            AllocationCategory.DIRECT,
            AllocationMethod.ESTIMATED_METERED_SHARE,
            confidence=Confidence.METERED_ESTIMATE,
            coverage=coverage,
            evidence_refs=u.evidence_refs,
        )
        for u in usages
    ]
    if coverage < 1:
        shares.append(
            _unallocated(AllocationMethod.COVERAGE_REMAINDER, billed - known, coverage=coverage)
        )
    return shares


def _scale(amount: Decimal, currency: str) -> int:
    """The amount's decimal places as exported, never coarser than the currency minor unit."""
    return max(_places(amount), _MINOR_UNITS.get(currency, 2))


def _places(value: Decimal) -> int:
    """Decimal places as written: `0.0010` has 4."""
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):  # DecimalString admits only finite values
        raise ValueError(f"not a finite decimal: {value}")
    return -exponent


def _split(
    amount: Decimal, weights: Mapping[_Target, Decimal], scale: int
) -> dict[_Target, Decimal]:
    """Split `amount` by `weights` at `scale` decimal places; the parts sum to it exactly.

    Largest remainder (spec 03 step G) on the absolute value, sign restored: every target
    gets its share rounded down, then the leftover units go one each to the largest
    discarded fractions, ties to the lowest (target type, target ID).
    """
    unit = Decimal(1).scaleb(-scale)
    magnitude = abs(amount)
    total = sum(weights.values(), Decimal(0))
    with localcontext() as context:
        context.prec = 60
        exact = {target: magnitude * weight / total for target, weight in weights.items()}
        parts = {
            target: share.quantize(unit, rounding=ROUND_FLOOR) for target, share in exact.items()
        }
        leftover = int((magnitude - sum(parts.values(), Decimal(0))) / unit)
        by_fraction = sorted(parts, key=lambda target: (parts[target] - exact[target], target))
        for target in by_fraction[:leftover]:
            parts[target] += unit
    return {target: -part if amount < 0 else part for target, part in parts.items()}
