"""Workload identity and lineage contracts (spec 02). Populated from M2 onwards."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from data_finops.contracts.types import UtcDatetime


class QueryCorrelation(BaseModel):
    """Identifiers extracted from a trusted query comment; the query text is not kept."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    invocation_id: str


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
    scanned_bytes: int = Field(ge=0)
    billable_bytes: int | None = Field(default=None, ge=0)
    correlation: QueryCorrelation | None = None


class ModelExecution(BaseModel):
    """One run of a dbt node within one invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    invocation_id: str
    status: str
    started_at: UtcDatetime | None = None
    completed_at: UtcDatetime | None = None


class WorkloadRunStatus(StrEnum):
    """Derived from a run's terminal event, whatever the arrival order (spec 02)."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    CONFLICTING = "conflicting"
    """Two different terminal events were reported; never resolved silently."""


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
    status: WorkloadRunStatus
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


class IdentityLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: str
    source_id: str
    target_type: str
    target_id: str
    evidence_type: str
    evidence_value: str
    policy_version: str
