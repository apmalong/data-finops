"""The golden oracle is independent of the engine; these tests check its own consistency.

At M4 the engine's output must be compared with these scenarios (golden-fixtures.md).
"""

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator

from data_finops.billing.focus import load_export
from data_finops.contracts.allocation import Allocation

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = json.loads((ROOT / "fixtures" / "golden" / "scenarios.json").read_text())
ALLOCATION_SCHEMA = json.loads((ROOT / "schemas" / "allocation.schema.json").read_text())


def test_allocations_validate_against_generated_schema_and_model():
    validator = Draft202012Validator(
        ALLOCATION_SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    for scenario in SCENARIOS:
        for allocation in scenario["allocations"]:
            validator.validate(allocation)
            Allocation.model_validate(allocation)


def test_each_billing_line_is_partitioned_exactly():
    for scenario in SCENARIOS:
        lines = {line["ref"]: line for line in scenario["billing_lines"]}
        allocated: dict[str, Decimal] = defaultdict(Decimal)
        for a in scenario["allocations"]:
            assert a["currency"] == lines[a["billing_line_ref"]]["currency"]
            allocated[a["billing_line_ref"]] += Decimal(a["amount"])
        assert set(allocated) == set(lines), scenario["id"]
        for ref, line in lines.items():
            assert allocated[ref] == Decimal(line["amount"]), (scenario["id"], ref)


def test_existing_fixture_scenario_matches_the_focus_export():
    export = load_export(ROOT / "fixtures" / "focus" / "export-2026-10.json")
    scenario = next(s for s in SCENARIOS if s["id"] == "existing-fixtures-complete-coverage")
    for line in scenario["billing_lines"]:
        index = int(line["ref"].rsplit("#", 1)[1])
        assert export.line_ref(index) == line["ref"]
        assert export.lines[index].BilledCost == Decimal(line["amount"])


def test_existing_fixture_meters_only_the_covered_athena_line():
    scenario = next(s for s in SCENARIOS if s["id"] == "existing-fixtures-complete-coverage")
    athena, storage = scenario["allocations"]
    assert athena["target_id"] == "q-001"
    assert athena["confidence"] == "metered_estimate"
    assert Decimal(athena["coverage"]) == Decimal("1")
    assert storage["category"] == "unallocated"


def test_targets_are_query_executions_or_synthetic():
    for scenario in SCENARIOS:
        for a in scenario["allocations"]:
            if a["target_type"] != "query_execution":
                assert a["target_id"] == a["target_type"]
