"""The allocation engine (spec 03, ADRs 0016 and 0017): Billing Lines in, Allocations out.

Pure: no I/O, clock or provider knowledge. Callers classify each Billing Line into a
pool and give each Query Execution's Billable Usage in the line's own unit; the engine
only splits money.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, localcontext

from data_finops.contracts.allocation import (
    Allocation,
    AllocationCategory,
    Confidence,
    TargetType,
)
from data_finops.contracts.billing import CostBasis, FocusBillingLine

METERED_SHARE = "estimated_metered_share"
INELIGIBLE = "ineligible"

# ISO 4217 (list one) minor units that are not 2; every other currency has 2.
_MINOR_UNITS = {
    **dict.fromkeys(
        "BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX UYI VND VUV XAF XOF XPF".split(), 0
    ),
    **dict.fromkeys("BHD IQD JOD KWD LYD OMR TND".split(), 3),
    **dict.fromkeys("CLF UYW".split(), 4),
}


@dataclass(frozen=True)
class MeteredUsage:
    """One Query Execution's Billable Usage, in the line's ConsumedUnit, with its Evidence."""

    query_execution_id: str
    quantity: Decimal
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class MeteredPool:
    """An aggregated line split by the Billable Usage of the Query Executions in its scope."""

    pool_id: str
    usages: tuple[MeteredUsage, ...]


@dataclass(frozen=True)
class IneligiblePool:
    """A line no MVP policy allocates; it stays Unallocated."""

    pool_id: str


Pool = MeteredPool | IneligiblePool


@dataclass(frozen=True)
class PooledLine:
    """A Billing Line, its reference (`<snapshot_id>#<position>`) and the pool it falls in."""

    ref: str
    line: FocusBillingLine
    pool: Pool


def allocate(
    lines: Iterable[PooledLine], *, policy_version: str, calculation_run_id: str
) -> list[Allocation]:
    """Partition every line, for every Cost Basis, among its Allocation Targets."""
    allocations: list[Allocation] = []
    for pooled in lines:
        common = {
            "billing_line_ref": pooled.ref,
            "pool_id": pooled.pool.pool_id,
            "currency": pooled.line.BillingCurrency,
            "policy_version": policy_version,
            "calculation_run_id": calculation_run_id,
        }
        for basis in CostBasis:
            amount: Decimal = getattr(pooled.line, basis.value)
            if isinstance(pooled.pool, IneligiblePool):
                allocations.append(
                    Allocation(
                        **common,
                        cost_basis=basis,
                        target_type=TargetType.UNALLOCATED,
                        target_id=TargetType.UNALLOCATED.value,
                        amount=amount,
                        category=AllocationCategory.UNALLOCATED,
                        method=INELIGIBLE,
                    )
                )
                continue
            usages = [u for u in pooled.pool.usages if u.quantity > 0]
            known = sum((u.quantity for u in usages), Decimal(0))
            coverage = known / _billed_quantity(pooled)
            shares = _split(
                amount,
                {u.query_execution_id: u.quantity for u in usages},
                _scale(amount, pooled.line.BillingCurrency),
            )
            for usage in usages:
                allocations.append(
                    Allocation(
                        **common,
                        cost_basis=basis,
                        target_type=TargetType.QUERY_EXECUTION,
                        target_id=usage.query_execution_id,
                        amount=shares[usage.query_execution_id],
                        category=AllocationCategory.DIRECT,
                        method=METERED_SHARE,
                        confidence=Confidence.METERED_ESTIMATE,
                        coverage=coverage,
                        evidence_refs=list(usage.evidence_refs),
                    )
                )
    return allocations


def _billed_quantity(pooled: PooledLine) -> Decimal:
    if pooled.line.ConsumedQuantity is None:
        raise ValueError(f"{pooled.ref}: a metered line needs ConsumedQuantity for Coverage")
    return pooled.line.ConsumedQuantity


def _scale(amount: Decimal, currency: str) -> int:
    """The line's own decimal places, never coarser than the currency minor unit."""
    exponent = amount.as_tuple().exponent
    if not isinstance(exponent, int):  # DecimalString admits only finite values
        raise ValueError(f"not a finite amount: {amount}")
    return max(-exponent, _MINOR_UNITS.get(currency, 2))


def _split(amount: Decimal, weights: Mapping[str, Decimal], scale: int) -> dict[str, Decimal]:
    """Split `amount` by `weights` at `scale` decimal places; the parts sum to it exactly.

    Largest remainder (spec 03 step G): every target gets its share rounded down, then the
    leftover units go one each to the largest discarded fractions, ties to the lowest
    target ID.
    """
    unit = Decimal(1).scaleb(-scale)
    total = sum(weights.values(), Decimal(0))
    with localcontext() as context:
        context.prec = 60
        exact = {target: amount * weight / total for target, weight in weights.items()}
        parts = {
            target: share.quantize(unit, rounding=ROUND_FLOOR) for target, share in exact.items()
        }
        leftover = int((amount - sum(parts.values(), Decimal(0))) / unit)
        by_fraction = sorted(parts, key=lambda target: (parts[target] - exact[target], target))
        for target in by_fraction[:leftover]:
            parts[target] += unit
    return parts
