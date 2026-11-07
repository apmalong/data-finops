"""The Collection Run against the Deployment's database object (spec 08 §Topology, ADR 0026;
seam 2): download it, collect, write it back only if nobody else has (S3 compare-and-swap).
"""

import io
from pathlib import Path
from typing import Any

import pytest
from aws_stubs import (
    ACCOUNT,
    BUCKET,
    EXPORT_BUCKET,
    EXPORT_NAME,
    EXPORT_PREFIX,
    METADATA,
    REGION,
    WORKGROUP,
    athena_client,
    expect_history,
    expect_listing,
    expect_prefixes,
    s3_client,
    stubbed,
)
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber
from fixture_database import fill

from data_finops.collection import AwsClients, CollectionSettings
from data_finops.collection.remote import DATABASE_KEY, run_remote_collection
from data_finops.providers.aws import WriteConflict

SETTINGS = CollectionSettings(
    bucket=BUCKET,
    region=REGION,
    account_id=ACCOUNT,
    workgroups=(WORKGROUP,),
    export_bucket=EXPORT_BUCKET,
    export_prefix=EXPORT_PREFIX,
    export_name=EXPORT_NAME,
)


def _expect_quiet_sources(s3_stub: Stubber, athena_stub: Stubber) -> None:
    """Every source empty: the Inboxes, the history and the export."""
    expect_listing(s3_stub, "inbox/dbt/", {})
    expect_listing(s3_stub, "inbox/openlineage/", {})
    expect_history(athena_stub, [])
    expect_prefixes(s3_stub, METADATA, [])


def _expect_database(s3_stub: Stubber, data: bytes, etag: str) -> None:
    s3_stub.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(data), len(data)), "ETag": etag},
        {"Bucket": BUCKET, "Key": DATABASE_KEY},
    )


def _expect_put(s3_stub: Stubber, condition: dict[str, str]) -> None:
    s3_stub.add_response(
        "put_object",
        {"ETag": '"written"'},
        {"Bucket": BUCKET, "Key": DATABASE_KEY, "Body": ANY} | condition,
    )


def _run(tmp_path: Path, s3: Any, athena: Any) -> Any:
    return run_remote_collection(
        SETTINGS, clients=AwsClients(s3=s3, athena=athena), writer=s3, scratch=tmp_path / "run"
    )


def test_the_first_run_starts_empty_and_creates_the_database_object(tmp_path: Path) -> None:
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena) as athena_stub:
        s3_stub.add_client_error(
            "get_object",
            service_error_code="NoSuchKey",
            http_status_code=404,
            expected_params={"Bucket": BUCKET, "Key": DATABASE_KEY},
        )
        _expect_quiet_sources(s3_stub, athena_stub)
        _expect_put(s3_stub, {"IfNoneMatch": "*"})
        summary = _run(tmp_path, s3, athena)
    assert summary.exit_code == 0


def test_a_later_run_writes_back_only_over_the_version_it_read(tmp_path: Path) -> None:
    held = tmp_path / "held.sqlite"
    run_id = fill(held)
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena) as athena_stub:
        _expect_database(s3_stub, held.read_bytes(), '"v7"')
        _expect_quiet_sources(s3_stub, athena_stub)
        _expect_put(s3_stub, {"IfMatch": '"v7"'})
        summary = _run(tmp_path, s3, athena)
    assert summary.calculation_run_ids == [run_id]  # what it read was used, then written back


def test_a_concurrent_write_is_a_conflict_and_nothing_is_overwritten(tmp_path: Path) -> None:
    held = tmp_path / "held.sqlite"
    fill(held)
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena) as athena_stub:
        _expect_database(s3_stub, held.read_bytes(), '"v7"')
        _expect_quiet_sources(s3_stub, athena_stub)
        s3_stub.add_client_error(
            "put_object",
            service_error_code="PreconditionFailed",
            http_status_code=412,
            expected_params={"Bucket": BUCKET, "Key": DATABASE_KEY, "Body": ANY, "IfMatch": '"v7"'},
        )
        with pytest.raises(WriteConflict):
            _run(tmp_path, s3, athena)
