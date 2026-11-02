"""Workload identity and lineage contracts (spec 02). Populated from M2 onwards."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_finops.contracts.types import UtcDatetime


class QueryCorrelation(BaseModel):
    """Identifiers extracted from a trusted query comment; the query text is not kept."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    invocation_id: str


class QueryExecutionFlag(StrEnum):
    CONFLICTING_OBSERVATIONS = "conflicting_observations"
    """The provider reported this Query Execution more than once with different content."""


class QueryExecution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    account_id: str
    region: str
    query_execution_id: str
    workgroup: str | None = None
    statement_type: str | None = None
    state: str
    submitted_at: UtcDatetime
    completed_at: UtcDatetime | None = None
    scanned_bytes: int | None = Field(default=None, ge=0)
    """As reported; absent when the provider reported no statistics."""
    billable_bytes: int | None = Field(default=None, ge=0)
    """Set only from the versioned provider policy (ADR 0017), by the Calculation Run (#88);
    for Athena, `providers.athena_billing.billable_bytes`."""
    correlation: QueryCorrelation | None = None
    provenance: str | None = None
    """The digest of the stored observation these fields come from."""
    flags: tuple[QueryExecutionFlag, ...] = ()


class DbtObservationFlag(StrEnum):
    CONFLICTING_OBSERVATIONS = "conflicting_observations"
    """More than one file of this artifact kind was ingested for the invocation."""


class ModelExecution(BaseModel):
    """One run of a dbt node within one invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    invocation_id: str
    status: str
    """dbt's own status value as reported (success, error, skipped, fail, warn, pass, ...)."""
    started_at: UtcDatetime | None = None
    completed_at: UtcDatetime | None = None
    provenance: str | None = None
    """The SHA-256 of the run-results file that reported this execution."""
    flags: tuple[DbtObservationFlag, ...] = ()


class DbtNode(BaseModel):
    """A node of one invocation's dbt manifest: catalog entry and static dependencies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    invocation_id: str
    resource_type: str
    name: str
    package_name: str | None = None
    relation_name: str | None = None
    materialized: str | None = None
    depends_on: tuple[str, ...] = ()
    provenance: str | None = None
    """The SHA-256 of the manifest file that reported this node."""
    flags: tuple[DbtObservationFlag, ...] = ()


class WorkloadRunStatus(StrEnum):
    """Derived from a run's terminal event, whatever the arrival order (spec 02)."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    CONFLICTING = "conflicting"
    """Two different terminal events were reported; never resolved silently."""


class WorkloadRunFlag(StrEnum):
    """Disagreements among a run's stored observations, shown rather than resolved."""

    CONFLICTING_OBSERVATIONS = "conflicting_observations"
    """Two events with the same run, event type and time but different content."""
    CONFLICTING_JOBS = "conflicting_jobs"
    """The run's events name different jobs."""
    CONFLICTING_PARENTS = "conflicting_parents"
    """The run's events name different direct parents (run or job)."""


class WorkloadRun(BaseModel):
    """One OpenLineage run as reported, including dbt-ol's per-model runs (ADR 0022)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    system: str
    job_namespace: str
    job_name: str
    run_id: str
    parent_run_id: str | None = None
    parent_job_namespace: str | None = None
    parent_job_name: str | None = None
    root_run_id: str | None = None
    root_job_namespace: str | None = None
    root_job_name: str | None = None
    status: WorkloadRunStatus
    flags: tuple[WorkloadRunFlag, ...] = ()
    started_at: UtcDatetime | None = None
    ended_at: UtcDatetime | None = None


class DataAsset(BaseModel):
    """Identified by the OpenLineage dataset naming convention."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    namespace: str
    name: str


class LineageDirection(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class LineageEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    asset: DataAsset
    direction: LineageDirection
    observed_at: UtcDatetime
    provenance: str | None = None
    """The digest of the stored event observation that reported this edge."""


class IdentityLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: str
    source_id: str
    target_type: str
    target_id: str
    evidence_type: str
    evidence_value: str
    policy_version: str


class LinkOutcome(StrEnum):
    """What became of an attempt to link a workload identity (GLOSSARY: Link Outcome)."""

    LINKED = "linked"
    """Exactly one counterpart; the only outcome that makes an Identity Link."""
    NO_EVIDENCE = "no_evidence"
    """Nothing explicit names a counterpart; nothing is inferred (ADR 0006)."""
    UNMATCHED = "unmatched"
    """The Evidence names a counterpart nobody reported; no link is made."""
    AMBIGUOUS = "ambiguous"
    """The Evidence names more than one counterpart; no link is made."""


class LinkResolution(BaseModel):
    """A Link Outcome, with the Identity Link only when the outcome is `linked`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: LinkOutcome
    link: IdentityLink | None = None

    @model_validator(mode="after")
    def _link_only_when_linked(self) -> Self:
        if (self.link is not None) != (self.outcome == LinkOutcome.LINKED):
            raise ValueError("an Identity Link is present exactly when the outcome is linked")
        return self
