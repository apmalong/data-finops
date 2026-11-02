"""SQLite-backed workload store (ADR 0018 amendment). Storage is private to this module.

Shares the installation's database file with the Ledger but owns its own tables and imports
nothing from billing. OpenLineage runs are stored as reported, one row per distinct event
observation (ADR 0022); Workload Run status and times are derived from those observations
when read, so they never depend on arrival order. Parent and root runs come only from the
ParentRunFacet.
"""

import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from data_finops.contracts.workload import WorkloadRun, WorkloadRunFlag, WorkloadRunStatus
from data_finops.lineage.validation import Rejection, RejectionKind, screen_events

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workload_lineage_event (
    run_id TEXT NOT NULL,
    event_type_key TEXT NOT NULL,
    event_type TEXT,
    event_time_us INTEGER NOT NULL,
    digest TEXT NOT NULL,
    job_namespace TEXT NOT NULL,
    job_name TEXT NOT NULL,
    parent_run_id TEXT,
    parent_job_namespace TEXT,
    parent_job_name TEXT,
    root_run_id TEXT,
    root_job_namespace TEXT,
    root_job_name TEXT,
    event_json TEXT NOT NULL,
    PRIMARY KEY (run_id, event_type_key, event_time_us, digest)
);
CREATE INDEX IF NOT EXISTS workload_lineage_event_parent
    ON workload_lineage_event (parent_run_id);
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
    """Invalid events in file order, by position and reason; never stored, never echoed."""
    unsupported: tuple[Rejection, ...] = ()
    """Valid JobEvents and DatasetEvents, by position; not stored in M2."""
    changed_copies: int = 0
    """Events stored beside an event of the same run, type and time but other content."""

    def counts(self) -> Counter[RejectionKind]:
        """Rejected and unsupported events counted by kind."""
        return Counter(r.kind for r in (*self.rejected, *self.unsupported))


@dataclass(frozen=True)
class _EventKey:
    """Spec 03 step C: run, event type (JSON-encoded, so absent differs from "") and time."""

    run_id: str
    event_type_key: str
    event_time_us: int


@dataclass(frozen=True)
class _RunRef:
    """A run and its job as a ParentRunFacet names them (direct parent or root)."""

    run_id: str
    job_namespace: str | None
    job_name: str | None


@dataclass(frozen=True)
class _Observation:
    event_type_key: str
    event_type: str | None
    event_time_us: int
    job_namespace: str
    job_name: str
    parent: _RunRef | None
    root: _RunRef | None

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> "_Observation":
        key, event_type, time, namespace, name, *refs = row
        parent, root = (_RunRef(*refs[i : i + 3]) if refs[i] else None for i in (0, 3))
        return cls(key, event_type, time, namespace, name, parent, root)


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
        so the same instant written with another offset is the same event. The `sql` facet's
        query and `errorMessage.stackTrace` are removed before the digest (spec 04), so events
        differing only in that text are duplicates. Each event is screened first; invalid ones
        are reported and the rest of the file is still stored.
        """
        screening = screen_events(path)
        accepted = duplicates = changed_copies = 0
        with self._write_transaction():
            for event in screening.events:
                document = _without_sensitive_text(event.model_dump(mode="json")) | {
                    "eventTime": event.eventTime.astimezone(UTC).isoformat().replace("+00:00", "Z")
                }
                stored_json = _canonical(document)
                digest = hashlib.sha256(stored_json.encode("utf-8")).hexdigest()
                key = _EventKey(
                    event.run.runId, json.dumps(event.eventType), _microseconds(event.eventTime)
                )
                stored = self._connection.execute(
                    "INSERT OR IGNORE INTO workload_lineage_event (run_id, event_type_key,"
                    " event_type, event_time_us, digest, job_namespace, job_name, parent_run_id,"
                    " parent_job_namespace, parent_job_name, root_run_id, root_job_namespace,"
                    " root_job_name, event_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        key.run_id,
                        key.event_type_key,
                        event.eventType,
                        key.event_time_us,
                        digest,
                        event.job.namespace,
                        event.job.name,
                        *_parent_and_root(event.run.facets.get("parent")),
                        stored_json,
                    ),
                ).rowcount
                accepted += stored
                duplicates += 1 - stored
                if stored and self._other_content_stored(key, digest):
                    changed_copies += 1
        return IngestResult(
            accepted=accepted,
            duplicates=duplicates,
            rejected=tuple(screening.rejected),
            unsupported=tuple(screening.unsupported),
            changed_copies=changed_copies,
        )

    def workload_run(self, run_id: str) -> WorkloadRun | None:
        """The Workload Run as its stored observations describe it, or None if unknown.

        Status comes from the terminal events (`conflicting` when they disagree); start is
        the earliest START, end the latest terminal event. If events name different jobs for
        one run, the earliest event's job is used, ties broken by job, so the answer is stable.
        The direct parent comes from the earliest event carrying a ParentRunFacet, the root
        from the earliest event naming one.
        """
        observations = [
            _Observation.from_row(row)
            for row in self._connection.execute(
                "SELECT event_type_key, event_type, event_time_us, job_namespace, job_name,"
                " parent_run_id,"
                " parent_job_namespace, parent_job_name, root_run_id, root_job_namespace,"
                " root_job_name"
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
        parent = next((o.parent for o in observations if o.parent), None)
        root = next((o.root for o in observations if o.root), None)
        return WorkloadRun(
            system=_SYSTEM,
            job_namespace=first.job_namespace,
            job_name=first.job_name,
            run_id=run_id,
            parent_run_id=parent.run_id if parent else None,
            parent_job_namespace=parent.job_namespace if parent else None,
            parent_job_name=parent.job_name if parent else None,
            root_run_id=root.run_id if root else None,
            root_job_namespace=root.job_namespace if root else None,
            root_job_name=root.job_name if root else None,
            status=status,
            flags=_flags(observations),
            started_at=_moment(min(starts)) if starts else None,
            ended_at=_moment(max(o.event_time_us for o in terminals)) if terminals else None,
        )

    def children(self, run_id: str) -> list[WorkloadRun]:
        """Runs whose direct parent, as `workload_run` reports it, is `run_id`; by run ID.

        One level only. Producers can report cycles across runs (A names B, B names A), so
        anything walking the chain must guard against revisiting a run.
        """
        candidates = self._connection.execute(
            "SELECT DISTINCT run_id FROM workload_lineage_event WHERE parent_run_id = ?"
            " ORDER BY run_id",
            (run_id,),
        ).fetchall()
        runs = (self.workload_run(candidate) for (candidate,) in candidates)
        return [run for run in runs if run is not None and run.parent_run_id == run_id]

    def stored_events(self, run_id: str) -> list[str]:
        """The run's events exactly as stored (canonical JSON), oldest first."""
        rows = self._connection.execute(
            "SELECT event_json FROM workload_lineage_event WHERE run_id = ?"
            " ORDER BY event_time_us, digest",
            (run_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def _other_content_stored(self, key: _EventKey, digest: str) -> bool:
        found = self._connection.execute(
            "SELECT 1 FROM workload_lineage_event WHERE run_id = ? AND event_type_key = ?"
            " AND event_time_us = ? AND digest != ? LIMIT 1",
            (key.run_id, key.event_type_key, key.event_time_us, digest),
        ).fetchone()
        return found is not None

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


def _flags(observations: list[_Observation]) -> tuple[WorkloadRunFlag, ...]:
    flags = []
    # Exact copies are never stored twice, so a repeated key means different content.
    keys = Counter((o.event_type_key, o.event_time_us) for o in observations)
    if any(count > 1 for count in keys.values()):
        flags.append(WorkloadRunFlag.CONFLICTING_OBSERVATIONS)
    if len({(o.job_namespace, o.job_name) for o in observations}) > 1:
        flags.append(WorkloadRunFlag.CONFLICTING_JOBS)
    if len({o.parent for o in observations if o.parent}) > 1:
        flags.append(WorkloadRunFlag.CONFLICTING_PARENTS)
    return tuple(flags)


def _canonical(document: dict[str, Any]) -> str:
    # Sorted-key compact JSON, the Ledger's digest form (build-vs-reuse register).
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def _microseconds(moment: datetime) -> int:
    """UTC instant as an integer, so ordering never depends on timestamp formatting."""
    return (moment - _EPOCH) // timedelta(microseconds=1)


def _moment(microseconds: int) -> datetime:
    return _EPOCH + timedelta(microseconds=microseconds)


def _parent_and_root(facet: Any) -> tuple[str | None, ...]:
    """Parent and root run and job from a ParentRunFacet (validated at screening), or Nones."""
    if not facet:
        return (None,) * 6
    root = facet.get("root")
    parent = (facet["run"]["runId"], facet["job"]["namespace"], facet["job"]["name"])
    if not root:
        return (*parent, None, None, None)
    return (*parent, root["run"]["runId"], root["job"]["namespace"], root["job"]["name"])


def _without_sensitive_text(document: dict[str, Any]) -> dict[str, Any]:
    """Drop SQL text and stack traces before storage (spec 04); keep every other field.

    Removes `query` from any facet named `sql` (job, run, input or output facets) and
    `stackTrace` from `errorMessage` and from each `extractionError` error. SQL quoted inside
    an error message is not detected.
    """
    for facets in _facet_maps(document):
        if isinstance(facets.get("sql"), dict):
            facets["sql"].pop("query", None)
        if isinstance(facets.get("errorMessage"), dict):
            facets["errorMessage"].pop("stackTrace", None)
        extraction = facets.get("extractionError")
        if isinstance(extraction, dict) and isinstance(extraction.get("errors"), list):
            for error in extraction["errors"]:
                if isinstance(error, dict):
                    error.pop("stackTrace", None)
    return document


def _facet_maps(document: dict[str, Any]) -> list[dict[str, Any]]:
    maps = [document.get("job", {}).get("facets"), document.get("run", {}).get("facets")]
    for dataset in [*document.get("inputs", []), *document.get("outputs", [])]:
        if isinstance(dataset, dict):
            maps += [dataset.get(k) for k in ("facets", "inputFacets", "outputFacets")]
    return [m for m in maps if isinstance(m, dict)]
