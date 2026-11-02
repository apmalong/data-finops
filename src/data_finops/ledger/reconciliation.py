"""Reconciliation of stored Billing Lines against the source they were imported from.

The Reconciliation Delta is source minus Ledger: the authoritative amount minus what
the Ledger holds. Amounts compare exactly, and an amount is only ever added to or
compared with amounts in the same currency.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from itertools import zip_longest

from data_finops.contracts.billing import CostBasis, FocusBillingLine, FocusExport


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class LineReconciliation:
    """One Billing Line position; a side is None when that side has no line there."""

    ref: str
    source: Money | None
    ledger: Money | None

    @property
    def balanced(self) -> bool:
        return self.source == self.ledger

    @property
    def delta(self) -> Decimal | None:
        """Source minus Ledger, or None when the two sides are in different currencies."""
        if self.source and self.ledger and self.source.currency != self.ledger.currency:
            return None
        return (self.source.amount if self.source else Decimal(0)) - (
            self.ledger.amount if self.ledger else Decimal(0)
        )


@dataclass(frozen=True)
class CurrencyTotal:
    source_amount: Decimal
    ledger_amount: Decimal

    @property
    def delta(self) -> Decimal:
        return self.source_amount - self.ledger_amount


@dataclass(frozen=True)
class ReconciliationReport:
    snapshot_id: str
    cost_basis: CostBasis
    lines: tuple[LineReconciliation, ...]
    totals: Mapping[str, CurrencyTotal]
    """One total per billing currency; there is deliberately no cross-currency total."""

    @property
    def balanced(self) -> bool:
        return all(line.balanced for line in self.lines)


def reconcile(
    source: FocusExport, stored: FocusExport, cost_basis: CostBasis
) -> ReconciliationReport:
    def money(line: FocusBillingLine | None) -> Money | None:
        return None if line is None else Money(line.cost(cost_basis), line.BillingCurrency)

    # Lines pair by position; a line present on only one side shows as unbalanced.
    lines = tuple(
        LineReconciliation(
            ref=f"{source.snapshot.snapshot_id}#{position}",
            source=money(source_line),
            ledger=money(stored_line),
        )
        for position, (source_line, stored_line) in enumerate(
            zip_longest(source.lines, stored.lines)
        )
    )
    source_sums: dict[str, Decimal] = defaultdict(Decimal)
    ledger_sums: dict[str, Decimal] = defaultdict(Decimal)
    for line in lines:
        if line.source:
            source_sums[line.source.currency] += line.source.amount
        if line.ledger:
            ledger_sums[line.ledger.currency] += line.ledger.amount
    totals = {
        currency: CurrencyTotal(source_sums[currency], ledger_sums[currency])
        for currency in sorted(source_sums.keys() | ledger_sums.keys())
    }
    return ReconciliationReport(
        snapshot_id=source.snapshot.snapshot_id, cost_basis=cost_basis, lines=lines, totals=totals
    )
