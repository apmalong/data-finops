"""The accounting invariants, for any input (#242; spec 03, ADRs 0016, 0017 and 0031).

The seam is the public calculation and roll-up functions: `allocate`, the Model Execution
and Workload Run Roll-ups, and `dimension_totals`. Hypothesis generates the Billing Lines,
their pools, the Workload Records that Evidence links (Model Executions and Workload Runs)
and the Cost Dimension claims. Each property's oracle comes from the spec text:
- a line's parts sum to its cost exactly, per Cost Basis, in its currency, with its sign
- each part is whole at the line's scale: its exported places, never coarser than the
  currency's ISO 4217 minor unit
- largest remainder: each part is within one unit of its exact share, and the leftover
  units go to the largest discarded fractions
- Coverage (ADR 0017): within half a unit in ConsumedQuantity's last digit nothing is
  Unallocated. Below, one remainder. Above, everything is Unallocated
- a line that goes whole to one target goes to the target and method its pool names
- each Roll-up is the sum of the Allocations whose Evidence names its group, once each
- a Cost Dimension's totals, over every value and source, sum to the snapshot's

The half-unit band, the scale and the tie-break restate the spec's rules, so the
hand-worked examples at the end pin them with literal amounts. A failure that Hypothesis
finds is kept as an `@example` of its property. None was found when this file was written:
200 examples a property in the `ci` profile, and 20,000 in the `long` one.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from decimal import ROUND_FLOOR, Decimal, localcontext
from pathlib import Path

import pytest
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
    RollupTotal,
    model_execution_evidence,
    model_execution_rollups,
    workload_run_evidence,
    workload_run_rollups,
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
RUNS = [f"00000000-0000-4000-8000-00000000000{n}" for n in range(4)]
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


@st.composite
def evidence(draw: st.DrawFn) -> tuple[str, ...]:
    """The Workload Records that one Query Execution's Evidence links: one Model Execution or
    none, then Workload Runs nearest first, a run sometimes named twice."""
    models = draw(st.lists(st.sampled_from(MODELS), max_size=1))
    runs = draw(st.lists(st.sampled_from(RUNS), max_size=3))
    return tuple(model_execution_evidence(m) for m in models) + tuple(
        workload_run_evidence(r) for r in runs
    )


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
    usages = tuple(MeteredUsage(q, draw(quantities), draw(evidence())) for q in queries)
    return MeteredCostPool("metered", usages)


@st.composite
def shared_pools(draw: st.DrawFn) -> SharedCostPool:
    queries = draw(st.lists(st.sampled_from(QUERIES), unique=True, max_size=5))
    weights = tuple(SharedWeight(q, draw(quantities)) for q in queries)
    return SharedCostPool("shared", weights, Confidence.HEURISTIC_ESTIMATE, draw(evidence()))


pools = st.one_of(
    metered_pools(),
    shared_pools(),
    st.builds(
        DirectCostPool,
        st.just("direct"),
        st.sampled_from(QUERIES),
        st.just(Confidence.DIRECT_VERIFIED),
        evidence(),
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


def _unit(pooled_line: PooledLine, basis: CostBasis) -> Decimal:
    """One unit at the line's scale for `basis` (spec 03 step G)."""
    line = pooled_line.line
    places = max(_places(line.cost(basis)), MINOR_UNITS[line.BillingCurrency])
    return Decimal(1).scaleb(-places)


def _half_unit(quantity: Decimal) -> Decimal:
    """Half a unit in the last digit that `quantity` was exported with (ADR 0017)."""
    return Decimal(5).scaleb(-_places(quantity) - 1)


def _exact_shares(pooled_line: PooledLine) -> dict[str, Decimal] | None:
    """Each target's exact fraction of the line, from the spec's rule for its pool, or
    None when the whole line goes to one target."""
    pool = pooled_line.pool
    if isinstance(pool, SharedCostPool):
        total = sum((w.weight for w in pool.weights), Decimal(0))
        if total == 0:
            return None
        return {w.query_execution_id: w.weight / total for w in pool.weights if w.weight > 0}
    if isinstance(pool, MeteredCostPool):
        billed = pooled_line.line.ConsumedQuantity
        assert billed is not None
        known = sum((u.quantity for u in pool.usages), Decimal(0))
        if known > billed + _half_unit(billed):
            return None
        if known < billed - _half_unit(billed):  # Coverage below one: the rest is Unallocated
            fractions = {u.query_execution_id: u.quantity / billed for u in pool.usages}
            fractions[TargetType.UNALLOCATED.value] = (billed - known) / billed
        else:  # Coverage counts as exactly 1: the Query Executions share the whole line
            fractions = {u.query_execution_id: u.quantity / known for u in pool.usages}
        return {target: f for target, f in fractions.items() if f}
    return None


@given(snapshots())
def test_every_billing_line_partitions_exactly_per_cost_basis(pooled: list[PooledLine]) -> None:
    allocations = _allocate(pooled)
    for pooled_line in pooled:
        for basis in CostBasis:
            parts = _of(allocations, pooled_line.ref, basis)
            cost = pooled_line.line.cost(basis)
            assert parts, "every line has at least one Allocation for each Cost Basis"
            assert sum((p.amount for p in parts), Decimal(0)) == cost
            assert {p.currency for p in parts} == {pooled_line.line.BillingCurrency}
            assert all(p.amount == 0 or (p.amount < 0) == (cost < 0) for p in parts)


@given(snapshots())
def test_every_amount_is_whole_at_the_lines_scale(pooled: list[PooledLine]) -> None:
    allocations = _allocate(pooled)
    for pooled_line in pooled:
        for basis in CostBasis:
            unit = _unit(pooled_line, basis)
            for part in _of(allocations, pooled_line.ref, basis):
                assert part.amount % unit == 0, (part.amount, unit)


@given(snapshots())
def test_each_part_is_within_one_unit_of_its_exact_share(pooled: list[PooledLine]) -> None:
    allocations = _allocate(pooled)
    for pooled_line in pooled:
        shares = _exact_shares(pooled_line)
        if shares is None:
            continue
        for basis in CostBasis:
            cost = pooled_line.line.cost(basis)
            parts = _of(allocations, pooled_line.ref, basis)
            assert {p.target_id for p in parts} == set(shares)
            for part in parts:
                exact = cost * shares[part.target_id]
                assert abs(part.amount - exact) < _unit(pooled_line, basis)


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
        for pooled_line in pooled:
            shares = _exact_shares(pooled_line)
            if shares is None:
                continue
            for basis in CostBasis:
                cost, unit = pooled_line.line.cost(basis), _unit(pooled_line, basis)
                bumped: list[Decimal] = []
                kept: list[Decimal] = []
                for part in _of(allocations, pooled_line.ref, basis):
                    exact = abs(cost) * shares[part.target_id]
                    floor = exact.quantize(unit, rounding=ROUND_FLOOR)
                    (bumped if abs(part.amount) > floor else kept).append(exact - floor)
                if bumped and kept:
                    assert min(bumped) >= max(kept)


WHOLE_LINE = {
    DirectCostPool: (TargetType.QUERY_EXECUTION, "provider_line_match"),
    IdleCostPool: (TargetType.IDLE, "no_observed_activity"),
    Ineligible: (TargetType.UNALLOCATED, "ineligible"),
    Adjustment: (TargetType.UNALLOCATED, "adjustment"),
    SharedCostPool: (TargetType.UNALLOCATED, "no_evidence"),  # no positive weight
    MeteredCostPool: (TargetType.UNALLOCATED, "coverage_exceeded"),
}
"""Where spec 03 steps E and F send a whole line, by its pool."""


@given(snapshots())
def test_a_whole_line_goes_to_the_target_its_pool_names(pooled: list[PooledLine]) -> None:
    allocations = _allocate(pooled)
    for pooled_line in pooled:
        if _exact_shares(pooled_line) is not None:
            continue
        target_type, method = WHOLE_LINE[type(pooled_line.pool)]
        for basis in CostBasis:
            (part,) = _of(allocations, pooled_line.ref, basis)
            assert part.amount == pooled_line.line.cost(basis)
            assert (part.target_type, part.method) == (target_type, method)
            if isinstance(pooled_line.pool, DirectCostPool):
                assert part.target_id == pooled_line.pool.query_execution_id


@st.composite
def near_full_coverage(draw: st.DrawFn) -> tuple[FocusBillingLine, MeteredCostPool]:
    """A metered line whose known usage is within about one unit of ConsumedQuantity's last
    digit: the band where ADR 0017's half-unit rule decides."""
    line = draw(lines())
    billed = line.ConsumedQuantity
    assert billed is not None
    known = billed + Decimal(draw(st.integers(-12, 12))).scaleb(-_places(billed) - 1)
    queries = draw(st.lists(st.sampled_from(QUERIES), unique=True, min_size=1, max_size=4))
    usages = [MeteredUsage(queries[0], max(known, Decimal(0)), draw(evidence()))]
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
        assert all(a.coverage is not None and a.coverage < 1 for a in allocations)
    else:
        assert not queries
        assert {a.method for a in unallocated} == {"coverage_exceeded"}
        assert all(a.coverage is not None and a.coverage > 1 for a in allocations)
    assert queries <= {u.query_execution_id for u in pool.usages if u.quantity > 0}


def _rolled_up[Group](groups: Mapping[Group, tuple[RollupTotal, ...]]) -> dict[Group, Totals]:
    return {
        group: {(t.currency, t.cost_basis.value): t.amount for t in totals}
        for group, totals in groups.items()
    }


@given(snapshots())
def test_each_roll_up_sums_the_allocations_whose_evidence_names_its_group(
    pooled: list[PooledLine],
) -> None:
    allocations = _allocate(pooled)
    to_queries = [a for a in allocations if a.target_type is TargetType.QUERY_EXECUTION]
    by_model = {
        model: _totals(a for a in to_queries if model_execution_evidence(model) in a.evidence_refs)
        for model in MODELS
    }
    by_run = {
        run: _totals(a for a in to_queries if workload_run_evidence(run) in a.evidence_refs)
        for run in RUNS
    }
    assert _rolled_up(model_execution_rollups(allocations)) == {
        model: totals for model, totals in by_model.items() if totals
    }
    assert _rolled_up(workload_run_rollups(allocations)) == {
        run: totals for run, totals in by_run.items() if totals
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
def test_dimension_totals_sum_to_the_snapshots(
    pooled: list[PooledLine], teams: dict[str, str], workgroup: str | None
) -> None:
    # `dimension_totals` alone; a Calculation Run's own attachment of the dimensions is
    # tested in tests/integration/test_cost_dimension_properties.py.
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


# Hand-worked examples: literal amounts for the rules that the properties restate.


def _metered_line(consumed: str, cost: str = "10.00") -> FocusBillingLine:
    costs = dict.fromkeys((basis.value for basis in CostBasis), Decimal(cost))
    return BASE_LINE.model_copy(
        update=costs | {"BillingCurrency": "USD", "ConsumedQuantity": Decimal(consumed)}
    )


@pytest.mark.parametrize(
    ("known", "q_amount", "unallocated", "method", "coverage"),
    [
        # ConsumedQuantity 1.0 was exported with one decimal place: half a unit is 0.05.
        ("1.05", "10.00", None, None, "1"),
        ("0.95", "10.00", None, None, "1"),
        ("0.94", "9.40", "0.60", "coverage_remainder", "0.94"),
        ("0.5", "5.00", "5.00", "coverage_remainder", "0.5"),
        ("1.06", None, "10.00", "coverage_exceeded", "1.06"),
    ],
)
def test_coverage_at_the_edges_of_the_half_unit_band(
    known: str, q_amount: str | None, unallocated: str | None, method: str | None, coverage: str
) -> None:
    pool = MeteredCostPool("metered", (MeteredUsage("q-1", Decimal(known)),))
    allocations = _of(
        _allocate([PooledLine("snap#0", _metered_line("1.0"), pool)]),
        "snap#0",
        CostBasis.BILLED_COST,
    )
    amounts = {a.target_id: a.amount for a in allocations}
    assert amounts.get("q-1") == (Decimal(q_amount) if q_amount else None)
    assert amounts.get("unallocated") == (Decimal(unallocated) if unallocated else None)
    assert {a.method for a in allocations if a.target_id == "unallocated"} == (
        {method} if method else set()
    )
    assert {a.coverage for a in allocations} == {Decimal(coverage)}


@pytest.mark.parametrize(
    ("cost", "expected"),
    [
        # Three equal weights of 0.10: each gets 0.03, and the leftover cent goes to the
        # lowest Query Execution ID (spec 03 step G).
        ("0.10", {"q-0": "0.04", "q-1": "0.03", "q-2": "0.03"}),
        ("-0.10", {"q-0": "-0.04", "q-1": "-0.03", "q-2": "-0.03"}),
        # 1 JPY has no minor unit: 0 each, and the one yen goes to q-0.
        ("1", {"q-0": "1", "q-1": "0", "q-2": "0"}),
    ],
)
def test_a_tie_goes_to_the_lowest_query_execution_id(cost: str, expected: dict[str, str]) -> None:
    line = _metered_line("1", cost).model_copy(
        update={"BillingCurrency": "JPY" if "." not in cost else "USD"}
    )
    weights = tuple(SharedWeight(q, Decimal(1)) for q in ("q-2", "q-0", "q-1"))
    pool = SharedCostPool("shared", weights, Confidence.HEURISTIC_ESTIMATE)
    allocations = _of(_allocate([PooledLine("snap#0", line, pool)]), "snap#0", CostBasis.LIST_COST)
    assert {a.target_id: a.amount for a in allocations} == {
        q: Decimal(amount) for q, amount in expected.items()
    }
