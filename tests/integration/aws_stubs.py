"""botocore Stubbers standing in for AWS in the collector tests (spec 08 seams 1 and 2).

No request ever leaves the process: every client has fake credentials and an activated
Stubber, which fails the test on any call it was not told to expect.
"""

import io
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import boto3
from botocore.response import StreamingBody
from botocore.stub import Stubber

BUCKET = "example-deployment-bucket"
REGION = "us-east-1"


def s3_client() -> Any:
    return boto3.client(
        "s3",
        region_name=REGION,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",  # noqa: S106 (a fake credential for the Stubber)
    )


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
