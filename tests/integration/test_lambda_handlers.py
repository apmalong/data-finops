"""The Lambda handlers through `handler(event, context)` with stubbed clients (spec 08 seam 3):
wiring and exit semantics only; what a Collection Run does is tested at seam 2."""

import io
import json
from pathlib import Path
from typing import Any

import pytest
from aws_stubs import (
    BUCKET,
    DEPLOYMENT,
    METADATA,
    athena_client,
    expect_database_put,
    expect_denied,
    expect_get,
    expect_history,
    expect_listing,
    expect_prefixes,
    expect_quiet_sources,
    s3_client,
    stubbed,
)
from botocore.response import StreamingBody
from botocore.stub import ANY
from fixture_database import fill

from data_finops.collection import AwsClients
from data_finops.collection.remote import DATABASE_KEY
from data_finops.lambda_handlers import (
    COLLECTOR_SETTINGS,
    ApiMisconfigured,
    CollectionFailed,
    make_api_handler,
    make_collector_handler,
)

SETTINGS_JSON = json.dumps(
    {
        "bucket": DEPLOYMENT.bucket,
        "region": DEPLOYMENT.region,
        "account_id": DEPLOYMENT.account_id,
        "workgroups": list(DEPLOYMENT.workgroups),
        "export_bucket": DEPLOYMENT.export_bucket,
        "export_prefix": DEPLOYMENT.export_prefix,
        "export_name": DEPLOYMENT.export_name,
    }
)
ENVIRON = {"DATA_FINOPS_COLLECTOR_SETTINGS": SETTINGS_JSON}


def _no_database(s3_stub: Any) -> None:
    s3_stub.add_client_error(
        "get_object",
        service_error_code="NoSuchKey",
        http_status_code=404,
        expected_params={"Bucket": BUCKET, "Key": DATABASE_KEY},
    )


def test_a_successful_collection_returns_and_logs_its_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    s3, athena = s3_client(), athena_client()
    made: list[Any] = []

    def clients(settings: Any) -> AwsClients:
        made.append(settings)
        return AwsClients(s3=s3, athena=athena)

    with stubbed(s3) as s3_stub, stubbed(athena) as athena_stub:
        _no_database(s3_stub)
        expect_quiet_sources(s3_stub, athena_stub)
        expect_database_put(s3_stub, {"IfNoneMatch": "*"})
        result = make_collector_handler(clients, ENVIRON)({}, None)

    assert result["exit_code"] == 0
    assert made == [DEPLOYMENT]
    assert json.loads(capsys.readouterr().out.splitlines()[0]) == result


def _clients(s3: Any, athena: Any) -> Any:
    return lambda settings: AwsClients(s3=s3, athena=athena)


def test_a_source_that_cannot_be_read_fails_the_invocation_after_writing_back() -> None:
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena) as athena_stub:
        _no_database(s3_stub)
        expect_listing(s3_stub, "inbox/dbt/", {})
        expect_listing(s3_stub, "inbox/openlineage/", {})
        expect_denied(athena_stub, "list_query_executions")
        expect_prefixes(s3_stub, METADATA, [])
        expect_database_put(s3_stub, {"IfNoneMatch": "*"})  # what was stored is kept
        with pytest.raises(CollectionFailed, match="exit code 2"):
            make_collector_handler(_clients(s3, athena), ENVIRON)({}, None)


def test_a_write_conflict_fails_the_invocation() -> None:
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena) as athena_stub:
        _no_database(s3_stub)
        expect_quiet_sources(s3_stub, athena_stub)
        s3_stub.add_client_error(
            "put_object",
            service_error_code="PreconditionFailed",
            http_status_code=412,
            expected_params={
                "Bucket": BUCKET,
                "Key": DATABASE_KEY,
                "Body": ANY,
                "IfNoneMatch": "*",
            },
        )
        with pytest.raises(CollectionFailed, match="changed by another writer"):
            make_collector_handler(_clients(s3, athena), ENVIRON)({}, None)


def test_rejected_items_return_normally_so_they_are_not_retried() -> None:
    s3, athena = s3_client(), athena_client()
    key = "inbox/openlineage/bad.json"
    with stubbed(s3) as s3_stub, stubbed(athena) as athena_stub:
        _no_database(s3_stub)
        expect_listing(s3_stub, "inbox/dbt/", {})
        expect_listing(s3_stub, "inbox/openlineage/", {key: b"{}"})
        expect_get(s3_stub, key, b"{}")
        expect_history(athena_stub, [])
        expect_prefixes(s3_stub, METADATA, [])
        expect_database_put(s3_stub, {"IfNoneMatch": "*"})
        result = make_collector_handler(_clients(s3, athena), ENVIRON)({}, None)
    assert result["exit_code"] == 1


@pytest.mark.parametrize(
    ("environ", "names"),
    [
        ({}, COLLECTOR_SETTINGS),
        ({COLLECTOR_SETTINGS: "{not json"}, COLLECTOR_SETTINGS),
        ({COLLECTOR_SETTINGS: json.dumps({"bucket": "secret-bucket"})}, "region"),
    ],
)
def test_bad_settings_fail_the_invocation_naming_the_setting_alone(
    environ: dict[str, str], names: str
) -> None:
    with pytest.raises(CollectionFailed) as raised:
        make_collector_handler(lambda settings: None, environ)({}, None)  # type: ignore[arg-type,return-value]
    assert names in str(raised.value)
    assert "secret" not in str(raised.value)


def _url_event(path: str, query: str = "") -> dict[str, Any]:
    """A Lambda Function URL request (payload format 2.0) for GET `path`."""
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": query,
        "headers": {"host": "example.lambda-url.us-east-1.on.aws", "accept": "application/json"},
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "example",
            "domainName": "example.lambda-url.us-east-1.on.aws",
            "domainPrefix": "example",
            "http": {
                "method": "GET",
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "192.0.2.1",
                "userAgent": "test",
            },
            "requestId": "request-1",
            "routeKey": "$default",
            "stage": "$default",
            "time": "07/Nov/2026:12:00:00 +0000",
            "timeEpoch": 0,
        },
        "isBase64Encoded": False,
    }


def _held_database(tmp_path: Path) -> bytes:
    held = tmp_path / "held.sqlite"
    fill(held)
    return held.read_bytes()


def _expect_current(s3_stub: Any, data: bytes, etag: str, cached: str | None = None) -> None:
    params = {"Bucket": BUCKET, "Key": DATABASE_KEY} | ({"IfNoneMatch": cached} if cached else {})
    s3_stub.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(data), len(data)), "ETag": etag},
        params,
    )


def _expect_unchanged(s3_stub: Any, cached: str) -> None:
    s3_stub.add_client_error(
        "get_object",
        service_error_code="304",
        http_status_code=304,
        expected_params={"Bucket": BUCKET, "Key": DATABASE_KEY, "IfNoneMatch": cached},
    )


API_ENVIRON = {"DATA_FINOPS_BUCKET": BUCKET}


def test_the_api_serves_reads_from_the_database_object_fetched_once(tmp_path: Path) -> None:
    s3 = s3_client()
    handler = make_api_handler(lambda: s3, API_ENVIRON, tmp_path / "cache")
    with stubbed(s3) as s3_stub:
        _expect_current(s3_stub, _held_database(tmp_path), '"v1"')
        _expect_unchanged(s3_stub, '"v1"')  # the second request downloads nothing
        health = handler(_url_event("/health"), None)
        summary = handler(_url_event("/v1/costs/summary"), None)
    assert (health["statusCode"], summary["statusCode"]) == (200, 200)
    assert json.loads(summary["body"])["summaries"]


def test_a_changed_database_object_replaces_the_cached_copy(tmp_path: Path) -> None:
    s3 = s3_client()
    data = _held_database(tmp_path)
    handler = make_api_handler(lambda: s3, API_ENVIRON, tmp_path / "cache")
    with stubbed(s3) as s3_stub:
        _expect_current(s3_stub, data, '"v1"')
        _expect_current(s3_stub, data, '"v2"', cached='"v1"')
        _expect_unchanged(s3_stub, '"v2"')
        for _ in range(3):
            assert handler(_url_event("/health"), None)["statusCode"] == 200


@pytest.mark.parametrize("path", ["/dashboard/", "/"])
def test_the_api_mounts_no_dashboard(tmp_path: Path, path: str) -> None:
    s3 = s3_client()
    handler = make_api_handler(lambda: s3, API_ENVIRON, tmp_path / "cache")
    with stubbed(s3) as s3_stub:
        _expect_current(s3_stub, _held_database(tmp_path), '"v1"')
        assert handler(_url_event(path), None)["statusCode"] == 404


def test_the_api_shows_a_minimal_billing_line_unless_told_otherwise(tmp_path: Path) -> None:
    data = _held_database(tmp_path)
    details = []
    for environ in (API_ENVIRON, API_ENVIRON | {"DATA_FINOPS_EXPLANATION_DETAIL": "full"}):
        s3 = s3_client()
        handler = make_api_handler(lambda s3=s3: s3, environ, tmp_path / f"cache-{len(details)}")
        with stubbed(s3) as s3_stub:
            _expect_current(s3_stub, data, '"v1"')
            _expect_unchanged(s3_stub, '"v1"')
            page = json.loads(handler(_url_event("/v1/allocations"), None)["body"])
            ref = page["items"][0]["ref"].replace("#", "%23")
            explained = handler(_url_event(f"/v1/allocations/{ref}/explanation"), None)
        details.append(json.loads(explained["body"])["billing_line_detail"])
    assert details == ["minimal", "full"]


def test_before_the_first_collection_the_api_answers_unavailable(tmp_path: Path) -> None:
    s3 = s3_client()
    handler = make_api_handler(lambda: s3, API_ENVIRON, tmp_path / "cache")
    with stubbed(s3) as s3_stub:
        s3_stub.add_client_error(
            "get_object",
            service_error_code="NoSuchKey",
            http_status_code=404,
            expected_params={"Bucket": BUCKET, "Key": DATABASE_KEY},
        )
        health = handler(_url_event("/health"), None)
    assert health["statusCode"] == 503


def test_the_api_has_no_receiver(tmp_path: Path) -> None:
    s3 = s3_client()
    handler = make_api_handler(lambda: s3, API_ENVIRON, tmp_path / "cache")
    event = _url_event("/api/v1/lineage")
    event["requestContext"]["http"]["method"] = "POST"
    event["body"] = "{}"
    with stubbed(s3) as s3_stub:
        _expect_current(s3_stub, _held_database(tmp_path), '"v1"')
        assert handler(event, None)["statusCode"] == 404


@pytest.mark.parametrize(
    ("environ", "names"),
    [
        ({}, "DATA_FINOPS_BUCKET"),
        (API_ENVIRON | {"DATA_FINOPS_EXPLANATION_DETAIL": "all"}, "DATA_FINOPS_EXPLANATION_DETAIL"),
    ],
)
def test_a_misconfigured_api_names_the_variable(
    tmp_path: Path, environ: dict[str, str], names: str
) -> None:
    with pytest.raises(ApiMisconfigured, match=names):
        make_api_handler(s3_client, environ, tmp_path / "cache")
