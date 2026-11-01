"""SQLite-backed Ledger (ADR 0018). Storage is private to this module.

Snapshots and lines are stored as their contract JSON, so amounts keep their exact
value and scale and every FOCUS column, including `x_` extensions, round-trips.
"""

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self

from data_finops.contracts.billing import (
    ExportSnapshot,
    FocusBillingLine,
    FocusExport,
    SnapshotScope,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS export_snapshot (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL UNIQUE,
    billing_account_id TEXT NOT NULL,
    billing_period_start TEXT NOT NULL,
    source TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_line (
    snapshot_id TEXT NOT NULL REFERENCES export_snapshot (snapshot_id),
    position INTEGER NOT NULL,
    line_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, position)
);
"""


class Ledger:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    @classmethod
    def open(cls, path: Path) -> Self:
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(_SCHEMA)
        return cls(connection)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def import_snapshot(self, export: FocusExport) -> None:
        snapshot = export.snapshot
        scope = _scope_key(snapshot.scope)
        with self._db:
            self._db.execute(
                "INSERT INTO export_snapshot"
                " (snapshot_id, billing_account_id, billing_period_start, source, snapshot_json)"
                " VALUES (?, ?, ?, ?, ?)",
                (snapshot.snapshot_id, *scope, snapshot.model_dump_json()),
            )
            self._db.executemany(
                "INSERT INTO billing_line (snapshot_id, position, line_json) VALUES (?, ?, ?)",
                [
                    (snapshot.snapshot_id, position, line.model_dump_json())
                    for position, line in enumerate(export.lines)
                ],
            )

    def current_snapshot(self, scope: SnapshotScope) -> FocusExport | None:
        row = self._db.execute(
            "SELECT snapshot_id, snapshot_json FROM export_snapshot"
            " WHERE billing_account_id = ? AND billing_period_start = ? AND source = ?"
            " ORDER BY seq DESC LIMIT 1",
            _scope_key(scope),
        ).fetchone()
        if row is None:
            return None
        snapshot_id, snapshot_json = row
        lines = self._db.execute(
            "SELECT line_json FROM billing_line WHERE snapshot_id = ? ORDER BY position",
            (snapshot_id,),
        ).fetchall()
        return FocusExport(
            snapshot=ExportSnapshot.model_validate_json(snapshot_json),
            lines=[FocusBillingLine.model_validate_json(line_json) for (line_json,) in lines],
        )


def _scope_key(scope: SnapshotScope) -> tuple[str, str, str]:
    dumped = scope.model_dump(mode="json")
    return (dumped["billing_account_id"], dumped["billing_period_start"], dumped["source"])
