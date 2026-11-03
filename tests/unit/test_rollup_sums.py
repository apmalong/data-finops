"""Roll-ups (#89; ADR 0016; spec 03, M4 structure 6): sums of Allocations, never Allocations.

Expected values are worked by hand from the Allocations each test builds.
"""

from decimal import Decimal

from data_finops.attribution.rollups import (
    ModelExecutionRef,
    RollupTotal,
    model_execution_evidence,
    model_execution_rollups,
    roll_up,
)
from data_finops.contracts.allocation import (
    Allocation,
    AllocationCategory,
    Confidence,
    TargetType,
)
from data_finops.contracts.billing import CostBasis

ORDERS = ModelExecutionRef("inv-001", "model.demo.orders")
CUSTOMERS = ModelExecutionRef("inv-001", "model.demo.customers")


def _share(
    query: str,
    amount: str,
    *evidence: str,
    currency: str = "USD",
    basis: CostBasis = CostBasis.BILLED_COST,
    category: AllocationCategory = AllocationCategory.DIRECT,
) -> Allocation:
    return Allocation(
        billing_line_ref="line#0",
        pool_id="pool",
        target_type=TargetType.QUERY_EXECUTION,
        target_id=query,
        amount=Decimal(amount),
        currency=currency,
        cost_basis=basis,
        category=category,
        method="estimated_metered_share",
        confidence=Confidence.METERED_ESTIMATE,
        policy_version="p",
        calculation_run_id="r",
        evidence_refs=[f"athena:{query}", *evidence],
    )


def _unallocated(amount: str) -> Allocation:
    return Allocation(
        billing_line_ref="line#1",
        pool_id="pool",
        target_type=TargetType.UNALLOCATED,
        target_id="unallocated",
        amount=Decimal(amount),
        currency="USD",
        cost_basis=CostBasis.BILLED_COST,
        category=AllocationCategory.UNALLOCATED,
        method="coverage_remainder",
        policy_version="p",
        calculation_run_id="r",
    )


def test_a_model_executions_rollup_sums_its_linked_queries_per_currency_and_basis() -> None:
    orders = model_execution_evidence(ORDERS)
    allocations = [
        _share("q-1", "1.25", orders),
        _share("q-2", "0.75", orders),
        _share("q-2", "0.70", orders, basis=CostBasis.EFFECTIVE_COST),
        _share("q-3", "100", orders, currency="JPY"),
        _share("q-4", "2.00", model_execution_evidence(CUSTOMERS)),
    ]
    assert model_execution_rollups(allocations) == {
        ORDERS: (
            RollupTotal("JPY", CostBasis.BILLED_COST, Decimal("100")),
            RollupTotal("USD", CostBasis.BILLED_COST, Decimal("2.00")),
            RollupTotal("USD", CostBasis.EFFECTIVE_COST, Decimal("0.70")),
        ),
        CUSTOMERS: (RollupTotal("USD", CostBasis.BILLED_COST, Decimal("2.00")),),
    }


def test_an_unlinked_query_and_synthetic_targets_roll_up_to_nothing() -> None:
    allocations = [_share("q-unlinked", "3.00"), _unallocated("4.00")]
    assert model_execution_rollups(allocations) == {}


def test_shared_allocations_roll_up_like_direct_ones() -> None:
    orders = model_execution_evidence(ORDERS)
    allocations = [_share("q-1", "1.25", orders, category=AllocationCategory.SHARED)]
    assert model_execution_rollups(allocations) == {
        ORDERS: (RollupTotal("USD", CostBasis.BILLED_COST, Decimal("1.25")),)
    }


def test_negative_amounts_net_within_a_rollup() -> None:
    orders = model_execution_evidence(ORDERS)
    allocations = [_share("q-1", "2.00", orders), _share("q-2", "-0.50", orders)]
    assert model_execution_rollups(allocations) == {
        ORDERS: (RollupTotal("USD", CostBasis.BILLED_COST, Decimal("1.50")),)
    }


def test_roll_up_counts_an_allocation_once_per_group() -> None:
    allocations = [_share("q-1", "1.00"), _share("q-2", "2.00")]
    groups = {"q-1": ["run-a", "run-b", "run-a"], "q-2": ["run-b"]}
    assert roll_up(allocations, lambda a: groups.get(a.target_id, [])) == {
        "run-a": (RollupTotal("USD", CostBasis.BILLED_COST, Decimal("1.00")),),
        "run-b": (RollupTotal("USD", CostBasis.BILLED_COST, Decimal("3.00")),),
    }


def test_the_evidence_format_names_the_model_execution() -> None:
    assert model_execution_evidence(ORDERS) == "dbt-query-comment:inv-001:model.demo.orders"


def test_roll_up_never_groups_synthetic_targets() -> None:
    assert roll_up([_unallocated("4.00")], lambda a: ["run-a"]) == {}


def test_only_well_formed_model_execution_evidence_links() -> None:
    allocations = [
        _share("q-1", "1.00", "explicit-price:inv-001:model.demo.orders"),
        _share("q-2", "1.00", "dbt-query-comment:inv-001:"),
        _share("q-3", "1.00", "dbt-query-comment::model.demo.orders"),
    ]
    assert model_execution_rollups(allocations) == {}
