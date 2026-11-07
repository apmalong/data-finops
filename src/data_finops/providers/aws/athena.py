"""Reading Athena query history for a Collection Run (spec 08 §3)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from data_finops.providers.aws.calls import call

PAGE_SIZE = 50
"""The most IDs one ListQueryExecutions page or one BatchGetQueryExecution call takes."""

UNFINISHED = frozenset({"QUEUED", "RUNNING"})
"""Athena states a query can still leave; every other state is final."""

WITHHELD = ("ExecutionParameters", "ResultConfiguration", "ResultReuseConfiguration")
"""Fields of an entry dropped as it is fetched: none is stored (`athena.STORED_FIELDS`)."""


class AthenaReader(Protocol):
    """The two Athena calls a collector makes, as boto3's client offers them. Neither runs a
    query or reads results (`docs/research/m6-collector-iam.md`)."""

    def list_query_executions(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def batch_get_query_execution(self, **kwargs: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class HistoryListing:
    ids: tuple[str, ...]
    """In Athena's order, which its documentation does not specify."""
    complete: bool
    """Whether the listing reached the end of the workgroup's history, not the page cap."""


def list_query_execution_ids(
    athena: AthenaReader, workgroup: str, *, max_pages: int
) -> HistoryListing:
    """The workgroup's query IDs as Athena lists them, at most `max_pages` pages of 50."""
    params: dict[str, Any] = {"WorkGroup": workgroup, "MaxResults": PAGE_SIZE}
    ids: list[str] = []
    for _ in range(max_pages):
        page = call(athena.list_query_executions, **params)
        ids += page.get("QueryExecutionIds", ())
        if "NextToken" not in page:
            return HistoryListing(tuple(ids), complete=True)
        params["NextToken"] = page["NextToken"]
    return HistoryListing(tuple(ids), complete=False)


def batch_get_query_executions(athena: AthenaReader, ids: list[str]) -> dict[str, Any]:
    """One BatchGetQueryExecution response for `ids`, fetched 50 at a time, shaped as the AWS
    CLI writes it (timestamps as ISO 8601 strings), each entry minimised, for the existing
    history reader."""
    executions: list[Any] = []
    unprocessed: list[Any] = []
    for start in range(0, len(ids), PAGE_SIZE):
        response = call(
            athena.batch_get_query_execution, QueryExecutionIds=ids[start : start + PAGE_SIZE]
        )
        executions += response.get("QueryExecutions", ())
        unprocessed += response.get("UnprocessedQueryExecutionIds", ())
    return {
        "QueryExecutions": [_minimised(entry) for entry in _as_json(executions)],
        "UnprocessedQueryExecutionIds": unprocessed,
    }


def _minimised(entry: dict[str, Any]) -> dict[str, Any]:
    """The entry with its query text cut to the first line, the only place a Trusted Query
    Comment is read (spec 04), and the withheld fields dropped, so no SQL beyond that line
    and no parameter ever reaches disk."""
    kept = {key: value for key, value in entry.items() if key not in WITHHELD}
    if isinstance(kept.get("Query"), str):
        kept["Query"] = kept["Query"].split("\n", 1)[0]
    return kept


def _as_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _as_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_as_json(item) for item in value]
    return value
