"""Athena query history in the shape of the BatchGetQueryExecution response.

Field names follow the botocore Athena service model (2017-05-18). Unknown fields
are preserved. The query text is held only until correlation is extracted (M3);
it must not be persisted (spec 04, data protection).
"""

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class _Provider(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow", hide_input_in_errors=True)


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

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    account_id: str
    region: str
    QueryExecutions: list[AthenaQueryExecution]
    UnprocessedQueryExecutionIds: list[dict[str, str]] = []


def load_query_executions(
    path: Path, *, account_id: str | None = None, region: str | None = None
) -> AthenaQueryHistory:
    """Read a BatchGetQueryExecution response, plain or with account and region recorded.

    The AWS response does not name its account or region, so for a plain response the
    caller supplies them. A value given here must agree with one recorded in the file.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected a BatchGetQueryExecution response object")
    for key, given in (("account_id", account_id), ("region", region)):
        if given is None:
            continue
        recorded = document.get(key)
        if recorded is not None and recorded != given:
            raise ValueError(f"{path}: {key} {given!r} disagrees with recorded {recorded!r}")
        document[key] = given
    return AthenaQueryHistory.model_validate(document)
