"""Reading Athena query history for a Collection Run (spec 08 §3)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from data_finops.providers.aws.s3 import call

PAGE_SIZE = 50
"""The most IDs one ListQueryExecutions page or one BatchGetQueryExecution call takes."""


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
    CLI writes it (timestamps as ISO 8601 strings), for the existing history reader."""
    executions: list[Any] = []
    unprocessed: list[Any] = []
    for start in range(0, len(ids), PAGE_SIZE):
        response = call(
            athena.batch_get_query_execution, QueryExecutionIds=ids[start : start + PAGE_SIZE]
        )
        executions += response.get("QueryExecutions", ())
        unprocessed += response.get("UnprocessedQueryExecutionIds", ())
    return {"QueryExecutions": _as_json(executions), "UnprocessedQueryExecutionIds": unprocessed}


def _as_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _as_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_as_json(item) for item in value]
    return value
