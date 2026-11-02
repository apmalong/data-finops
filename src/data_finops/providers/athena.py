"""Athena query history in the shape of the BatchGetQueryExecution response.

Field names follow the botocore Athena service model (2017-05-18). Reading preserves
unknown fields; storing keeps only the allowlisted metadata in `STORED_FIELDS`, so the
query text, execution parameters and free-text failure reasons are never persisted
(spec 04, data protection).
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
"""Per query history file (spec 04)."""

_KEEP = True
STORED_FIELDS: dict[str, Any] = {
    "QueryExecutionId": _KEEP,
    "StatementType": _KEEP,
    "SubstatementType": _KEEP,
    "WorkGroup": _KEEP,
    "QueryExecutionContext": {"Catalog": _KEEP, "Database": _KEEP},
    "Status": {
        "State": _KEEP,
        "SubmissionDateTime": _KEEP,
        "CompletionDateTime": _KEEP,
        "AthenaError": {"ErrorCategory": _KEEP, "ErrorType": _KEEP, "Retryable": _KEEP},
    },
    "Statistics": {
        name: _KEEP
        for name in (
            "DataScannedInBytes",
            "EngineExecutionTimeInMillis",
            "TotalExecutionTimeInMillis",
            "QueryQueueTimeInMillis",
            "ServicePreProcessingTimeInMillis",
            "QueryPlanningTimeInMillis",
            "ServiceProcessingTimeInMillis",
        )
    },
    "EngineVersion": {"SelectedEngineVersion": _KEEP, "EffectiveEngineVersion": _KEEP},
}
"""The metadata kept when a Query Execution is stored; everything else is dropped.

An allowlist, not a denylist (spec 04): `Query`, `ExecutionParameters` (literal values of a
parameterized query), `StateChangeReason` and `AthenaError.ErrorMessage` (which can quote
the query) and any field AWS adds later never reach storage.
"""


@dataclass(frozen=True)
class EntryRejection:
    position: int
    """1-based position in `QueryExecutions`."""
    detail: str
    """The failing field and rule, e.g. `Status.State (missing)`; never a value."""


@dataclass(frozen=True)
class ScreenedEntry:
    parsed: AthenaQueryExecution
    stored: dict[str, Any]
    """The entry reduced to `STORED_FIELDS`, ready to persist."""


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
    disagreement = _disagreement(document, account_id=account_id, region=region)
    if disagreement:
        return f"{disagreement} disagrees with the one recorded in the file"
    entries: list[ScreenedEntry] = []
    rejected: list[EntryRejection] = []
    for position, item in enumerate(document["QueryExecutions"], start=1):
        try:
            parsed = AthenaQueryExecution.model_validate(item)
            entries.append(ScreenedEntry(parsed, _allowlisted(item, STORED_FIELDS)))
        except ValidationError as error:
            rejected.append(EntryRejection(position, _detail(error)))
    return QueryHistoryScreening(account_id, region, entries, rejected)


def _disagreement(document: dict[str, Any], **given: str | None) -> str | None:
    """The first of account and region given here that the file records otherwise."""
    for key, value in given.items():
        recorded = document.get(key)
        if value is not None and recorded is not None and recorded != value:
            return key
    return None


def _allowlisted(value: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    kept = {}
    for name, rule in fields.items():
        if name not in value:
            continue
        if rule is _KEEP:
            kept[name] = value[name]
        elif isinstance(value[name], dict):
            kept[name] = _allowlisted(value[name], rule)
    return kept


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
    disagreement = _disagreement(document, account_id=account_id, region=region)
    if disagreement:
        raise ValueError(f"{path}: {disagreement} disagrees with the one recorded")
    for key, given in (("account_id", account_id), ("region", region)):
        if given is not None:
            document[key] = given
    return AthenaQueryHistory.model_validate(document)
