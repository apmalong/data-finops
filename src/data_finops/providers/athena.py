"""Athena query history in the shape of the BatchGetQueryExecution response.

Field names follow the botocore Athena service model (2017-05-18). Unknown fields
are preserved. The query text is held only until correlation is extracted (M3);
it must not be persisted (spec 04, data protection).
"""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class _Provider(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")


class QueryExecutionStatus(_Provider):
    State: str
    SubmissionDateTime: datetime
    CompletionDateTime: datetime | None = None


class QueryExecutionStatistics(_Provider):
    DataScannedInBytes: int = Field(ge=0)


class AthenaQueryExecution(_Provider):
    QueryExecutionId: str
    Query: str
    StatementType: str | None = None
    WorkGroup: str | None = None
    Status: QueryExecutionStatus
    Statistics: QueryExecutionStatistics | None = None


class AthenaQueryHistory(BaseModel):
    """A BatchGetQueryExecution response plus the account and region it was read from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str
    region: str
    QueryExecutions: list[AthenaQueryExecution]
    UnprocessedQueryExecutionIds: list[dict[str, str]] = []


def load_query_executions(path: Path) -> AthenaQueryHistory:
    return AthenaQueryHistory.model_validate_json(path.read_text(encoding="utf-8"))
