"""The AWS collectors' public functions with botocore's Stubber (spec 08 seam 1)."""

import pytest
from aws_stubs import BUCKET, expect_listing, s3_client, stubbed

from data_finops.providers.aws import AwsSourceError, S3Object, list_objects


def test_listing_follows_every_page_in_order() -> None:
    s3 = s3_client()
    with stubbed(s3) as stubber:
        expect_listing(stubber, "inbox/", {"inbox/a": b"1", "inbox/b": b"22"}, next_token="t1")
        expect_listing(stubber, "inbox/", {"inbox/c": b"333"}, token="t1")
        listed = list(list_objects(s3, BUCKET, "inbox/"))
    assert listed == [
        S3Object("inbox/a", '"inbox/a"', 1),
        S3Object("inbox/b", '"inbox/b"', 2),
        S3Object("inbox/c", '"inbox/c"', 3),
    ]


def test_a_failed_call_is_reported_by_its_aws_error_code_alone() -> None:
    s3 = s3_client()
    with stubbed(s3) as stubber:
        stubber.add_client_error(
            "list_objects_v2",
            service_error_code="ExpiredToken",  # not in S3's model: a plain ClientError
            service_message="The token for arn:aws:iam::123456789012:role/secret has expired",
            http_status_code=400,
        )
        with pytest.raises(AwsSourceError) as raised:
            list(list_objects(s3, BUCKET, "inbox/"))
    assert str(raised.value) == "ExpiredToken"
