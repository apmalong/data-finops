"""The accounting invariants, for any input (#242; spec 03, ADRs 0016, 0017 and 0031).

The seam is the public calculation and roll-up functions: `allocate`, `roll_up` through
`model_execution_rollups`, `resolve` and `dimension_totals`. Hypothesis generates the
Billing Lines, their pools, the Workload links and the Cost Dimension claims. Each oracle
comes from the spec text, never from the engine's own arithmetic:
- a line's parts sum to its cost exactly, per Cost Basis, in its currency, with its sign
- each part is whole at the line's scale: its exported places, never coarser than the
  currency's ISO 4217 minor unit
- largest remainder: each part is within one unit of its exact share
- Coverage (ADR 0017): within half a unit in ConsumedQuantity's last digit nothing is
  Unallocated; below, one remainder; above, everything is Unallocated
- Roll-ups and the unlinked Query Execution Allocations sum to all Query Execution
  Allocations
- a Cost Dimension's totals, over every value and source, sum to the snapshot's

A failure that Hypothesis finds is kept as an `@example`, so it is checked on every run.
"""

from collections import defaultdict
from collections.abc import Iterable
from decimal import ROUND_FLOOR, Decimal, localcontext
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from data_finops.attribution.dimensions import dimension_totals, resolve
from data_finops.attribution.engine import (
    Adjustment,
    DirectCostPool,
    IdleCostPool,
    Ineligible,
    MeteredCostPool,
    MeteredUsage,
    PooledLine,
    SharedCostPool,
    SharedWeight,
    allocate,
)
from data_finops.attribution.rollups import (
    model_execution_evidence,
    model_execution_of,
    model_execution_rollups,
)
from data_finops.billing.focus import load_export
from data_finops.contracts.allocation import Allocation, Confidence, TargetType
from data_finops.contracts.billing import CostBasis, FocusBillingLine
from data_finops.contracts.dimensions import CostDimensions, Dimension, Source, SourceKind
from data_finops.identity.links import ModelExecutionKey

ROOT = Path(__file__).resolve().parents[2]
BASE_LINE = load_export(ROOT / "fixtures" / "focus" / "export-2026-10.json").lines[0]

MINOR_UNITS = {"USD": 2, "EUR": 2, "JPY": 0, "KWD": 3}
"""ISO 4217 minor units of the generated currencies (the oracle, not the engine's table)."""

QUERIES = [f"q-{n}" for n in range(6)]
MODELS = [ModelExecutionKey("inv-1", f"model.demo.m{n}") for n in range(3)]
Totals = dict[tuple[str, str], Decimal]


def _decimal(digits: int, places: int) -> Decimal:
    return Decimal(digits).scaleb(-places)


def _places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    assert isinstance(exponent, int)
    return -exponent


amounts = st.builds(_decimal, st.integers(-(10**9), 10**9), st.integers(0, 6))
"""Any signed cost, 0 to 6 decimal places, as an export writes it."""
quantities = st.builds(_decimal, st.integers(0, 10**7), st.integers(0, 6))
"""A nonnegative quantity in the line's unit, 0 to 6 decimal places."""
billed_quantities = quantities.filter(lambda q: q > 0)
evidence = st.lists(st.sampled_from(MODELS), max_size=1).map(
    lambda models: tuple(model_execution_evidence(m) for m in models)
)
"""A Query Execution's Evidence: linked to one Model Execution, or to none."""


@st.composite
def lines(draw: st.DrawFn) -> FocusBillingLine:
    currency = draw(st.sampled_from(sorted(MINOR_UNITS)))
    costs = {basis.value: draw(amounts) for basis in CostBasis}
    return BASE_LINE.model_copy(
        update=costs | {"BillingCurrency": currency, "ConsumedQuantity": draw(billed_quantities)}
    )


@st.composite
def metered_pools(draw: st.DrawFn) -> MeteredCostPool:
    queries = draw(st.lists(st.sampled_from(QUERIES), unique=True, max_size=5))
    usages = tuple(MeteredUsage(q, draw(quantities), draw(evidence)) for q in queries)
    return MeteredCostPool("metered", usages)


@st.composite
def shared_pools(draw: st.DrawFn) -> SharedCostPool:
    queries = draw(st.lists(st.sampled_from(QUERIES), unique=True, max_size=5))
    weights = tuple(SharedWeight(q, draw(quantities)) for q in queries)
    return SharedCostPool("shared", weights, Confidence.HEURISTIC_ESTIMATE)


pools = st.one_of(
    metered_pools(),
    shared_pools(),
    st.builds(
        DirectCostPool,
        st.just("direct"),
        st.sampled_from(QUERIES),
        st.just(Confidence.DIRECT_VERIFIED),
    ),
    st.just(IdleCostPool("idle")),
    st.just(Ineligible("ineligible")),
    st.just(Adjustment("adjustment")),
)


@st.composite
def snapshots(draw: st.DrawFn) -> list[PooledLine]:
    count = draw(st.integers(1, 4))
    return [PooledLine(f"snap#{n}", draw(lines()), draw(pools)) for n in range(count)]


def _allocate(pooled: list[PooledLine]) -> list[Allocation]:
    return allocate(pooled, policy_version="test/1", calculation_run_id="calc-test")


def _of(allocations: list[Allocation], ref: str, basis: CostBasis) -> list[Allocation]:
    return [a for a in allocations if a.billing_line_ref == ref and a.cost_basis == basis]


def _totals(allocations: Iterable[Allocation]) -> Totals:
    totals: Totals = defaultdict(Decimal)
    for allocation in allocations:
        totals[(allocation.currency, allocation.cost_basis)] += allocation.amount
    return dict(totals)


def _exact_shares(pooled: PooledLine) -> dict[str, Decimal] | None:
    """Each target's exact fraction of the line, from the spec's rule for its pool, or
    None when the whole line goes to one target."""
    pool = pooled.pool
    if isinstance(pool, SharedCostPool):
        total = sum((w.weight for w in pool.weights), Decimal(0))
        if total == 0:
            return None
        return {w.query_execution_id: w.weight / total for w in pool.weights if w.weight > 0}
    if isinstance(pool, MeteredCostPool):
        billed = pooled.line.ConsumedQuantity
        assert billed is not None
        known = sum((u.quantity for u in pool.usages), Decimal(0))
        if known > billed + _half_unit(billed):
            return None
        fractions = {u.query_execution_id: u.quantity / billed for u in pool.usages if u.quantity}
        if known < billed - _half_unit(billed):
            fractions[TargetType.UNALLOCATED.value] = (billed - known) / billed
        else:  # Coverage counts as exactly 1: the Query Executions share the whole line
            fractions = {q: f * billed / known for q, f in fractions.items()}
        return fractions
    return None


def _half_unit(quantity: Decimal) -> Decimal:
    """Half a unit in the last digit that `quantity` was exported with (ADR 0017)."""
    return Decimal(5).scaleb(-_places(quantity) - 1)


@given(snapshots())
def test_every_billing_line_partitions_exactly_per_cost_basis(pooled: list[PooledLine]) -> None:
    allocations = _allocate(pooled)
    for line in pooled:
        for basis in CostBasis:
            parts = _of(allocations, line.ref, basis)
            cost = line.line.cost(basis)
            assert parts, "every line has at least one Allocation for each Cost Basis"
            assert sum((p.amount for p in parts), Decimal(0)) == cost
            assert {p.currency for p in parts} == {line.line.BillingCurrency}
            assert all(p.amount == 0 or (p.amount < 0) == (cost < 0) for p in parts)


@given(snapshots())
def test_every_amount_is_whole_at_the_lines_scale(pooled: list[PooledLine]) -> None:
    allocations = _allocate(pooled)
    for line in pooled:
        for basis in CostBasis:
            cost = line.line.cost(basis)
            unit = Decimal(1).scaleb(-max(_places(cost), MINOR_UNITS[line.line.BillingCurrency]))
            for part in _of(allocations, line.ref, basis):
                assert part.amount % unit == 0, (part.amount, unit)


@given(snapshots())
def test_each_part_is_within_one_unit_of_its_exact_share(pooled: list[PooledLine]) -> None:
    allocations = _allocate(pooled)
    for line in pooled:
        shares = _exact_shares(line)
        for basis in CostBasis:
            cost = line.line.cost(basis)
            unit = Decimal(1).scaleb(-max(_places(cost), MINOR_UNITS[line.line.BillingCurrency]))
            parts = _of(allocations, line.ref, basis)
            if shares is None:
                assert [p.amount for p in parts] == [cost]
                continue
            assert {p.target_id for p in parts} == set(shares)
            for part in parts:
                assert abs(part.amount - cost * shares[part.target_id]) < unit


@given(snapshots())
def test_the_leftover_units_go_to_the_largest_discarded_fractions(
    pooled: list[PooledLine],
) -> None:
    # Largest remainder (spec 03 step G), on the magnitude: each target gets its share
    # rounded down, and each leftover unit goes to a target whose discarded fraction is at
    # least as large as that of every target that gets none.
    allocations = _allocate(pooled)
    with localcontext() as context:
        context.prec = 60
        for line in pooled:
            shares = _exact_shares(line)
            if shares is None:
                continue
            for basis in CostBasis:
                cost = line.line.cost(basis)
                places = max(_places(cost), MINOR_UNITS[line.line.BillingCurrency])
                unit = Decimal(1).scaleb(-places)
                fractions: dict[bool, list[Decimal]] = {True: [], False: []}
                for part in _of(allocations, line.ref, basis):
                    exact = abs(cost) * shares[part.target_id]
                    floor = exact.quantize(unit, rounding=ROUND_FLOOR)
                    fractions[abs(part.amount) > floor].append(exact - floor)
                if fractions[True] and fractions[False]:
                    assert min(fractions[True]) >= max(fractions[False])


@st.composite
def near_full_coverage(draw: st.DrawFn) -> tuple[FocusBillingLine, MeteredCostPool]:
    """A metered line whose known usage is within about one unit of ConsumedQuantity's last
    digit: the band where ADR 0017's half-unit rule decides."""
    line = draw(lines())
    billed = line.ConsumedQuantity
    assert billed is not None
    tenths = Decimal(draw(st.integers(-12, 12))).scaleb(-_places(billed) - 1)
    known = billed + tenths
    queries = draw(st.lists(st.sampled_from(QUERIES), unique=True, min_size=1, max_size=4))
    usages = [MeteredUsage(queries[0], max(known, Decimal(0)), draw(evidence))]
    usages += [MeteredUsage(q, Decimal(0)) for q in queries[1:]]
    return line, MeteredCostPool("metered", tuple(usages))


@given(st.one_of(st.tuples(lines(), metered_pools()), near_full_coverage()))
def test_coverage_decides_metered_or_unallocated(
    metered: tuple[FocusBillingLine, MeteredCostPool],
) -> None:
    line, pool = metered
    billed = line.ConsumedQuantity
    assert billed is not None
    known = sum((u.quantity for u in pool.usages), Decimal(0))
    allocations = _allocate([PooledLine("snap#0", line, pool)])
    unallocated = [a for a in allocations if a.target_type is TargetType.UNALLOCATED]
    queries = {a.target_id for a in allocations if a.target_type is TargetType.QUERY_EXECUTION}
    if abs(known - billed) <= _half_unit(billed):
        assert not unallocated
        assert {a.coverage for a in allocations} == {Decimal(1)}
    elif known < billed:
        assert {a.method for a in unallocated} == {"coverage_remainder"}
        assert len(unallocated) == len(CostBasis)
    else:
        assert not queries
        assert {a.method for a in unallocated} == {"coverage_exceeded"}
    assert queries <= {u.query_execution_id for u in pool.usages if u.quantity > 0}


@given(snapshots())
def test_roll_ups_and_unlinked_allocations_sum_to_every_query_executions(
    pooled: list[PooledLine],
) -> None:
    allocations = _allocate(pooled)
    to_queries = [a for a in allocations if a.target_type is TargetType.QUERY_EXECUTION]
    rolled: Totals = defaultdict(Decimal)
    for totals in model_execution_rollups(allocations).values():
        for total in totals:
            rolled[(total.currency, total.cost_basis.value)] += total.amount
    for key, amount in _totals(a for a in to_queries if model_execution_of(a) is None).items():
        rolled[key] += amount
    assert {k: v for k, v in rolled.items() if v} == {
        k: v for k, v in _totals(to_queries).items() if v
    }


TEAM = CostDimensions(
    (
        Dimension(
            "team",
            (Source(SourceKind.QUERY_COMMENT, "team"), Source(SourceKind.WORKGROUP_MAP)),
            workgroup_map={"demo": "platform"},
        ),
    )
)


@given(
    snapshots(),
    st.dictionaries(st.sampled_from(QUERIES), st.sampled_from(["data", "finance", ""])),
    st.sampled_from(["demo", "other", None]),
)
def test_a_cost_dimensions_totals_sum_to_the_snapshots(
    pooled: list[PooledLine], teams: dict[str, str], workgroup: str | None
) -> None:
    allocations = [
        a.model_copy(
            update={
                "dimensions": resolve(
                    TEAM,
                    claims={"team": teams[a.target_id]} if a.target_id in teams else {},
                    tags=[],
                    workgroup=workgroup,
                )
            }
        )
        for a in _allocate(pooled)
    ]
    totals = dimension_totals(allocations, "team")
    assert totals is not None
    summed: Totals = defaultdict(Decimal)
    for by_basis in totals.values():
        for key, by_source in by_basis.items():
            summed[key] += sum(by_source.values(), Decimal(0))
    assert dict(summed) == _totals(allocations)
