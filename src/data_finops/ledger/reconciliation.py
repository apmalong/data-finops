"""Reconciliation of stored Billing Lines against the source they were imported from.

The Reconciliation Delta is source minus Ledger: the authoritative amount minus what
the Ledger holds. Amounts are compared exactly; currencies are never summed together.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from itertools import zip_longest

from data_finops.contracts.billing import CostBasis, FocusBillingLine, FocusExport


@dataclass(frozen=True)
class LineReconciliation:
    ref: str
    currency: str
    source_amount: Decimal
    ledger_amount: Decimal

    @property
    def delta(self) -> Decimal:
        return self.source_amount - self.ledger_amount


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
    lines: list[LineReconciliation]
    totals: dict[str, CurrencyTotal]
    """One total per billing currency; there is deliberately no cross-currency total."""

    @property
    def balanced(self) -> bool:
        return all(line.delta == 0 for line in self.lines)


def reconcile(
    source: FocusExport, stored: FocusExport, cost_basis: CostBasis
) -> ReconciliationReport:
    # Lines pair by position; a line present on only one side counts as zero on the
    # other, so a missing or extra line shows up as a delta rather than an error.
    lines = [
        LineReconciliation(
            ref=f"{source.snapshot.snapshot_id}#{position}",
            currency=(source_line or stored_line).BillingCurrency,
            source_amount=_amount(source_line, cost_basis),
            ledger_amount=_amount(stored_line, cost_basis),
        )
        for position, (source_line, stored_line) in enumerate(
            zip_longest(source.lines, stored.lines)
        )
    ]
    sums: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for line in lines:
        sums[line.currency][0] += line.source_amount
        sums[line.currency][1] += line.ledger_amount
    totals = {
        currency: CurrencyTotal(source_amount=s, ledger_amount=held)
        for currency, (s, held) in sums.items()
    }
    return ReconciliationReport(
        snapshot_id=source.snapshot.snapshot_id, cost_basis=cost_basis, lines=lines, totals=totals
    )


def _amount(line: FocusBillingLine | None, cost_basis: CostBasis) -> Decimal:
    if line is None:
        return Decimal(0)
    amount: Decimal = getattr(line, cost_basis)
    return amount
