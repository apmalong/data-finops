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
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from data_finops.contracts.workload import (
    DataAsset,
    DbtNode,
    DbtObservationFlag,
    LineageDirection,
    LineageEdge,
    LinkResolution,
    ModelExecution,
    QueryExecution,
    QueryExecutionFlag,
    WorkloadRun,
    WorkloadRunFlag,
    WorkloadRunStatus,
)
from data_finops.dbt.artifacts import (
    Manifest,
    RunResults,
    ScreenedArtifact,
    Timing,
    screen_dbt_artifact,
)
from data_finops.identity.links import ModelExecutionKey, resolve_model_run
from data_finops.lineage.openlineage import RunEvent
from data_finops.lineage.validation import Rejection, RejectionKind, screen_events
from data_finops.providers.athena import EntryRejection, screen_query_history

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
CREATE TABLE IF NOT EXISTS workload_lineage_edge (
    run_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    event_time_us INTEGER NOT NULL,
    direction TEXT NOT NULL,
    namespace TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (run_id, digest, direction, namespace, name)
);
CREATE TABLE IF NOT EXISTS workload_dbt_artifact (
    invocation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY (invocation_id, kind, digest)
);
CREATE TABLE IF NOT EXISTS workload_model_execution (
    invocation_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    started_us INTEGER,
    completed_us INTEGER,
    PRIMARY KEY (invocation_id, node_id, artifact_digest)
);
CREATE TABLE IF NOT EXISTS workload_manifest_node (
    invocation_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    name TEXT NOT NULL,
    package_name TEXT,
    relation_name TEXT,
    materialized TEXT,
    depends_on TEXT NOT NULL,
    PRIMARY KEY (invocation_id, node_id, artifact_digest)
);
CREATE TABLE IF NOT EXISTS workload_query_execution (
    account_id TEXT NOT NULL,
    region TEXT NOT NULL,
    query_execution_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    workgroup TEXT,
    statement_type TEXT,
    state TEXT NOT NULL,
    submitted_us INTEGER NOT NULL,
    completed_us INTEGER,
    scanned_bytes INTEGER,
    entry_json TEXT NOT NULL,
    PRIMARY KEY (account_id, region, query_execution_id, digest)
);
"""

_START = "START"
_TERMINAL = {
    "COMPLETE": WorkloadRunStatus.COMPLETED,
    "FAIL": WorkloadRunStatus.FAILED,
    "ABORT": WorkloadRunStatus.ABORTED,
}
_SYSTEM = "openlineage"
_ATHENA = "aws_athena"
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


class ArtifactOutcome(StrEnum):
    STORED = "stored"
    DUPLICATE = "duplicate"
    """The same file for this invocation is already stored; nothing changed."""
    CHANGED_COPY = "changed_copy"
    """Another file for this invocation is already stored; this one is kept beside it."""
    REJECTED = "rejected"


@dataclass(frozen=True)
class ArtifactIngestResult:
    outcome: ArtifactOutcome
    invocation_id: str | None = None
    reason: str | None = None
    """Why a file was rejected; never content from the file."""


@dataclass(frozen=True)
class QueryHistoryIngestResult:
    accepted: int = 0
    """Query Executions stored by this ingest."""
    duplicates: int = 0
    """Already stored with the same content; nothing changed."""
    rejected: tuple[EntryRejection, ...] = ()
    """Invalid entries by position and reason; never stored, never echoed."""
    changed_copies: int = 0
    """Stored beside an observation of the same Query Execution with other content."""
    file_rejection: str | None = None
    """Why the whole file was refused (nothing stored); never content from the file."""


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
                if stored:
                    self._store_edges(key, digest, event)
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

    def ingest_dbt_artifact(self, path: Path) -> ArtifactIngestResult:
        """Store a dbt run-results (v6) or manifest (v12) file, keyed by invocation and hash.

        Run results become Model Executions; a manifest becomes per-node catalog entries and
        their `depends_on.nodes` (no SQL, descriptions, columns or macros). Re-ingesting the
        same file is a duplicate; another file of the same kind for the same invocation is
        kept beside the first and reported as a changed copy. Unacceptable content is rejected
        with a fixed reason (see `screen_dbt_artifact`); a missing path raises as usual.
        """
        screened = screen_dbt_artifact(path)
        if isinstance(screened, str):
            return ArtifactIngestResult(ArtifactOutcome.REJECTED, reason=screened)
        invocation, digest, kind = screened.invocation_id, screened.digest, screened.kind
        with self._write_transaction():
            others = self._connection.execute(
                "SELECT digest FROM workload_dbt_artifact WHERE invocation_id = ? AND kind = ?",
                (invocation, kind),
            ).fetchall()
            if (digest,) in others:
                return ArtifactIngestResult(ArtifactOutcome.DUPLICATE, invocation)
            self._connection.execute(
                "INSERT INTO workload_dbt_artifact (invocation_id, kind, digest) VALUES (?, ?, ?)",
                (invocation, kind, digest),
            )
            if isinstance(screened.artifact, RunResults):
                self._store_model_executions(screened, screened.artifact)
            else:
                self._store_manifest_nodes(screened, screened.artifact)
        outcome = ArtifactOutcome.CHANGED_COPY if others else ArtifactOutcome.STORED
        return ArtifactIngestResult(outcome, invocation)

    def ingest_query_history(
        self, path: Path, *, account_id: str, region: str
    ) -> QueryHistoryIngestResult:
        """Store an Athena BatchGetQueryExecution response read from one account and region.

        Each Query Execution is keyed by account, region and ID plus a digest of the entry as
        stored, which keeps only allowlisted metadata (`athena.STORED_FIELDS`, spec 04): query
        text, execution parameters and free-text failure reasons never reach storage. The same
        content again is a duplicate; other content for the same key is kept beside it and
        flagged.
        """
        screening = screen_query_history(path, account_id=account_id, region=region)
        if isinstance(screening, str):
            return QueryHistoryIngestResult(file_rejection=screening)
        accepted = changed_copies = 0
        with self._write_transaction():
            for screened in screening.entries:
                stored_json = _canonical(screened.stored)
                digest = hashlib.sha256(stored_json.encode("utf-8")).hexdigest()
                key = (account_id, region, screened.parsed.QueryExecutionId)
                others = self._connection.execute(
                    "SELECT 1 FROM workload_query_execution WHERE account_id = ? AND region = ?"
                    " AND query_execution_id = ? AND digest != ? LIMIT 1",
                    (*key, digest),
                ).fetchone()
                entry = screened.parsed
                completed = entry.Status.CompletionDateTime
                stored = self._connection.execute(
                    "INSERT OR IGNORE INTO workload_query_execution (account_id, region,"
                    " query_execution_id, digest, workgroup, statement_type, state,"
                    " submitted_us, completed_us, scanned_bytes, entry_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        *key,
                        digest,
                        entry.WorkGroup,
                        entry.StatementType,
                        entry.Status.State,
                        _microseconds(entry.Status.SubmissionDateTime),
                        _microseconds(completed) if completed else None,
                        entry.Statistics.DataScannedInBytes if entry.Statistics else None,
                        stored_json,
                    ),
                ).rowcount
                accepted += stored
                changed_copies += stored and others is not None
        return QueryHistoryIngestResult(
            accepted=accepted,
            duplicates=len(screening.entries) - accepted,
            rejected=tuple(screening.rejected),
            changed_copies=changed_copies,
        )

    def query_execution(
        self, account_id: str, region: str, query_execution_id: str
    ) -> QueryExecution | None:
        """The Query Execution as reported, or None if unknown.

        When the provider reported it with different content, the fields come from the
        observation with the lowest digest, so the answer does not depend on arrival order,
        and the Query Execution is flagged.
        """
        rows = self._query_observations(account_id, region, query_execution_id)
        if not rows:
            return None
        digest, workgroup, statement_type, state, submitted, completed, scanned, _ = rows[0]
        return QueryExecution(
            provider=_ATHENA,
            account_id=account_id,
            region=region,
            query_execution_id=query_execution_id,
            workgroup=workgroup,
            statement_type=statement_type,
            state=state,
            submitted_at=_moment(submitted),
            completed_at=_moment(completed) if completed is not None else None,
            scanned_bytes=scanned,
            provenance=digest,
            flags=(QueryExecutionFlag.CONFLICTING_OBSERVATIONS,) if len(rows) > 1 else (),
        )

    def stored_query_executions(
        self, account_id: str, region: str, query_execution_id: str
    ) -> list[str]:
        """The Query Execution's observations exactly as stored (canonical JSON), by digest."""
        rows = self._query_observations(account_id, region, query_execution_id)
        return [row[-1] for row in rows]

    def _query_observations(
        self, account_id: str, region: str, query_execution_id: str
    ) -> list[tuple[Any, ...]]:
        """A Query Execution's stored observations by digest, `entry_json` last."""
        return self._connection.execute(
            "SELECT digest, workgroup, statement_type, state, submitted_us, completed_us,"
            " scanned_bytes, entry_json FROM workload_query_execution WHERE account_id = ?"
            " AND region = ? AND query_execution_id = ? ORDER BY digest",
            (account_id, region, query_execution_id),
        ).fetchall()

    def _store_model_executions(self, screened: ScreenedArtifact, results: RunResults) -> None:
        invocation, digest = screened.invocation_id, screened.digest
        self._connection.executemany(
            "INSERT INTO workload_model_execution (invocation_id, node_id,"
            " artifact_digest, status, started_us, completed_us) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (invocation, result.unique_id, digest, result.status, *_span(result.timing))
                for result in results.results
            ],
        )

    def _store_manifest_nodes(self, screened: ScreenedArtifact, manifest: Manifest) -> None:
        invocation, digest = screened.invocation_id, screened.digest
        self._connection.executemany(
            "INSERT INTO workload_manifest_node (invocation_id, node_id, artifact_digest,"
            " resource_type, name, package_name, relation_name, materialized, depends_on)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    invocation,
                    node.unique_id,
                    digest,
                    node.resource_type,
                    node.name,
                    node.package_name,
                    node.relation_name,
                    node.config.materialized,
                    json.dumps(sorted(set(node.depends_on.nodes))),
                )
                for node in manifest.nodes.values()
            ],
        )

    def manifest_nodes(self, invocation_id: str) -> list[DbtNode]:
        """The invocation's manifest nodes and dependencies, by node; a changed copy adds rows."""
        rows = self._connection.execute(
            "SELECT node_id, resource_type, name, package_name, relation_name, materialized,"
            " depends_on, artifact_digest FROM workload_manifest_node WHERE invocation_id = ?"
            " ORDER BY node_id, artifact_digest",
            (invocation_id,),
        ).fetchall()
        flags = _artifact_flags(row[7] for row in rows)
        return [
            DbtNode(
                node_id=node_id,
                invocation_id=invocation_id,
                resource_type=resource_type,
                name=name,
                package_name=package,
                relation_name=relation,
                materialized=materialized,
                depends_on=tuple(json.loads(depends_on)),
                provenance=digest,
                flags=flags,
            )
            for (
                node_id,
                resource_type,
                name,
                package,
                relation,
                materialized,
                depends_on,
                digest,
            ) in rows
        ]

    def model_executions(self, invocation_id: str) -> list[ModelExecution]:
        """The invocation's Model Executions, by node; a changed copy adds its own rows."""
        rows = self._connection.execute(
            "SELECT node_id, status, started_us, completed_us, artifact_digest"
            " FROM workload_model_execution WHERE invocation_id = ?"
            " ORDER BY node_id, artifact_digest",
            (invocation_id,),
        ).fetchall()
        flags = _artifact_flags(row[4] for row in rows)
        return [
            ModelExecution(
                node_id=node_id,
                invocation_id=invocation_id,
                status=status,
                started_at=_moment(started) if started is not None else None,
                completed_at=_moment(completed) if completed is not None else None,
                provenance=digest,
                flags=flags,
            )
            for node_id, status, started, completed, digest in rows
        ]

    def data_assets(self, run_id: str) -> list[DataAsset]:
        """The Data Assets the run read or wrote, as OpenLineage names them; by namespace, name."""
        rows = self._connection.execute(
            "SELECT DISTINCT namespace, name FROM workload_lineage_edge WHERE run_id = ?"
            " ORDER BY namespace, name",
            (run_id,),
        ).fetchall()
        return [DataAsset(namespace=namespace, name=name) for namespace, name in rows]

    def lineage_edges(self, run_id: str) -> list[LineageEdge]:
        """One edge per stored event, Data Asset and direction, ordered by observation time.

        Each edge's provenance is the digest of the event observation that reported it, so the
        edges of two changed copies of one event can be told apart.
        """
        rows = self._connection.execute(
            "SELECT direction, namespace, name, event_time_us, digest FROM workload_lineage_edge"
            " WHERE run_id = ? ORDER BY event_time_us, direction, namespace, name, digest",
            (run_id,),
        ).fetchall()
        return [
            LineageEdge(
                run_id=run_id,
                asset=DataAsset(namespace=namespace, name=name),
                direction=LineageDirection(direction),
                observed_at=_moment(event_time_us),
                provenance=digest,
            )
            for direction, namespace, name, event_time_us, digest in rows
        ]

    def stored_events(self, run_id: str) -> list[str]:
        """The run's events exactly as stored (canonical JSON), oldest first."""
        rows = self._connection.execute(
            "SELECT event_json FROM workload_lineage_event WHERE run_id = ?"
            " ORDER BY event_time_us, digest",
            (run_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def model_execution_of_run(self, run_id: str) -> LinkResolution | None:
        """The Model Execution a dbt-ol model run reports, as a Link Outcome (ADR 0022).

        Evidence is the `dbt_node.unique_id` job facet with the `dbt_run.invocation_id` run
        facet, taken from every stored observation of the run. A run without them has no
        evidence; None if the run is unknown. Derived when asked, so arrival order of events
        and run results does not matter.
        """
        events = self.stored_events(run_id)
        if not events:
            return None
        claims = {claim for claim in map(_model_run_claim, events) if claim}
        return resolve_model_run(run_id, claims, self._reported_model_executions(claims))

    def _reported_model_executions(
        self, keys: Iterable[ModelExecutionKey]
    ) -> set[ModelExecutionKey]:
        return {
            key
            for key in keys
            if self._connection.execute(
                "SELECT 1 FROM workload_model_execution WHERE invocation_id = ? AND node_id = ?"
                " LIMIT 1",
                key,
            ).fetchone()
        }

    def _store_edges(self, key: _EventKey, digest: str, event: RunEvent) -> None:
        """Edges from the event's inputs and outputs, names kept exactly as reported."""
        self._connection.executemany(
            "INSERT OR IGNORE INTO workload_lineage_edge"
            " (run_id, digest, event_time_us, direction, namespace, name)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (key.run_id, digest, key.event_time_us, direction, dataset.namespace, dataset.name)
                for direction, datasets in (
                    (LineageDirection.INPUT, event.inputs),
                    (LineageDirection.OUTPUT, event.outputs),
                )
                for dataset in datasets
            ],
        )

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


def _model_run_claim(event_json: str) -> ModelExecutionKey | None:
    """(invocation, node) from one stored event's dbt-ol facets, if it carries both."""
    event = json.loads(event_json)
    node = event.get("job", {}).get("facets", {}).get("dbt_node", {}).get("unique_id")
    invocation = event.get("run", {}).get("facets", {}).get("dbt_run", {}).get("invocation_id")
    if isinstance(node, str) and node and isinstance(invocation, str) and invocation:
        return (invocation, node)
    return None


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


def _span(timing: list[Timing]) -> tuple[int | None, int | None]:
    """Earliest start and latest completion across dbt's timing steps, as UTC microseconds."""
    starts = [_microseconds(step.started_at) for step in timing if step.started_at]
    ends = [_microseconds(step.completed_at) for step in timing if step.completed_at]
    return (min(starts) if starts else None, max(ends) if ends else None)


def _artifact_flags(digests: Iterable[str]) -> tuple[DbtObservationFlag, ...]:
    """Flag every row of an invocation's artifact kind when more than one file was ingested."""
    return (DbtObservationFlag.CONFLICTING_OBSERVATIONS,) if len(set(digests)) > 1 else ()


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
