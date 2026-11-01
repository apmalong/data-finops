"""SQLite-backed workload store (ADR 0018 amendment). Storage is private to this module.

Shares the installation's database file with the Ledger but owns its own tables and imports
nothing from billing. OpenLineage runs are stored as reported, one row per distinct event
observation (ADR 0022); Workload Run status and times are derived from those observations
when read, so they never depend on arrival order. Parent and root runs come with #43.
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
from typing import Any, Self

from data_finops.contracts.workload import WorkloadRun, WorkloadRunStatus
from data_finops.lineage.validation import Rejection, screen_events

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workload_lineage_event (
    run_id TEXT NOT NULL,
    event_type_key TEXT NOT NULL,
    event_type TEXT,
    event_time_us INTEGER NOT NULL,
    digest TEXT NOT NULL,
    job_namespace TEXT NOT NULL,
    job_name TEXT NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (run_id, event_type_key, event_time_us, digest)
);
"""

_START = "START"
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
    rejected: tuple[Rejection, ...] = ()
    """Invalid events, by position and reason; never stored, never echoed."""
    unsupported: int = 0
    """JobEvents and DatasetEvents: valid OpenLineage, not stored in M2."""


@dataclass(frozen=True)
class _Observation:
    event_type: str | None
    event_time_us: int
    job_namespace: str
    job_name: str


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
        """Store an OpenLineage event file (JSON array or JSON Lines); re-ingest is a no-op.

        Events are keyed by run, event type and time plus a digest of the event as stored
        (spec 03 step C). The stored form is sorted-key compact JSON with `eventTime` in UTC,
        so the same instant written with another offset is the same event. Each event is
        screened first; invalid ones are reported and the rest of the file is still stored.
        """
        screening = screen_events(path)
        accepted = duplicates = 0
        with self._write_transaction():
            for _, event in screening.events:
                document = event.model_dump(mode="json") | {
                    "eventTime": event.eventTime.astimezone(UTC).isoformat().replace("+00:00", "Z")
                }
                stored_json = _canonical(document)
                stored = self._connection.execute(
                    "INSERT OR IGNORE INTO workload_lineage_event VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.run.runId,
                        json.dumps(event.eventType),  # keeps an absent type apart from ""
                        event.eventType,
                        _microseconds(event.eventTime),
                        hashlib.sha256(stored_json.encode("utf-8")).hexdigest(),
                        event.job.namespace,
                        event.job.name,
                        stored_json,
                    ),
                ).rowcount
                accepted += stored
                duplicates += 1 - stored
        return IngestResult(
            accepted=accepted,
            duplicates=duplicates,
            rejected=tuple(screening.rejected),
            unsupported=screening.unsupported,
        )

    def workload_run(self, run_id: str) -> WorkloadRun | None:
        """The Workload Run as its stored observations describe it, or None if unknown.

        Status comes from the terminal events (`conflicting` when they disagree); start is
        the earliest START, end the latest terminal event. If events name different jobs for
        one run, the earliest event's job is used, ties broken by job, so the answer is stable.
        """
        observations = [
            _Observation(*row)
            for row in self._connection.execute(
                "SELECT event_type, event_time_us, job_namespace, job_name"
                " FROM workload_lineage_event WHERE run_id = ?"
                " ORDER BY event_time_us, job_namespace, job_name",
                (run_id,),
            )
        ]
        if not observations:
            return None
        starts = [o.event_time_us for o in observations if o.event_type == _START]
        terminals = [o for o in observations if o.event_type in _TERMINAL]
        statuses = {_TERMINAL[str(o.event_type)] for o in terminals}
        if not statuses:
            status = WorkloadRunStatus.RUNNING
        elif len(statuses) == 1:
            status = statuses.pop()
        else:
            status = WorkloadRunStatus.CONFLICTING
        first = observations[0]
        return WorkloadRun(
            system=_SYSTEM,
            job_namespace=first.job_namespace,
            job_name=first.job_name,
            run_id=run_id,
            status=status,
            started_at=_moment(min(starts)) if starts else None,
            ended_at=_moment(max(o.event_time_us for o in terminals)) if terminals else None,
        )

    @contextmanager
    def _write_transaction(self) -> Iterator[None]:
        # IMMEDIATE takes the write lock before the duplicate checks, so two processes
        # ingesting the same file at once cannot both store an event.
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")


def _canonical(document: dict[str, Any]) -> str:
    # Sorted-key compact JSON, the Ledger's digest form (build-vs-reuse register).
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def _microseconds(moment: datetime) -> int:
    """UTC instant as an integer, so ordering never depends on timestamp formatting."""
    return (moment - _EPOCH) // timedelta(microseconds=1)


def _moment(microseconds: int) -> datetime:
    return _EPOCH + timedelta(microseconds=microseconds)
