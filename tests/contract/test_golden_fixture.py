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


def test_existing_fixture_does_not_invent_query_price():
    golden = json.loads((ROOT / "fixtures" / "golden" / "scenarios.json").read_text())
    baseline = next(s for s in golden if s["id"] == "existing-fixtures-no-price-evidence")
    assert all(a["kind"] == "unallocated" for a in baseline["allocations"])
