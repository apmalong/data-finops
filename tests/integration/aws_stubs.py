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
from botocore.stub import ANY, Stubber

from data_finops.collection import CollectionSettings
from data_finops.collection.remote import DATABASE_KEY

BUCKET = "example-deployment-bucket"
REGION = "us-east-1"


ACCOUNT = "123456789012"
WORKGROUP = "demo"
EXPORT_BUCKET = "example-export-bucket"
EXPORT_PREFIX = "focus"
EXPORT_NAME = "example-focus-1-2"
METADATA = f"{EXPORT_PREFIX}/{EXPORT_NAME}/metadata/"


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
    bucket: str = BUCKET,
) -> None:
    """One `ListObjectsV2` page listing `objects` (key → content) under `prefix`; with
    `next_token`, a truncated page whose next one the caller expects too."""
    params: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
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


def expect_get(
    stubber: Stubber, key: str, data: bytes, *, version: str | None = None, bucket: str = BUCKET
) -> None:
    """One `GetObject` of `key` at the ETag the listing gave it."""
    stubber.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(data), len(data)), "ContentLength": len(data)},
        {"Bucket": bucket, "Key": key, "IfMatch": version or etag(key)},
    )


def expect_prefixes(
    stubber: Stubber, prefix: str, names: list[str], *, bucket: str = EXPORT_BUCKET
) -> None:
    """One delimited `ListObjectsV2` page: the 'folders' directly under `prefix`."""
    page: dict[str, Any] = {"IsTruncated": False, "KeyCount": len(names)}
    if names:  # the model forbids an empty list
        page["CommonPrefixes"] = [{"Prefix": f"{prefix}{name}/"} for name in names]
    stubber.add_response(
        "list_objects_v2", page, {"Bucket": bucket, "Prefix": prefix, "Delimiter": "/"}
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


DEPLOYMENT = CollectionSettings(  # the settings of the Deployment the stubs stand for
    bucket=BUCKET,
    region=REGION,
    account_id=ACCOUNT,
    workgroups=(WORKGROUP,),
    export_bucket=EXPORT_BUCKET,
    export_prefix=EXPORT_PREFIX,
    export_name=EXPORT_NAME,
)


def expect_quiet_sources(s3_stub: Stubber, athena_stub: Stubber) -> None:
    """Every source empty: the Inboxes, the history and the export."""
    expect_listing(s3_stub, "inbox/dbt/", {})
    expect_listing(s3_stub, "inbox/openlineage/", {})
    expect_history(athena_stub, [])
    expect_prefixes(s3_stub, METADATA, [])


def expect_database_put(s3_stub: Stubber, condition: dict[str, str]) -> None:
    s3_stub.add_response(
        "put_object",
        {"ETag": '"written"'},
        {"Bucket": BUCKET, "Key": DATABASE_KEY, "Body": ANY} | condition,
    )
