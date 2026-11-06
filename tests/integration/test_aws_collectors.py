"""The AWS collectors' public functions with botocore's Stubber (spec 08 seam 1)."""

import pytest
from aws_stubs import BUCKET, expect_get, expect_listing, s3_client, stubbed

from data_finops.providers.aws import (
    AwsSourceError,
    S3Object,
    TooLarge,
    list_objects,
    read_object,
)


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


def test_an_object_listed_over_the_cap_is_refused_before_any_download() -> None:
    s3 = s3_client()
    with stubbed(s3), pytest.raises(TooLarge):  # no GetObject expected
        read_object(s3, BUCKET, S3Object("inbox/a", '"inbox/a"', 11), max_bytes=10)


def test_a_body_longer_than_the_cap_is_refused_whatever_the_listing_said() -> None:
    s3 = s3_client()
    with stubbed(s3) as stubber, pytest.raises(TooLarge):
        expect_get(stubber, "inbox/a", b"x" * 11)
        read_object(s3, BUCKET, S3Object("inbox/a", '"inbox/a"', 3), max_bytes=10)


def test_an_object_within_the_cap_is_read_at_its_listed_version() -> None:
    s3 = s3_client()
    with stubbed(s3) as stubber:
        expect_get(stubber, "inbox/a", b"0123456789")
        data = read_object(s3, BUCKET, S3Object("inbox/a", '"inbox/a"', 10), max_bytes=10)
    assert data == b"0123456789"
