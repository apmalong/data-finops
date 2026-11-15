"""The publisher of the Demo Snapshot (#227; spec 09 §The publisher, seam 3): on a database with a
demo line and an unrelated line, it writes only the demo's files; no 12-digit number but the
example; each file validates against its route's OpenAPI response schema; sensitive Cost
Dimensions are left out; and the swap is atomic.

The database is the hand-written fixture in another account, `210987654321`, so that masking
has something to do. Its Athena line is the workgroup `demo` ($6.00); its S3 line is not."""

import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aws_stubs import s3_client, stubbed
from botocore.response import StreamingBody
from botocore.stub import ANY
from fixture_database import EXPORT, FIXTURES, OTHER_EXPORT
from jsonschema import Draft202012Validator

from data_finops.api import create_app
from data_finops.calculation import run_calculation
from data_finops.contracts.billing import FocusChargeCategory, FocusExport
from data_finops.contracts.dimensions import CostDimensions
from data_finops.ledger import Ledger
from data_finops.providers.aws import AwsSourceError
from data_finops.public_demo.publisher import (
    CURRENT,
    EXAMPLE_ACCOUNT,
    PublishedFile,
    demo_lines,
    publish,
    render,
)
from data_finops.workload.store import WorkloadStore

OWNER = "210987654321"
ELSEWHERE = "345678901234"
"""Another account of the owner's, with no demo line."""
PUBLISHED = datetime(2026, 11, 5, 7, 0, tzinfo=UTC)
DEMO = demo_lines("demo")
DIMENSIONS = CostDimensions.from_mapping(
    {
        "dimensions": {
            "target": {"sources": ["query_comment.target_name"]},
            "profile": {"sources": ["query_comment.profile_name"], "sensitive": True},
        }
    }
)
BUCKET = "example-demo-bucket"


def _fill(path: Path) -> None:
    """The hand-written fixtures, as `fixture_database.fill` ingests them, in OWNER's account."""
    export = FocusExport.model_validate_json(
        EXPORT.model_dump_json().replace(EXAMPLE_ACCOUNT, OWNER)
    )
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        ledger.import_snapshot(export)
        for name in ("query-executions.json", "link-outcomes.json", "link-outcomes-restated.json"):
            moved = path.parent / name  # the query history names its account too
            text = (FIXTURES / "athena" / name).read_text(encoding="utf-8")
            moved.write_text(text.replace(EXAMPLE_ACCOUNT, OWNER), encoding="utf-8")
            store.ingest_query_history(
                moved,
                account_id=OWNER,
                region="us-east-1",
                dimensions=DIMENSIONS,
            )
        store.ingest_events(FIXTURES / "openlineage" / "events.json")
        # A DAG run that no published cost reaches: its lineage is not published.
        store.ingest_events(FIXTURES / "reference-workload" / "airflow" / "airflow-events.jsonl")
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "manifest.json")
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "run_results.json")
        run_calculation(ledger, store, export.snapshot.scope, dimensions=DIMENSIONS)
        # Another account's scope in the same period, S3 lines only: none of it is the demo's.
        other = FocusExport.model_validate_json(
            OTHER_EXPORT.model_dump_json().replace(OWNER, ELSEWHERE)
        )
        ledger.import_snapshot(other)
        run_calculation(ledger, store, other.snapshot.scope, dimensions=DIMENSIONS)


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("publisher") / "data-finops.sqlite"
    _fill(path)
    return path


@pytest.fixture(scope="module")
def files(database: Path) -> dict[str, str]:
    rendered = render(database, published_at=PUBLISHED, lines=DEMO)
    return {f.key: f.body.decode("utf-8") for f in rendered}


def test_only_the_demo_line_and_what_comes_from_it_is_written(files: dict[str, str]) -> None:
    for key, text in files.items():
        assert "demo-lake" not in text and "Simple Storage" not in text, key
    summary = json.loads(files["v1/costs/summary/BilledCost.json"])
    (usd,) = summary["summaries"][0]["currencies"]
    assert usd["billed"] == "6.00"
    pages = [
        json.loads(t)
        for k, t in files.items()
        if re.fullmatch(r"v1/allocations/[^/]+/page-\d+\.json", k)
    ]
    lines = {item["allocation"]["billing_line_ref"] for page in pages for item in page["items"]}
    assert lines == {f"aws-focus-{EXAMPLE_ACCOUNT}-2026-10-v1#0"}


UNREACHED = "01a19dd8-4a8c-72ec-873a-a3bb810cbae6"
"""The reference workload's Airflow DAG run."""


def test_only_the_runs_that_published_cost_reaches_are_published(files: dict[str, str]) -> None:
    roots = json.loads(files["v1/lineage/page-0.json"])
    assert roots["items"] and UNREACHED not in {run["run_id"] for run in roots["items"]}
    assert roots["total"] == len(roots["items"])
    assert all(UNREACHED not in text for text in files.values())


def test_the_demo_lines_are_its_workgroups_usage_only() -> None:
    usage = EXPORT.lines[0]  # Athena usage of the workgroup `demo`
    credit = usage.model_copy(update={"ChargeCategory": FocusChargeCategory.CREDIT})
    other = usage.model_copy(update={"ResourceId": usage.ResourceId.replace("/demo", "/etl")})

    assert DEMO(usage) and not DEMO(credit) and not DEMO(other)
    assert not DEMO(usage.model_copy(update={"ResourceId": None}))


def test_a_scope_with_no_demo_line_is_not_published(database: Path, files: dict[str, str]) -> None:
    from data_finops.reporting import Reporting

    with Reporting(database) as read_model:
        runs = {s.snapshot.calculation_run_id for s in read_model.cost_summary().summaries}
    summary = json.loads(files["v1/costs/summary/BilledCost.json"])
    (published,) = [s["snapshot"]["calculation_run_id"] for s in summary["summaries"]]
    (elsewhere,) = runs - {published}
    assert summary["uncalculated_scopes"] == []
    assert all(elsewhere not in key and elsewhere not in text for key, text in files.items())


def test_the_explained_share_leaves_out_what_is_unallocated(database: Path) -> None:
    # Both of the demo account's lines: $6.00 explained, $4.00 Unallocated (the S3 line).
    def owners(line: Any) -> bool:
        return OWNER in (line.SubAccountId or "")

    rendered = render(database, published_at=PUBLISHED, lines=owners)
    snapshot = json.loads(next(f.body for f in rendered if f.key == "snapshot.json"))
    (period,) = snapshot["periods"]
    assert (period["billed"], period["explained_share"]) == ("10.00", "0.6000")


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        (
            "arn:aws:athena:us-east-1:210987654321:workgroup/x",
            "arn:aws:athena:us-east-1:123456789012:workgroup/x",
        ),
        (
            "210987654321.dkr.ecr.us-east-1.amazonaws.com",
            "123456789012.dkr.ecr.us-east-1.amazonaws.com",
        ),
        ("aws-focus-210987654321-2026-10-v1#0", "aws-focus-123456789012-2026-10-v1#0"),
        ('"BillingAccountId":"210987654321"', '"BillingAccountId":"123456789012"'),
        ("3fa85f64-5717-4562-b3fc-210987654321", "3fa85f64-5717-4562-b3fc-210987654321"),
        ("a1b2c3210987654321d4e5f6", "a1b2c3210987654321d4e5f6"),
        ("0.210987654321", "0.210987654321"),
        ("2109876543210", "2109876543210"),
    ],
)
def test_masking_changes_account_ids_and_nothing_else(text: str, shown: str) -> None:
    from data_finops.public_demo.publisher import masked

    assert masked(text) == shown


def test_no_twelve_digit_number_but_the_example(files: dict[str, str]) -> None:
    for key, text in files.items():
        assert OWNER not in text, key
        for match in re.finditer(r"(?<!\d)\d{12}(?!\d)", text):
            in_uuid = re.fullmatch(r"[0-9a-f]{4}-", text[match.start() - 5 : match.start()])
            assert in_uuid or match.group() == EXAMPLE_ACCOUNT, key


def test_sensitive_cost_dimensions_are_left_out(files: dict[str, str]) -> None:
    dimension_files = [k for k in files if k.startswith("v1/rollups/dimensions/")]
    assert dimension_files and all(
        k.startswith("v1/rollups/dimensions/target/") for k in dimension_files
    )
    for key, text in files.items():
        if key.startswith("v1/allocations/"):
            assert '"profile"' not in text and '"target"' in text, key


def _schemas(database: Path) -> dict[str, Draft202012Validator]:
    """Each read route's 200 response schema, with the document's components beside it so
    that its `#/components/...` references resolve."""
    document = create_app(database, read_only=True).openapi()
    by_route = {}
    for route, methods in document["paths"].items():
        response = methods["get"]["responses"].get("200", {})
        schema = response.get("content", {}).get("application/json", {}).get("schema")
        if schema is not None:
            by_route[route] = Draft202012Validator({**schema, "components": document["components"]})
    return by_route


ROUTES = [
    (r"v1/costs/summary/[A-Za-z]+\.json", "/v1/costs/summary"),
    (r"v1/allocations/[^/]+/page-\d+\.json", "/v1/allocations"),
    (r"v1/allocations/[^/]+/\d+/explanation\.json", "/v1/allocations/{ref}/explanation"),
    (r"v1/rollups/(model_execution|workload_run)/[^/]+/page-\d+\.json", "/v1/rollups"),
    (r"v1/rollups/dimensions/[^/]+/[^/]+/page-\d+\.json", "/v1/rollups/dimensions/{name}"),
    (r"v1/lineage/page-\d+\.json", "/v1/lineage"),
    (r"v1/lineage/runs/[^/]+\.json", "/v1/lineage"),
]


def test_each_file_validates_against_its_routes_response_schema(
    database: Path, files: dict[str, str]
) -> None:
    validators = _schemas(database)
    checked = set()
    for key, text in files.items():
        if key == "snapshot.json":
            continue
        route = next(r for pattern, r in ROUTES if re.fullmatch(pattern, key))
        errors = list(validators[route].iter_errors(json.loads(text)))
        assert not errors, (key, errors[0].message)
        checked.add(route)
    assert checked == {route for _, route in ROUTES}


def test_the_snapshot_file_says_when_and_how_much_is_explained(files: dict[str, str]) -> None:
    snapshot = json.loads(files["snapshot.json"])
    assert snapshot["published_at"] == "2026-11-05T07:00:00Z"
    assert snapshot["before_credits"] is True
    (period,) = snapshot["periods"]
    assert (period["billing_period"], period["billed"], period["explained_share"]) == (
        "2026-10-01",
        "6.00",
        "1.0000",
    )


def _captured(s3: Any) -> list[str]:
    keys: list[str] = []
    s3.meta.events.register(
        "provide-client-params.s3.PutObject", lambda params, **_: keys.append(params["Key"])
    )
    return keys


def _expect_files(stub: Any, count: int) -> None:
    for _ in range(count):
        stub.add_response(
            "put_object",
            {},
            {"Bucket": BUCKET, "Key": ANY, "Body": ANY, "ContentType": "application/json"},
        )


def _expect_current(stub: Any, published_at: str | None, etag: str = '"e1"') -> None:
    """One read of `current.json`: none yet, or one published at `published_at`."""
    if published_at is None:
        stub.add_client_error(
            "get_object", "NoSuchKey", expected_params={"Bucket": BUCKET, "Key": CURRENT}
        )
        return
    body = json.dumps({"prefix": "snapshots/old/", "published_at": published_at}).encode()
    stub.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(body), len(body)), "ETag": etag},
        {"Bucket": BUCKET, "Key": CURRENT},
    )


def _expect_move(stub: Any, condition: dict[str, str]) -> None:
    stub.add_response(
        "put_object",
        {},
        {"Bucket": BUCKET, "Key": CURRENT, "Body": ANY, "ContentType": "application/json"}
        | condition,
    )


def test_current_json_moves_only_after_every_file_is_written(database: Path) -> None:
    count = len(render(database, published_at=PUBLISHED, lines=DEMO))
    s3 = s3_client()
    keys = _captured(s3)
    with stubbed(s3) as stub:
        _expect_files(stub, count)
        _expect_current(stub, None)
        _expect_move(stub, {"IfNoneMatch": "*"})  # the first snapshot: only if there is none
        pointer = publish(database, s3=s3, bucket=BUCKET, clock=lambda: PUBLISHED, lines=DEMO)

    assert pointer["moved"] is True
    assert keys[-1] == CURRENT and CURRENT not in keys[:-1]
    assert re.fullmatch(r"snapshots/20261105T070000Z-[0-9a-f]{8}/", pointer["prefix"])
    assert all(key.startswith(pointer["prefix"]) for key in keys[:-1])
    assert len(keys) - 1 == pointer["files"] == count


def test_current_json_moves_from_an_older_snapshot_only_if_it_is_unchanged(
    database: Path,
) -> None:
    count = len(render(database, published_at=PUBLISHED, lines=DEMO))
    s3 = s3_client()
    with stubbed(s3) as stub:
        _expect_files(stub, count)
        _expect_current(stub, "2026-11-04T07:00:00Z")
        _expect_move(stub, {"IfMatch": '"e1"'})
        pointer = publish(database, s3=s3, bucket=BUCKET, clock=lambda: PUBLISHED, lines=DEMO)

    assert pointer["moved"] is True


def test_current_json_never_moves_back_to_an_older_snapshot(database: Path) -> None:
    # Another publisher, started later, moved current.json while this one wrote its files.
    count = len(render(database, published_at=PUBLISHED, lines=DEMO))
    s3 = s3_client()
    keys = _captured(s3)
    with stubbed(s3) as stub:
        _expect_files(stub, count)
        _expect_current(stub, "2026-11-04T07:00:00Z")
        stub.add_client_error("put_object", "PreconditionFailed", http_status_code=412)
        _expect_current(stub, "2026-11-05T08:00:00Z", etag='"e2"')
        pointer = publish(database, s3=s3, bucket=BUCKET, clock=lambda: PUBLISHED, lines=DEMO)

    assert pointer["moved"] is False
    assert keys.count(CURRENT) == 1  # the one refused write


def test_a_failed_write_leaves_current_json_as_it_was(database: Path) -> None:
    s3 = s3_client()
    keys = _captured(s3)
    with stubbed(s3) as stub, pytest.raises(AwsSourceError):
        stub.add_response("put_object", {})
        stub.add_client_error("put_object", "SlowDown")
        publish(database, s3=s3, bucket=BUCKET, clock=lambda: PUBLISHED, lines=DEMO)

    assert CURRENT not in keys


def test_rendering_the_same_database_twice_gives_the_same_files(
    database: Path, files: dict[str, str]
) -> None:
    again: list[PublishedFile] = render(database, published_at=PUBLISHED, lines=DEMO)
    assert {f.key: f.body.decode("utf-8") for f in again} == files


def test_the_handler_publishes_the_database_object_of_the_deployment(
    database: Path, tmp_path: Path
) -> None:
    from data_finops.collection.remote import DATABASE_KEY
    from data_finops.lambda_handlers import DEMO_BUCKET, DEPLOYMENT_BUCKET, make_publisher_handler

    data = database.read_bytes()
    count = len(render(database, published_at=PUBLISHED, lines=DEMO))
    s3 = s3_client()
    keys = _captured(s3)
    with stubbed(s3) as stub:
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(data), len(data)), "ETag": '"db"'},
            {"Bucket": "example-deployment-bucket", "Key": DATABASE_KEY},
        )
        for _ in range(count):
            stub.add_response("put_object", {})
        _expect_current(stub, None)
        stub.add_response("put_object", {})
        handler = make_publisher_handler(
            lambda: s3,
            {DEPLOYMENT_BUCKET: "example-deployment-bucket", DEMO_BUCKET: BUCKET},
            clock=lambda: PUBLISHED,
            scratch=tmp_path,
            lines=DEMO,
        )
        result = handler({"Records": []}, None)

    assert result["prefix"].startswith("snapshots/20261105T070000Z-") and keys[-1] == CURRENT


def test_with_no_database_object_yet_nothing_is_published(tmp_path: Path) -> None:
    from data_finops.lambda_handlers import DEMO_BUCKET, DEPLOYMENT_BUCKET, make_publisher_handler

    s3 = s3_client()
    keys = _captured(s3)
    with stubbed(s3) as stub:
        stub.add_client_error("get_object", "NoSuchKey")
        handler = make_publisher_handler(
            lambda: s3,
            {DEPLOYMENT_BUCKET: "example-deployment-bucket", DEMO_BUCKET: BUCKET},
            clock=lambda: PUBLISHED,
            scratch=tmp_path,
        )
        assert handler({}, None) == {"published": False}
    assert keys == []


@pytest.mark.parametrize("missing", ["DATA_FINOPS_BUCKET", "DATA_FINOPS_DEMO_BUCKET"])
def test_a_missing_bucket_variable_is_named(missing: str, tmp_path: Path) -> None:
    from data_finops.lambda_handlers import PublisherMisconfigured, make_publisher_handler

    environ = {"DATA_FINOPS_BUCKET": "b", "DATA_FINOPS_DEMO_BUCKET": "d"}
    del environ[missing]
    handler = make_publisher_handler(s3_client, environ, clock=lambda: PUBLISHED, scratch=tmp_path)
    with pytest.raises(PublisherMisconfigured, match=missing):
        handler({}, None)


def test_a_pointer_that_keeps_changing_stops_the_publisher_after_five_tries(
    database: Path,
) -> None:
    from data_finops.public_demo.publisher import MAX_MOVES, PointerContended

    count = len(render(database, published_at=PUBLISHED, lines=DEMO))
    s3 = s3_client()
    with stubbed(s3) as stub, pytest.raises(PointerContended):
        _expect_files(stub, count)
        for n in range(MAX_MOVES):
            _expect_current(stub, "2026-11-04T07:00:00Z", etag=f'"e{n}"')
            stub.add_client_error("put_object", "PreconditionFailed", http_status_code=412)
        publish(database, s3=s3, bucket=BUCKET, clock=lambda: PUBLISHED, lines=DEMO)

    assert MAX_MOVES == 5
