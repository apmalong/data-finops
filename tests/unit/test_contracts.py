from decimal import Decimal

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from data_finops.contracts.allocation import Allocation, AllocationCategory, Confidence
from data_finops.schemas import generate

BASE = {
    "billing_line_ref": "snap#0",
    "pool_id": "pool-1",
    "target_type": "query_execution",
    "target_id": "q-001",
    "amount": "6.00",
    "currency": "USD",
    "cost_basis": "BilledCost",
    "category": "direct",
    "method": "estimated_metered_share",
    "confidence": "metered_estimate",
    "policy_version": "v1",
    "calculation_run_id": "run-1",
}


def test_amount_is_exact_decimal_and_keeps_scale():
    a = Allocation.model_validate(BASE)
    assert a.amount == Decimal("6.00")
    assert a.model_dump(mode="json")["amount"] == "6.00"


@pytest.mark.parametrize("amount", [6.0, 6, "6.0.0", "abc", True])
def test_amount_rejects_non_decimal_strings(amount):
    with pytest.raises(ValidationError):
        Allocation.model_validate({**BASE, "amount": amount})


def test_currency_must_be_iso_4217_code():
    with pytest.raises(ValidationError):
        Allocation.model_validate({**BASE, "currency": "usd"})


def test_unallocated_amounts_carry_no_confidence():
    unallocated = {
        **BASE,
        "target_type": "unallocated",
        "target_id": "unallocated",
        "category": "unallocated",
        "method": "no_evidence",
    }
    with pytest.raises(ValidationError):
        Allocation.model_validate(unallocated)
    del unallocated["confidence"]
    assert Allocation.model_validate(unallocated).confidence is None


@pytest.mark.parametrize("category", ["direct", "shared"])
def test_direct_and_shared_require_confidence(category):
    data = {**BASE, "category": category}
    del data["confidence"]
    with pytest.raises(ValidationError):
        Allocation.model_validate(data)


def test_synthetic_targets_match_their_category():
    with pytest.raises(ValidationError):
        Allocation.model_validate({**BASE, "target_type": "idle", "target_id": "idle"})


INVALID = {
    "unallocated with confidence": {
        **BASE,
        "target_type": "unallocated",
        "target_id": "unallocated",
        "category": "unallocated",
    },
    "direct without confidence": {k: v for k, v in BASE.items() if k != "confidence"},
    "idle targeting a query": {**BASE, "category": "idle", "confidence": None},
    "direct targeting idle": {**BASE, "target_type": "idle", "target_id": "idle"},
}


@pytest.mark.parametrize("name", sorted(INVALID))
def test_generated_schema_rejects_what_the_model_rejects(name):
    validator = Draft202012Validator(generate("allocation"))
    assert not validator.is_valid(INVALID[name])
    with pytest.raises(ValidationError):
        Allocation.model_validate(INVALID[name])


def test_generated_schema_accepts_a_valid_allocation():
    Draft202012Validator(generate("allocation")).validate(BASE)


def test_confidence_vocabulary_excludes_unallocated():
    assert "unallocated" not in {c.value for c in Confidence}
    assert {c.value for c in AllocationCategory} == {"direct", "shared", "idle", "unallocated"}
