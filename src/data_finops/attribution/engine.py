"""The allocation engine (spec 03, ADRs 0016 and 0017): Billing Lines in, Allocations out.

Pure: no I/O, clock or provider knowledge. Callers classify each Billing Line as a
metered Cost Pool or Ineligible and give each Query Execution's Billable Usage in the
line's own unit; the engine only splits money.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, localcontext
from enum import StrEnum

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


_UNALLOCATED = TargetType.UNALLOCATED.value


class AllocationMethod(StrEnum):
    """How an amount was allocated; for Unallocated amounts, why (spec 03, M4 structure 10)."""

    ESTIMATED_METERED_SHARE = "estimated_metered_share"
    INELIGIBLE = "ineligible"
    COVERAGE_REMAINDER = "coverage_remainder"
    COVERAGE_EXCEEDED = "coverage_exceeded"


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
class Ineligible:
    """A line no MVP policy allocates; it stays Unallocated under `pool_id`."""

    pool_id: str


@dataclass(frozen=True)
class PooledLine:
    """A Billing Line, its reference (`<snapshot_id>#<position>`) and how it is classified."""

    ref: str
    line: FocusBillingLine
    pool: MeteredCostPool | Ineligible


def allocate(
    lines: Iterable[PooledLine], *, policy_version: str, calculation_run_id: str
) -> list[Allocation]:
    """Partition every line, for every Cost Basis, among its Allocation Targets.

    A metered line is split by Coverage (ADR 0017): its covered fraction among the Query
    Executions by Billable Usage, the rest Unallocated `coverage_remainder`; a line whose
    Coverage exceeds one is wholly Unallocated `coverage_exceeded`. Every Allocation of a
    metered line records its Coverage. Raises ValueError for a metered line without a
    positive ConsumedQuantity.
    """
    allocations: list[Allocation] = []
    for pooled in lines:
        line, pool = pooled.line, pooled.pool
        if isinstance(pool, Ineligible):
            allocations += [
                Allocation(
                    billing_line_ref=pooled.ref,
                    pool_id=pool.pool_id,
                    target_type=TargetType.UNALLOCATED,
                    target_id=TargetType.UNALLOCATED.value,
                    amount=line.cost(basis),
                    currency=line.BillingCurrency,
                    cost_basis=basis,
                    category=AllocationCategory.UNALLOCATED,
                    method=AllocationMethod.INELIGIBLE,
                    policy_version=policy_version,
                    calculation_run_id=calculation_run_id,
                )
                for basis in CostBasis
            ]
            continue
        coverage, weights = _coverage(pooled.ref, line, pool)
        for basis in CostBasis:
            amount = line.cost(basis)
            shares = _split(amount, weights, _scale(amount, line.BillingCurrency))
            allocations += [
                Allocation(
                    billing_line_ref=pooled.ref,
                    pool_id=pool.pool_id,
                    target_type=TargetType.QUERY_EXECUTION,
                    target_id=usage.query_execution_id,
                    amount=shares[usage.query_execution_id],
                    currency=line.BillingCurrency,
                    cost_basis=basis,
                    category=AllocationCategory.DIRECT,
                    method=AllocationMethod.ESTIMATED_METERED_SHARE,
                    confidence=Confidence.METERED_ESTIMATE,
                    coverage=coverage,
                    policy_version=policy_version,
                    calculation_run_id=calculation_run_id,
                    evidence_refs=list(usage.evidence_refs),
                )
                for usage in pool.usages
                if usage.query_execution_id in shares
            ]
            if _UNALLOCATED in shares:
                allocations.append(
                    Allocation(
                        billing_line_ref=pooled.ref,
                        pool_id=pool.pool_id,
                        target_type=TargetType.UNALLOCATED,
                        target_id=_UNALLOCATED,
                        amount=shares[_UNALLOCATED],
                        currency=line.BillingCurrency,
                        cost_basis=basis,
                        category=AllocationCategory.UNALLOCATED,
                        method=AllocationMethod.COVERAGE_EXCEEDED
                        if coverage > 1
                        else AllocationMethod.COVERAGE_REMAINDER,
                        coverage=coverage,
                        policy_version=policy_version,
                        calculation_run_id=calculation_run_id,
                    )
                )
    return allocations


def _coverage(
    ref: str, line: FocusBillingLine, pool: MeteredCostPool
) -> tuple[Decimal, dict[str, Decimal]]:
    """The line's Coverage and the weights its amount is split by (ADR 0017).

    Coverage is known over billed usage in the line's unit, and counts as exactly 1 within
    half a unit in the last digit ConsumedQuantity was exported with. Below one, the
    uncovered quantity weighs on Unallocated; above one, Unallocated takes everything.
    Query Executions with no Billable Usage receive nothing.
    """
    billed = line.ConsumedQuantity
    if billed is None or billed <= 0:
        raise ValueError(f"{ref}: a metered line needs a positive ConsumedQuantity for Coverage")
    usage = {u.query_execution_id: u.quantity for u in pool.usages if u.quantity > 0}
    known = sum(usage.values(), Decimal(0))
    if abs(known - billed) <= Decimal(5).scaleb(-_places(billed) - 1):
        return Decimal(1), usage
    coverage = known / billed
    if coverage > 1:
        return coverage, {_UNALLOCATED: Decimal(1)}
    return coverage, usage | {_UNALLOCATED: billed - known}


def _scale(amount: Decimal, currency: str) -> int:
    """The amount's decimal places as exported, never coarser than the currency minor unit."""
    return max(_places(amount), _MINOR_UNITS.get(currency, 2))


def _places(value: Decimal) -> int:
    """Decimal places as written: `0.0010` has 4."""
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):  # DecimalString admits only finite values
        raise ValueError(f"not a finite decimal: {value}")
    return -exponent


def _split(amount: Decimal, weights: Mapping[str, Decimal], scale: int) -> dict[str, Decimal]:
    """Split `amount` by `weights` at `scale` decimal places; the parts sum to it exactly.

    Largest remainder (spec 03 step G) on the absolute value, sign restored: every target
    gets its share rounded down, then the leftover units go one each to the largest
    discarded fractions, ties to the lowest target ID.
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
