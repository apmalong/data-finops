"""A Ledger fault for the reconciliation tests (#212): a Ledger that drops one Billing Line
when it stores an Export Snapshot, so what it holds differs from the source it read."""

from collections.abc import Sequence

import pytest

from data_finops.contracts.billing import FocusBillingLine
from data_finops.ledger import Ledger


def drop_one_billing_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """From now on in the test, every Ledger stores each snapshot without its last line."""
    store = Ledger._store_lines

    def dropping(self: Ledger, snapshot_id: str, lines: Sequence[FocusBillingLine]) -> None:
        store(self, snapshot_id, lines[:-1])

    monkeypatch.setattr(Ledger, "_store_lines", dropping)
