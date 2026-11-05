"""Reconcile an imported AWS Data Exports Delivery against the Ledger, per Billing Line (#12).

Usage, after `data-finops ingest focus <export>-Manifest.json --db <db>`:
    uv run python scripts/reconcile_delivery.py <export>-Manifest.json --db <db> [--data-dir DIR]

For BilledCost and EffectiveCost, prints the Delivery's Billing Period, its line count,
whether every line balances, the source and Ledger totals per currency, and how many lines
have a non-zero delta. The account ID inside the snapshot ID is never printed.
"""

import argparse
import sys
from pathlib import Path

from data_finops.billing.aws_data_exports import load_delivery
from data_finops.contracts.billing import CostBasis
from data_finops.ledger import Ledger


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args(argv)
    export = load_delivery(args.manifest, args.data_dir)
    period = f"{export.snapshot.billing_period_start:%Y-%m}"
    balanced = True
    with Ledger(args.db) as ledger:
        for basis in (CostBasis.BILLED_COST, CostBasis.EFFECTIVE_COST):
            report = ledger.reconcile(export, basis)
            unbalanced = sum(1 for line in report.lines if not line.balanced)
            totals = "  ".join(
                f"{currency} {total.source_amount} = {total.ledger_amount}"
                for currency, total in sorted(report.totals.items())
            )
            print(
                f"{period}  {basis.value:<13}  {len(report.lines)} lines  "
                f"{'balanced' if report.balanced else 'UNBALANCED'}  {totals}  "
                f"lines with a non-zero delta: {unbalanced}"
            )
            balanced = balanced and report.balanced
    return 0 if balanced else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
