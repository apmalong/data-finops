import json
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_golden_scenarios_reconcile():
    scenarios = json.loads((ROOT / "fixtures" / "golden" / "scenarios.json").read_text())
    for scenario in scenarios:
        assert sum(
            (Decimal(a["amount"]) for a in scenario["allocations"]), Decimal("0")
        ) == Decimal(scenario["billed_cost"])
        assert all(a["currency"] == scenario["currency"] for a in scenario["allocations"])


def test_existing_fixture_meters_only_the_covered_athena_line():
    golden = json.loads((ROOT / "fixtures" / "golden" / "scenarios.json").read_text())
    baseline = next(s for s in golden if s["id"] == "existing-fixtures-complete-coverage")
    by_line = {a["source_record_id"]: a for a in baseline["allocations"]}
    assert by_line["bill-001"]["kind"] == "direct"
    assert by_line["bill-001"]["target"] == "q-001"
    assert by_line["bill-001"]["confidence"] == "metered_estimate"
    assert Decimal(by_line["bill-001"]["coverage"]) == Decimal("1")
    assert by_line["bill-002"]["kind"] == "unallocated"


def test_unallocated_amounts_carry_no_confidence():
    golden = json.loads((ROOT / "fixtures" / "golden" / "scenarios.json").read_text())
    for scenario in golden:
        for a in scenario["allocations"]:
            if a["kind"] == "unallocated":
                assert "confidence" not in a
