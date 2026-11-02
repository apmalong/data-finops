"""The allocation engine's public interface (#81; spec 03, ADRs 0016 and 0017).

The engine is pure: callers classify each Billing Line into a pool and give each Query
Execution's Billable Usage in the line's own unit. Expected values come from the golden
oracle or are worked by hand in each test.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from data_finops.attribution.engine import (
    Ineligible,
    MeteredCostPool,
    MeteredUsage,
    PooledLine,
    allocate,
)
from data_finops.billing.focus import load_export
from data_finops.contracts.allocation import Allocation
from data_finops.contracts.billing import CostBasis, FocusBillingLine

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = json.loads((ROOT / "fixtures" / "golden" / "scenarios.json").read_text())
ALLOCATION_SCHEMA = json.loads((ROOT / "schemas" / "allocation.schema.json").read_text())
EXPORT = load_export(ROOT / "fixtures" / "focus" / "export-2026-10.json")
ATHENA_LINE = EXPORT.lines[0]


def _scenario(scenario_id: str) -> dict[str, Any]:
    return next(s for s in SCENARIOS if s["id"] == scenario_id)


def _key(allocation: dict[str, Any]) -> tuple[str, str, str]:
    return allocation["billing_line_ref"], allocation["cost_basis"], allocation["target_id"]


def _dumped(allocations: list[Allocation]) -> list[dict[str, Any]]:
    return sorted((a.model_dump(mode="json") for a in allocations), key=_key)


def _line(
    every_cost: str | None = None, *, currency: str = "USD", **columns: str
) -> FocusBillingLine:
    """The fixture's Athena line with `every_cost` in all four cost columns and other
    decimal columns (a single cost column, ConsumedQuantity) replaced by name."""
    costs = dict.fromkeys((basis.value for basis in CostBasis), every_cost) if every_cost else {}
    decimals = {name: Decimal(value) for name, value in (costs | columns).items()}
    return ATHENA_LINE.model_copy(update=decimals | {"BillingCurrency": currency})


def _metered(line: FocusBillingLine, **usage: str) -> PooledLine:
    usages = tuple(MeteredUsage(query, Decimal(quantity)) for query, quantity in usage.items())
    return PooledLine("line#0", line, MeteredCostPool("pool", usages))


def _amounts(allocations: list[Allocation], basis: CostBasis) -> dict[str, str]:
    return {
        a.target_id: a.model_dump(mode="json")["amount"]
        for a in allocations
        if a.cost_basis == basis
    }


def test_existing_fixtures_golden_scenario() -> None:
    # Coverage = 1: the line's 0.001 TB equals q-001's Billable Usage (golden-fixtures.md).
    lines = [
        PooledLine(
            EXPORT.line_ref(0),
            ATHENA_LINE,
            MeteredCostPool(
                "athena-on-demand:123456789012:us-east-1:demo:2026-10-01",
                (
                    MeteredUsage(
                        "q-001",
                        Decimal("0.001"),
                        ("athena:q-001", "dbt-query-comment:inv-001:model.demo.orders"),
                    ),
                    MeteredUsage("q-002", Decimal("0")),  # DDL, nothing scanned
                ),
            ),
        ),
        PooledLine(
            EXPORT.line_ref(1),
            EXPORT.lines[1],
            Ineligible("ineligible:123456789012:2026-10-01"),
        ),
    ]

    allocations = allocate(lines, policy_version="golden-v1", calculation_run_id="golden")

    # The oracle states BilledCost; the fixture's other cost columns hold the same amounts.
    expected = [
        Allocation.model_validate(a).model_copy(update={"cost_basis": basis})
        for a in _scenario("existing-fixtures-complete-coverage")["allocations"]
        for basis in CostBasis
    ]
    assert _dumped(allocations) == _dumped(expected)


def test_remainders_go_to_the_largest_fractions_then_the_lowest_target_id() -> None:
    line = _line("1.00", ConsumedQuantity="0.003")
    allocations = allocate(
        [_metered(line, q_c="0.001", q_a="0.001", q_b="0.001")],
        policy_version="p",
        calculation_run_id="r",
    )
    assert _amounts(allocations, CostBasis.BILLED_COST) == {
        "q_a": "0.34",
        "q_b": "0.33",
        "q_c": "0.33",
    }


def test_the_largest_fraction_wins_the_remainder_before_target_order() -> None:
    # 0.10 by 1:2 is 0.0333… and 0.0666…: q_z's larger fraction takes the cent.
    line = _line("0.10", ConsumedQuantity="0.003")
    allocations = allocate(
        [_metered(line, q_a="0.001", q_z="0.002")], policy_version="p", calculation_run_id="r"
    )
    assert _amounts(allocations, CostBasis.BILLED_COST) == {"q_a": "0.03", "q_z": "0.07"}


def test_a_line_is_never_split_coarser_than_its_currency_minor_unit() -> None:
    usd = _line("1", ConsumedQuantity="0.003")
    jpy = _line("100", ConsumedQuantity="0.003", currency="JPY")
    for line, expected in ((usd, ["0.33", "0.33", "0.34"]), (jpy, ["33", "33", "34"])):
        allocations = allocate(
            [_metered(line, q_a="0.001", q_b="0.001", q_c="0.001")],
            policy_version="p",
            calculation_run_id="r",
        )
        assert sorted(_amounts(allocations, CostBasis.BILLED_COST).values()) == expected


def test_a_line_finer_than_its_minor_unit_is_split_at_its_own_scale() -> None:
    line = _line("0.0000100", ConsumedQuantity="0.003")
    allocations = allocate(
        [_metered(line, q_a="0.001", q_b="0.001", q_c="0.001")],
        policy_version="p",
        calculation_run_id="r",
    )
    assert _amounts(allocations, CostBasis.BILLED_COST) == {
        "q_a": "0.0000034",
        "q_b": "0.0000033",
        "q_c": "0.0000033",
    }


def test_every_cost_basis_is_split_by_the_same_weights_and_reconciles_on_its_own() -> None:
    line = _line(
        BilledCost="1.00",
        EffectiveCost="0.90",
        ListCost="1.25",
        ContractedCost="0.995",
        ConsumedQuantity="0.003",
    )
    allocations = allocate(
        [_metered(line, q_a="0.001", q_b="0.002")], policy_version="p", calculation_run_id="r"
    )
    assert {basis: _amounts(allocations, basis) for basis in CostBasis} == {
        CostBasis.BILLED_COST: {"q_a": "0.33", "q_b": "0.67"},
        CostBasis.EFFECTIVE_COST: {"q_a": "0.30", "q_b": "0.60"},
        CostBasis.LIST_COST: {"q_a": "0.42", "q_b": "0.83"},
        CostBasis.CONTRACTED_COST: {"q_a": "0.332", "q_b": "0.663"},
    }


def test_every_allocation_validates_against_the_published_schema() -> None:
    validator = Draft202012Validator(
        ALLOCATION_SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    lines = [
        _metered(_line("1.00", ConsumedQuantity="0.003"), q_a="0.001", q_b="0.002"),
        PooledLine("line#1", EXPORT.lines[1], Ineligible("ineligible")),
    ]
    allocations = allocate(lines, policy_version="p", calculation_run_id="r")
    assert {a.target_id for a in allocations} == {"q_a", "q_b", "unallocated"}
    for allocation in allocations:
        validator.validate(allocation.model_dump(mode="json"))


def test_a_metered_line_without_a_billed_quantity_is_refused() -> None:
    # Coverage cannot be measured (ADR 0017); the provider policy must not meter such a line.
    for quantity in (None, Decimal(0)):
        line = ATHENA_LINE.model_copy(update={"ConsumedQuantity": quantity})
        with pytest.raises(ValueError, match="ConsumedQuantity"):
            allocate([_metered(line, q_a="0.001")], policy_version="p", calculation_run_id="r")


@pytest.mark.parametrize(
    "usage",
    [{"q_a": "0.0005"}, {"q_a": "0.002"}, {"q_a": "0"}, {}],
    ids=["below-one", "above-one", "no-usage", "no-queries"],
)
def test_a_line_whose_coverage_is_not_one_is_refused_until_84(usage: dict[str, str]) -> None:
    # Coverage below and above one are #84's; until then the engine must not misallocate.
    with pytest.raises(ValueError, match="Coverage"):
        allocate(
            [_metered(_line("1.00", ConsumedQuantity="0.001"), **usage)],
            policy_version="p",
            calculation_run_id="r",
        )


def test_a_negative_amount_is_split_on_its_absolute_value_with_the_sign_restored() -> None:
    # Step G: -1.00 three ways is -0.34 to the lowest target ID, as for +1.00.
    line = _line("-1.00", ConsumedQuantity="0.003")
    allocations = allocate(
        [_metered(line, q_c="0.001", q_a="0.001", q_b="0.001")],
        policy_version="p",
        calculation_run_id="r",
    )
    assert _amounts(allocations, CostBasis.BILLED_COST) == {
        "q_a": "-0.34",
        "q_b": "-0.33",
        "q_c": "-0.33",
    }


def test_a_large_amount_keeps_every_digit() -> None:
    line = _line("1234567.89", ConsumedQuantity="0.003")
    allocations = allocate(
        [_metered(line, q_a="0.001", q_b="0.002")], policy_version="p", calculation_run_id="r"
    )
    assert _amounts(allocations, CostBasis.BILLED_COST) == {
        "q_a": "411522.63",
        "q_b": "823045.26",
    }


def test_an_ineligible_line_stays_unallocated_in_every_cost_basis() -> None:
    line = _line(BilledCost="4.00", EffectiveCost="3.50", ListCost="4.20", ContractedCost="3.90")
    allocations = allocate(
        [PooledLine("line#1", line, Ineligible("ineligible"))],
        policy_version="p",
        calculation_run_id="r",
    )
    assert {basis: _amounts(allocations, basis) for basis in CostBasis} == {
        CostBasis.BILLED_COST: {"unallocated": "4.00"},
        CostBasis.EFFECTIVE_COST: {"unallocated": "3.50"},
        CostBasis.LIST_COST: {"unallocated": "4.20"},
        CostBasis.CONTRACTED_COST: {"unallocated": "3.90"},
    }
    assert {(a.method, a.confidence, a.coverage) for a in allocations} == {
        ("ineligible", None, None)
    }
