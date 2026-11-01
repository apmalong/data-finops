"""SQLite-backed Ledger (ADR 0018). Storage is private to this module.

Snapshots and lines are stored as their contract JSON, so amounts keep their exact
value and scale and every FOCUS column, including `x_` extensions, round-trips.
"""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
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
    content_digest TEXT NOT NULL,
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
class _StoredSnapshot:
    snapshot_id: str
    content_digest: str
    snapshot_json: str


@dataclass(frozen=True)
class ImportResult:
    outcome: ImportOutcome
    snapshot_id: str
    """The stored snapshot: the one just imported, or the one this import replayed."""


class Ledger:
    """The immutable, versioned record of Export Snapshots, opened from a database file."""

    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path, isolation_level=None)
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
        """Store an Export Snapshot, unless it replays the current one for its scope.

        Replay means the same billing content as the scope's *current* snapshot, so
        content republished after a restatement is new (ADR 0019). A snapshot ID
        already stored with different content is rejected: stored snapshots are
        immutable.
        """
        snapshot = export.snapshot
        digest = _content_digest(export)
        with self._write_transaction():
            current = self._current(snapshot.scope)
            if current is not None and current.content_digest == digest:
                return ImportResult(outcome=ImportOutcome.REPLAY, snapshot_id=current.snapshot_id)
            stored = self._connection.execute(
                "SELECT content_digest FROM export_snapshot WHERE snapshot_id = ?",
                (snapshot.snapshot_id,),
            ).fetchone()
            if stored is not None:
                if stored[0] != digest:
                    raise SnapshotConflictError(
                        f"snapshot {snapshot.snapshot_id} is already stored"
                        " with different billing content"
                    )
                # The same publication is stored but no longer current; whether
                # re-importing it should restore it is decided in #9.
                return ImportResult(outcome=ImportOutcome.REPLAY, snapshot_id=snapshot.snapshot_id)
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
        current = self._current(scope)
        if current is None:
            return None
        lines = self._connection.execute(
            "SELECT line_json FROM billing_line WHERE snapshot_id = ? ORDER BY position",
            (current.snapshot_id,),
        ).fetchall()
        return FocusExport(
            snapshot=ExportSnapshot.model_validate_json(current.snapshot_json),
            lines=[FocusBillingLine.model_validate_json(line_json) for (line_json,) in lines],
        )

    def _current(self, scope: SnapshotScope) -> _StoredSnapshot | None:
        row = self._connection.execute(
            "SELECT snapshot_id, content_digest, snapshot_json FROM export_snapshot"
            " WHERE billing_account_id = ? AND billing_period_start = ?"
            " AND billing_period_end = ? AND source = ?"
            " ORDER BY seq DESC LIMIT 1",
            _scope_key(scope),
        ).fetchone()
        return None if row is None else _StoredSnapshot(*row)

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        # IMMEDIATE takes the write lock before the replay check, so two processes
        # importing at once cannot both decide a snapshot is new.
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")


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
    # Sorted-key compact JSON over contract-serialized values (strings and nulls), so
    # the digest is deterministic; internal to the Ledger (build-vs-reuse register).
    canonical = json.dumps(export.billing_content(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
