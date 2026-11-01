"""SQLite-backed workload store (ADR 0018 amendment). Storage is private to this module.

Shares the installation's database file with the Ledger but owns its own tables and imports
nothing from billing. OpenLineage runs are stored as reported, one row per distinct event
observation (ADR 0022); Workload Run status is derived from those observations when read,
so it never depends on arrival order.
"""

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Self

from data_finops.contracts.workload import WorkloadRun, WorkloadRunStatus
from data_finops.lineage.openlineage import RunEvent, load_events

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workload_lineage_event (
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_time_us INTEGER NOT NULL,
    digest TEXT NOT NULL,
    job_namespace TEXT NOT NULL,
    job_name TEXT NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (run_id, event_type, event_time_us, digest)
);
"""

_TERMINAL = {
    "COMPLETE": WorkloadRunStatus.COMPLETED,
    "FAIL": WorkloadRunStatus.FAILED,
    "ABORT": WorkloadRunStatus.ABORTED,
}
_SYSTEM = "openlineage"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class IngestResult:
    accepted: int
    """Events stored by this ingest."""
    duplicates: int
    """Events already stored with the same key and content; nothing changed."""


class WorkloadStore:
    """Workload Runs and their evidence, opened from the installation's database file."""

    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path, isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=WAL")
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

    def ingest_events(self, path: Path) -> IngestResult:
        """Store an OpenLineage event file (JSON array or JSON Lines); re-ingest is a no-op."""
        accepted = duplicates = 0
        with self._write_transaction():
            for event in load_events(path):
                stored = self._connection.execute(
                    "INSERT OR IGNORE INTO workload_lineage_event VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.run.runId,
                        event.eventType or "",
                        _microseconds(event.eventTime),
                        _digest(event),
                        event.job.namespace,
                        event.job.name,
                        event.model_dump_json(),
                    ),
                ).rowcount
                accepted += stored
                duplicates += 1 - stored
        return IngestResult(accepted=accepted, duplicates=duplicates)

    def workload_run(self, run_id: str) -> WorkloadRun | None:
        """The Workload Run as its stored observations describe it, or None if unknown."""
        rows = self._connection.execute(
            "SELECT event_type, event_time_us, job_namespace, job_name"
            " FROM workload_lineage_event WHERE run_id = ? ORDER BY event_time_us",
            (run_id,),
        ).fetchall()
        if not rows:
            return None
        starts = [time for event_type, time, _, _ in rows if event_type == "START"]
        terminals = {
            (event_type, time) for event_type, time, _, _ in rows if event_type in _TERMINAL
        }
        statuses = {_TERMINAL[event_type] for event_type, _ in terminals}
        if not statuses:
            status = WorkloadRunStatus.RUNNING
        elif len(statuses) == 1:
            status = statuses.pop()
        else:
            status = WorkloadRunStatus.CONFLICTING
        _, _, namespace, name = rows[0]
        return WorkloadRun(
            system=_SYSTEM,
            job_namespace=namespace,
            job_name=name,
            run_id=run_id,
            status=status,
            started_at=_moment(min(starts)) if starts else None,
            ended_at=_moment(max(time for _, time in terminals)) if terminals else None,
        )

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")


def _digest(event: RunEvent) -> str:
    # Sorted-key compact JSON of the event as stored, like the Ledger's content digest.
    canonical = json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _microseconds(moment: datetime) -> int:
    """UTC instant as an integer, so ordering never depends on timestamp formatting."""
    return (moment - _EPOCH) // timedelta(microseconds=1)


def _moment(microseconds: int) -> datetime:
    return _EPOCH + timedelta(microseconds=microseconds)
