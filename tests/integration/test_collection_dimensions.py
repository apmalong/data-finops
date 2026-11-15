"""Cost Dimensions in a Deployment (#224; spec 09 §Cost Dimensions): the collector reads the
configuration from its bucket, at the key its settings name, and applies it in the Collection
Run. AWS calls go to stubbed clients only."""

import dataclasses
import io
from pathlib import Path
from typing import Any

import pytest
from aws_stubs import (
    ACCOUNT,
    BUCKET,
    METADATA,
    REGION,
    athena_client,
    expect_batch,
    expect_history,
    expect_listing,
    expect_prefixes,
    s3_client,
    stubbed,
)
from botocore.response import StreamingBody
from botocore.stub import Stubber
from test_collection_run import SETTINGS

from data_finops.collection import AwsClients, run_collection
from data_finops.collection.run import DimensionsUnavailable
from data_finops.contracts.dimensions import CostDimensions
from data_finops.workload.store import WorkloadStore

KEY = "config/cost-dimensions.toml"
WITH_KEY = dataclasses.replace(SETTINGS, cost_dimensions_key=KEY)
TEAM = b'[dimensions.team]\nsources = ["query_comment.team"]\n'
ENTRY = {
    "QueryExecutionId": "q-team",
    "Query": '/* {"team": "finance"} */\nselect 1',
    "StatementType": "DML",
    "WorkGroup": "demo",
    "Status": {
        "State": "SUCCEEDED",
        "SubmissionDateTime": "2026-10-01T01:00:00Z",
        "CompletionDateTime": "2026-10-01T01:00:10Z",
    },
    "Statistics": {"DataScannedInBytes": 1},
}


def _expect_config(stubber: Stubber, data: bytes) -> None:
    stubber.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(data), len(data)), "ContentLength": len(data)},
        {"Bucket": BUCKET, "Key": KEY},
    )


def test_the_configuration_in_the_bucket_applies_in_the_run(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena) as athena_stub:
        _expect_config(s3_stub, TEAM)
        expect_listing(s3_stub, "inbox/dbt/", {})
        expect_listing(s3_stub, "inbox/openlineage/", {})
        expect_history(athena_stub, ["q-team"])
        expect_batch(athena_stub, [ENTRY])
        expect_prefixes(s3_stub, METADATA, [])
        summary = run_collection(db, WITH_KEY, clients=AwsClients(s3=s3, athena=athena))

    assert summary.exit_code == 0
    with WorkloadStore(db) as store:
        query = store.query_execution(ACCOUNT, REGION, "q-team")
    assert query is not None and dict(query.claims) == {"team": "finance"}


@pytest.mark.parametrize(
    ("configure", "fault"),
    [
        (
            lambda stub: _expect_config(stub, b'[dimensions.team]\nsources = ["secret.x"]\n'),
            "source 1",
        ),
        (lambda stub: _expect_config(stub, b"\xff not toml"), "not valid TOML"),
        (
            lambda stub: stub.add_client_error(
                "get_object", "NoSuchKey", expected_params={"Bucket": BUCKET, "Key": KEY}
            ),
            "NoSuchKey",
        ),
    ],
)
def test_a_configuration_that_cannot_be_read_stops_the_run_before_it_collects(
    tmp_path: Path, configure: Any, fault: str
) -> None:
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena):  # no other AWS call is expected
        configure(s3_stub)
        with pytest.raises(DimensionsUnavailable) as error:
            run_collection(
                tmp_path / "data-finops.sqlite", WITH_KEY, clients=AwsClients(s3=s3, athena=athena)
            )

    assert fault in str(error.value) and KEY in str(error.value)
    assert "secret" not in str(error.value)


def test_a_file_and_a_bucket_key_together_are_refused(tmp_path: Path) -> None:
    file_config = CostDimensions.from_mapping(
        {"dimensions": {"team": {"sources": ["query_comment.team"]}}}
    )
    s3, athena = s3_client(), athena_client()
    with stubbed(s3), stubbed(athena), pytest.raises(DimensionsUnavailable, match="not both"):
        run_collection(
            tmp_path / "data-finops.sqlite",
            WITH_KEY,
            clients=AwsClients(s3=s3, athena=athena),
            dimensions=file_config,
        )


def test_the_key_is_a_setting_of_the_collector() -> None:
    from data_finops.collection import CollectionSettings

    given = {k: v for k, v in dataclasses.asdict(SETTINGS).items() if v is not None}
    given["workgroups"] = list(SETTINGS.workgroups)

    settings = CollectionSettings.from_mapping(given | {"cost_dimensions_key": KEY})
    assert settings.cost_dimensions_key == KEY
    with pytest.raises(ValueError, match="cost_dimensions_key"):
        CollectionSettings.from_mapping(given | {"cost_dimensions_key": 7})
