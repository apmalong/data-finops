"""The AWS collectors' public functions with botocore's Stubber (spec 08 seam 1)."""

from pathlib import Path

import pytest
from aws_stubs import (
    BUCKET,
    athena_client,
    expect_batch,
    expect_get,
    expect_history,
    expect_listing,
    s3_client,
    stubbed,
)

from data_finops.providers.aws import (
    AwsSourceError,
    S3Object,
    TooLarge,
    batch_get_query_executions,
    download_object,
    list_folders,
    list_objects,
    list_query_execution_ids,
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


def _entry(query: str) -> dict[str, object]:
    return {
        "QueryExecutionId": query,
        "Status": {"State": "SUCCEEDED", "SubmissionDateTime": "2026-11-01T21:18:59+00:00"},
    }


def test_history_is_fetched_fifty_ids_at_a_time_with_timestamps_as_text() -> None:
    ids = [f"q-{n:03}" for n in range(51)]
    athena = athena_client()
    with stubbed(athena) as stubber:
        expect_batch(stubber, [_entry(q) for q in ids[:50]])
        expect_batch(stubber, [_entry(ids[50])])
        response = batch_get_query_executions(athena, ids)
    assert [e["QueryExecutionId"] for e in response["QueryExecutions"]] == ids
    assert response["QueryExecutions"][0]["Status"]["SubmissionDateTime"] == (
        "2026-11-01T21:18:59+00:00"
    )


def test_history_listing_follows_pages_up_to_the_cap() -> None:
    athena = athena_client()
    with stubbed(athena) as stubber:
        expect_history(stubber, ["q-1", "q-2"], next_token="t1")
        expect_history(stubber, ["q-3"], token="t1")
        whole = list_query_execution_ids(athena, "demo", max_pages=5)
    with stubbed(athena) as stubber:
        expect_history(stubber, ["q-1", "q-2"], next_token="t1")
        capped = list_query_execution_ids(athena, "demo", max_pages=1)
    assert (whole.ids, whole.complete) == (("q-1", "q-2", "q-3"), True)
    assert (capped.ids, capped.complete) == (("q-1", "q-2"), False)


def test_fetched_history_keeps_only_the_first_line_of_sql_and_no_parameters() -> None:
    entry = _entry("q-1") | {
        "Query": '-- /* {"node_id": "model.demo.orders"} */\nselect * from secret_table',
        "ExecutionParameters": ["secret-parameter"],
        "ResultConfiguration": {"OutputLocation": "s3://secret-results/"},
    }
    athena = athena_client()
    with stubbed(athena) as stubber:
        expect_batch(stubber, [entry])
        (fetched,) = batch_get_query_executions(athena, ["q-1"])["QueryExecutions"]
    assert fetched["Query"] == '-- /* {"node_id": "model.demo.orders"} */'
    assert "secret" not in str(fetched)


def test_folders_are_listed_by_name_across_pages() -> None:
    s3 = s3_client()
    with stubbed(s3) as stubber:
        stubber.add_response(
            "list_objects_v2",
            {
                "IsTruncated": True,
                "NextContinuationToken": "t1",
                "CommonPrefixes": [{"Prefix": "m/billing_period=2026-11/"}],
            },
            {"Bucket": BUCKET, "Prefix": "m/", "Delimiter": "/"},
        )
        stubber.add_response(
            "list_objects_v2",
            {"IsTruncated": False, "CommonPrefixes": [{"Prefix": "m/billing_period=2026-10/"}]},
            {"Bucket": BUCKET, "Prefix": "m/", "Delimiter": "/", "ContinuationToken": "t1"},
        )
        folders = list_folders(s3, BUCKET, "m/")
    assert folders == ["billing_period=2026-10", "billing_period=2026-11"]


def test_a_download_is_written_whole_or_refused_at_its_cap(tmp_path: Path) -> None:
    s3 = s3_client()
    target = tmp_path / "file"
    with stubbed(s3) as stubber:
        expect_get(stubber, "d/a", b"0123456789")
        download_object(s3, BUCKET, S3Object("d/a", '"d/a"', 10), target, max_bytes=10)
    assert target.read_bytes() == b"0123456789"
    with stubbed(s3), pytest.raises(TooLarge):  # listed over the cap: never fetched
        download_object(s3, BUCKET, S3Object("d/a", '"d/a"', 11), target, max_bytes=10)
    with stubbed(s3) as stubber, pytest.raises(TooLarge):  # listed small, body larger
        expect_get(stubber, "d/a", b"x" * 11)
        download_object(s3, BUCKET, S3Object("d/a", '"d/a"', 3), target, max_bytes=10)
