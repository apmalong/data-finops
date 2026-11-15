"""The dataset generator's Lambda handler (#225; spec 09 §The demo dataset): one invocation
writes each file of the dataset under `dataset/` in the demo bucket. AWS calls go to a stubbed
client only."""

from typing import Any

import pytest
from aws_stubs import s3_client, stubbed

from data_finops.lambda_handlers import DEMO_BUCKET, GeneratorMisconfigured, make_generator_handler
from data_finops.providers.aws import AwsSourceError
from data_finops.public_demo.dataset import generate

DEMO = "example-demo-bucket"
SMALL = {"customers": 20, "orders": 30, "events": 40}


def _small() -> Any:
    return generate(seed=3, rows=SMALL, part_rows=25)


def test_each_file_is_written_under_the_dataset_prefix() -> None:
    files = list(_small())
    s3 = s3_client()
    with stubbed(s3) as stub:
        for file in files:
            stub.add_response(
                "put_object",
                {"ETag": '"e"'},
                {
                    "Bucket": DEMO,
                    "Key": f"dataset/{file.key}",
                    "Body": file.data,
                    "ContentType": "application/vnd.apache.parquet",
                },
            )
        result = make_generator_handler(lambda: s3, {DEMO_BUCKET: DEMO}, _small)({}, None)

    assert [f.key for f in files] == [
        "customers/part-00000.parquet",
        "orders/part-00000.parquet",
        "orders/part-00001.parquet",
        "events/part-00000.parquet",
        "events/part-00001.parquet",
    ]
    assert result == {"files": 5, "bytes": sum(len(f.data) for f in files)}


def test_a_missing_bucket_is_named() -> None:
    with pytest.raises(GeneratorMisconfigured, match=DEMO_BUCKET):
        make_generator_handler(s3_client, {}, _small)({}, None)


def test_a_refused_write_fails_the_invocation_with_the_code_alone() -> None:
    s3 = s3_client()
    with stubbed(s3) as stub, pytest.raises(AwsSourceError, match="^AccessDenied$"):
        stub.add_client_error("put_object", "AccessDenied", "bucket example-demo-bucket")
        make_generator_handler(lambda: s3, {DEMO_BUCKET: DEMO}, _small)({}, None)
