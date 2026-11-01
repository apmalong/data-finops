from decimal import Decimal
from data_finops.contracts import Allocation, AllocationKind

def test_allocation_uses_exact_decimal():
    a = Allocation("bill-001", "USD", Decimal("0.10") + Decimal("0.20"), AllocationKind.UNALLOCATED, "unknown")
    assert a.amount == Decimal("0.30")
