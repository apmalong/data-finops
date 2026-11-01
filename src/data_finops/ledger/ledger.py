"""SQLite-backed Ledger (ADR 0018). Storage is private to this module.

Snapshots and lines are stored as their contract JSON, so amounts keep their exact
value and scale and every FOCUS column, including `x_` extensions, round-trips.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
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
    billing_period_end TEXT NOT NULL,
    source TEXT NOT NULL,
    content_digest TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_line (
    snapshot_id TEXT NOT NULL REFERENCES export_snapshot (snapshot_id),
    position INTEGER NOT NULL,
    line_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, position)
);
"""


class SnapshotConflictError(Exception):
    """A snapshot ID is already stored with different billing content."""


class ImportOutcome(StrEnum):
    NEW = "new"
    REPLAY = "replay"


@dataclass(frozen=True)
class ImportResult:
    outcome: ImportOutcome
    snapshot_id: str
    """The stored snapshot: the one just imported, or the one this import replayed."""


class Ledger:
    """The immutable, versioned record of Export Snapshots, opened from a database file."""

    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def import_snapshot(self, export: FocusExport) -> ImportResult:
        snapshot = export.snapshot
        digest = _content_digest(export)
        with self._connection:
            existing = self._connection.execute(
                "SELECT snapshot_id FROM export_snapshot WHERE content_digest = ?", (digest,)
            ).fetchone()
            if existing is not None:
                return ImportResult(outcome=ImportOutcome.REPLAY, snapshot_id=existing[0])
            reused_id = self._connection.execute(
                "SELECT 1 FROM export_snapshot WHERE snapshot_id = ?", (snapshot.snapshot_id,)
            ).fetchone()
            if reused_id is not None:
                raise SnapshotConflictError(
                    f"snapshot {snapshot.snapshot_id} is already stored with different content"
                )
            self._connection.execute(
                "INSERT INTO export_snapshot (snapshot_id, billing_account_id,"
                " billing_period_start, billing_period_end, source, content_digest,"
                " snapshot_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot.snapshot_id,
                    *_scope_key(snapshot.scope),
                    digest,
                    snapshot.model_dump_json(),
                ),
            )
            self._connection.executemany(
                "INSERT INTO billing_line (snapshot_id, position, line_json) VALUES (?, ?, ?)",
                [
                    (snapshot.snapshot_id, position, line.model_dump_json())
                    for position, line in enumerate(export.lines)
                ],
            )
        return ImportResult(outcome=ImportOutcome.NEW, snapshot_id=snapshot.snapshot_id)

    def current_snapshot(self, scope: SnapshotScope) -> FocusExport | None:
        row = self._connection.execute(
            "SELECT snapshot_id, snapshot_json FROM export_snapshot"
            " WHERE billing_account_id = ? AND billing_period_start = ?"
            " AND billing_period_end = ? AND source = ?"
            " ORDER BY seq DESC LIMIT 1",
            _scope_key(scope),
        ).fetchone()
        if row is None:
            return None
        snapshot_id, snapshot_json = row
        lines = self._connection.execute(
            "SELECT line_json FROM billing_line WHERE snapshot_id = ? ORDER BY position",
            (snapshot_id,),
        ).fetchall()
        return FocusExport(
            snapshot=ExportSnapshot.model_validate_json(snapshot_json),
            lines=[FocusBillingLine.model_validate_json(line_json) for (line_json,) in lines],
        )


def _scope_key(scope: SnapshotScope) -> tuple[str, str, str, str]:
    """Scope as SQL parameters, in the column order used by every query above."""
    s = scope.model_dump(mode="json")
    return (
        s["billing_account_id"],
        s["billing_period_start"],
        s["billing_period_end"],
        s["source"],
    )


def _content_digest(export: FocusExport) -> str:
    """Identity of the billing content: scope, FOCUS version and every line, in order.

    Publication metadata (snapshot ID, publish time, files) is excluded, so the same
    export delivered twice is recognised as a replay (ADR 0019).
    """
    content = {
        "scope": export.snapshot.scope.model_dump(mode="json"),
        "focus_version": export.snapshot.focus_version,
        "lines": [line.model_dump(mode="json") for line in export.lines],
    }
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
