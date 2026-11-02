"""Athena query history in the shape of the BatchGetQueryExecution response.

Field names follow the botocore Athena service model (2017-05-18). Unknown fields
are preserved. The query text is held only until correlation is extracted (M3);
it must not be persisted (spec 04, data protection).
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


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


MAX_HISTORY_BYTES = 512 * 1024 * 1024
"""Per query history file, the same cap as a dbt artifact (spec 04)."""


@dataclass(frozen=True)
class EntryRejection:
    position: int
    """1-based position in `QueryExecutions`."""
    detail: str
    """The failing field and rule, e.g. `Status.State (missing)`; never a value."""


@dataclass(frozen=True)
class ScreenedEntry:
    entry: AthenaQueryExecution
    document: dict[str, Any]
    """The entry as the file reported it."""


@dataclass(frozen=True)
class QueryHistoryScreening:
    account_id: str
    region: str
    entries: list[ScreenedEntry]
    rejected: list[EntryRejection]


def screen_query_history(
    path: Path, *, account_id: str, region: str
) -> QueryHistoryScreening | str:
    """The valid entries and per-entry rejections, or why the whole file is unacceptable.

    The file must be a BatchGetQueryExecution response (optionally recording its account and
    region, which must agree with those given) of at most MAX_HISTORY_BYTES. One bad entry
    never stops the file; reasons name fields and rules, never values.
    """
    if path.stat().st_size > MAX_HISTORY_BYTES:
        return "too large"
    try:
        document = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return "not a BatchGetQueryExecution response"
    if not isinstance(document, dict) or not isinstance(document.get("QueryExecutions"), list):
        return "not a BatchGetQueryExecution response"
    for key, given in (("account_id", account_id), ("region", region)):
        if document.get(key, given) != given:
            return f"{key} disagrees with the one recorded in the file"
    entries: list[ScreenedEntry] = []
    rejected: list[EntryRejection] = []
    for position, item in enumerate(document["QueryExecutions"], start=1):
        try:
            entries.append(ScreenedEntry(AthenaQueryExecution.model_validate(item), item))
        except ValidationError as error:
            rejected.append(EntryRejection(position, _detail(error)))
    return QueryHistoryScreening(account_id, region, entries, rejected)


def _detail(error: ValidationError) -> str:
    first = error.errors(include_input=False)[0]
    field = ".".join(str(part) for part in first["loc"]) or "entry"
    return f"{field} ({first['type']})"


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
