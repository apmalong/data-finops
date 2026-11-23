"""The scale measurement's Database (#336; ADR 0033, Q177): a copy of a Database with about
`factor` times its stored Billing Lines."""

import sqlite3
from pathlib import Path

from fixture_database import EXPORT, fill, restate

from data_finops.ledger import Ledger
from scripts.measure_scale import scale


def _count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


def test_a_scaled_database_holds_factor_times_the_stored_lines(tmp_path: Path) -> None:
    seed = tmp_path / "seed.sqlite"
    fill(seed)
    restate(seed)  # two Export Snapshots of one scope: one superseded, one current
    lines, snapshots = _count(seed, "billing_line"), _count(seed, "export_snapshot")
    with Ledger(seed) as ledger:
        current = ledger.current_snapshot(EXPORT.snapshot.scope)
    assert current is not None and snapshots == 2

    scaled = tmp_path / "scaled.sqlite"
    scale(seed, 3, scaled)

    assert _count(scaled, "billing_line") == 3 * lines
    assert _count(scaled, "export_snapshot") == 2 * snapshots
    with Ledger(scaled) as ledger:
        grown = ledger.current_snapshot(EXPORT.snapshot.scope)
    # The copy of the current Export Snapshot is the new current one, with its lines twice.
    assert grown is not None and grown.lines == current.lines * 2
    assert _count(seed, "billing_line") == lines  # the seed is not changed
