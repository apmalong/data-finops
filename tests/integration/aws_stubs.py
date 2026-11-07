"""botocore Stubbers standing in for AWS in the collector tests (spec 08 seams 1 and 2).

No request ever leaves the process: every client has fake credentials and an activated
Stubber, which fails the test on any call it was not told to expect.
"""

import io
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import boto3
from botocore.response import StreamingBody
from botocore.stub import Stubber

BUCKET = "example-deployment-bucket"
REGION = "us-east-1"


ACCOUNT = "123456789012"
WORKGROUP = "demo"


def _client(service: str) -> Any:
    return boto3.client(
        service,
        region_name=REGION,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",  # noqa: S106 (a fake credential for the Stubber)
    )


def s3_client() -> Any:
    return _client("s3")


def athena_client() -> Any:
    return _client("athena")


def expect_history(
    stubber: Stubber,
    ids: list[str],
    *,
    workgroup: str = WORKGROUP,
    token: str | None = None,
    next_token: str | None = None,
) -> None:
    """One `ListQueryExecutions` page of `ids` for `workgroup`."""
    params: dict[str, Any] = {"WorkGroup": workgroup, "MaxResults": 50}
    if token is not None:
        params["NextToken"] = token
    page: dict[str, Any] = {"QueryExecutionIds": ids} if ids else {}  # the model forbids []
    if next_token is not None:
        page["NextToken"] = next_token
    stubber.add_response("list_query_executions", page, params)


@contextmanager
def stubbed(client: Any) -> Iterator[Stubber]:
    """The client's Stubber, activated; asserts every expected call was made."""
    with Stubber(client) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


def etag(name: str) -> str:
    return f'"{name}"'


def expect_listing(
    stubber: Stubber,
    prefix: str,
    objects: Mapping[str, bytes],
    *,
    token: str | None = None,
    next_token: str | None = None,
    sizes: Mapping[str, int] | None = None,
    etags: Mapping[str, str] | None = None,
) -> None:
    """One `ListObjectsV2` page listing `objects` (key → content) under `prefix`; with
    `next_token`, a truncated page whose next one the caller expects too."""
    params: dict[str, Any] = {"Bucket": BUCKET, "Prefix": prefix}
    if token is not None:
        params["ContinuationToken"] = token
    page: dict[str, Any] = {
        "IsTruncated": next_token is not None,
        "KeyCount": len(objects),
        "Contents": [
            {
                "Key": key,
                "ETag": (etags or {}).get(key, etag(key)),
                "Size": (sizes or {}).get(key, len(data)),
            }
            for key, data in objects.items()
        ],
    }
    if next_token is not None:
        page["NextContinuationToken"] = next_token
    stubber.add_response("list_objects_v2", page, params)


def expect_denied(stubber: Stubber, operation: str) -> None:
    """The call fails as S3 refuses an identity without the permission; AWS's message names
    the principal, which must never reach a summary."""
    stubber.add_client_error(
        operation,
        service_error_code="AccessDenied",
        service_message="User: arn:aws:iam::123456789012:role/secret-role is not authorized",
        http_status_code=403,
    )


def expect_get(stubber: Stubber, key: str, data: bytes, *, version: str | None = None) -> None:
    """One `GetObject` of `key` at the ETag the listing gave it."""
    stubber.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(data), len(data)), "ContentLength": len(data)},
        {"Bucket": BUCKET, "Key": key, "IfMatch": version or etag(key)},
    )


def as_returned(entry: Mapping[str, Any]) -> dict[str, Any]:
    """A BatchGetQueryExecution entry as boto3 returns it: timestamps as datetimes."""
    status = dict(entry["Status"])
    for field in ("SubmissionDateTime", "CompletionDateTime"):
        if field in status:
            status[field] = datetime.fromisoformat(status[field])
    return dict(entry) | {"Status": status}


def expect_batch(
    stubber: Stubber,
    entries: list[Mapping[str, Any]],
    *,
    unprocessed: Mapping[str, str] | None = None,
) -> None:
    """One `BatchGetQueryExecution` of these entries' IDs (then any `unprocessed` ones, ID →
    error code), answering the entries and reporting the others unprocessed."""
    ids = [e["QueryExecutionId"] for e in entries] + list(unprocessed or {})
    stubber.add_response(
        "batch_get_query_execution",
        {
            "QueryExecutions": [as_returned(e) for e in entries],
            "UnprocessedQueryExecutionIds": [
                {"QueryExecutionId": query, "ErrorCode": code, "ErrorMessage": "secret detail"}
                for query, code in (unprocessed or {}).items()
            ],
        },
        {"QueryExecutionIds": ids},
    )
