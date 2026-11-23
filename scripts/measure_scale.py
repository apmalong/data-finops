"""Measure a Database at a stated size (#336; ADR 0033, Q174 and Q177).

    python -m scripts.measure_scale scale SEED FACTOR OUT
    python -m scripts.measure_scale measure DB

`scale` copies the Database SEED to OUT with about FACTOR times its stored Billing Lines.
Each Export Snapshot gets a copy, published one second later, with its lines FACTOR - 1
times. So the copy of a scope's current Export Snapshot becomes the current one, and the
copies of the superseded ones stay superseded, as later deliveries would. The workload data
and the Allocation Snapshots are not copied: a Calculation Run makes the new current ones.

`measure` runs, on DB, what the collector and the publisher do after their download: one
Calculation Run for each Snapshot Scope, then the render of the Demo Snapshot and the write of
its files to a temporary folder. It logs the phase lines of `data_finops.phases` to stderr,
and prints the size of the file. Run each measurement in its own process: the peak memory is
the peak of the process. `--only collector` or `--only publisher` runs one of the two.

The Deployment's own numbers come from its logs (the wizard `measure.sh`).
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from data_finops.calculation import run_calculation
from data_finops.contracts.billing import FocusExport
from data_finops.ledger import Ledger
from data_finops.phases import Phases
from data_finops.public_demo.publisher import demo_lines, render
from data_finops.workload.store import WorkloadStore


def scale(seed: Path, factor: int, out: Path) -> None:
    """Copy SEED to OUT with about `factor` times its stored Billing Lines."""
    if factor < 1:
        raise ValueError("the factor must be 1 or more")
    shutil.copyfile(seed, out)
    if factor == 1:
        return
    with sqlite3.connect(seed) as connection:
        ids = [row[0] for row in connection.execute("SELECT snapshot_id FROM export_snapshot")]
    with Ledger(out) as ledger:
        for snapshot_id in ids:
            export = ledger.snapshot(snapshot_id)
            if export is None:  # listed a moment ago, in a copy no one else writes
                raise RuntimeError(f"Export Snapshot {snapshot_id} is gone")
            copy = export.snapshot.model_copy(
                update={
                    "snapshot_id": f"{snapshot_id}.x{factor}",
                    "published_at": export.snapshot.published_at + timedelta(seconds=1),
                    "files": [],
                }
            )
            ledger.import_snapshot(FocusExport(snapshot=copy, lines=export.lines * (factor - 1)))


def measure(database: Path, only: str | None = None) -> None:
    """Calculate and publish DB locally, with the phase lines on stderr. `only` runs one of
    the two, so that the peak memory is that function's alone."""
    if only != "publisher":
        collector = Phases("collector")
        with collector("calculate"):
            with Ledger(database) as ledger, WorkloadStore(database) as store:
                for scope in ledger.scopes():
                    run_calculation(ledger, store, scope)
    if only == "collector":
        return
    publisher = Phases("publisher")
    files = render(database, published_at=datetime.now(UTC), lines=demo_lines(), phases=publisher)
    with publisher("write"), tempfile.TemporaryDirectory(prefix="measure-") as folder:
        for file in files:
            path = Path(folder, file.key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(file.body)
    print(f"{database.name}: {database.stat().st_size:,} bytes, {len(files)} files published")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    scaling = commands.add_parser("scale", help="copy a Database at about FACTOR times its lines")
    scaling.add_argument("seed", type=Path)
    scaling.add_argument("factor", type=int)
    scaling.add_argument("out", type=Path)
    measuring = commands.add_parser("measure", help="calculate and publish, with phase lines")
    measuring.add_argument("database", type=Path)
    measuring.add_argument("--only", choices=["collector", "publisher"])
    args = parser.parse_args(argv)
    if args.command == "scale":
        scale(args.seed, args.factor, args.out)
    else:
        measure(args.database, args.only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
