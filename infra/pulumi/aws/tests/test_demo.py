"""The public demo's first resources through Pulumi's mocks (#225; spec 09 §Stack, seam 5): the
demo bucket, the `data-finops-demo` workgroup, the Glue tables of the demo dataset and the
dataset generator. They exist only when `publicDemo` is on."""

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pulumi
import pytest
from deployment.demo import DATABASE, DATASET_PREFIX, RESULTS_PREFIX, TABLES, WORKGROUP
from deployment.program import DEMO_OUTPUTS, OUTPUTS, Deployment, build
from deployment.settings import Settings

from tests.mocks import MOCKS

DEMO_BUCKET = "mock-data-finops-demo-bucket"
WORKGROUP_TYPE = "aws:athena/workgroup:Workgroup"
TABLE_TYPE = "aws:glue/catalogTable:CatalogTable"
DATABASE_TYPE = "aws:glue/catalogDatabase:CatalogDatabase"
FUNCTION = "aws:lambda/function:Function"
BUCKET = "aws:s3/bucket:Bucket"


@pytest.fixture(autouse=True)
def _fresh_mocks() -> None:
    MOCKS.resources.clear()


def _settings(**changes: Any) -> Settings:
    off = Settings(
        region="us-east-1",
        export_bucket="example-export-bucket",
        export_name="example-focus-1-2",
        workgroups=("demo",),
    )
    return dataclasses.replace(off, **changes)


def _after(
    tmp_path: Path, check: Callable[[Deployment], None], **changes: Any
) -> pulumi.Output[None]:
    package = tmp_path / "data-finops-lambda.zip"
    package.write_bytes(b"PK\x05\x06" + b"\x00" * 18)  # an empty zip
    deployment = build(_settings(**changes), package=package)
    return deployment.ready.apply(lambda _: check(deployment))


@pulumi.runtime.test
def test_without_public_demo_there_is_no_demo_resource(tmp_path: Path) -> pulumi.Output[None]:
    def check(deployment: Deployment) -> None:
        assert deployment.demo is None
        types = {r.typ for r in MOCKS.resources}
        assert not types & {WORKGROUP_TYPE, TABLE_TYPE, DATABASE_TYPE}
        assert len(MOCKS.of_type(BUCKET)) == 1
        assert not [f for f in MOCKS.of_type(FUNCTION) if f.name.endswith("generator")]
        assert list(deployment.outputs()) == [k for k in OUTPUTS if k not in DEMO_OUTPUTS]

    return _after(tmp_path, check)


@pulumi.runtime.test
def test_the_demo_workgroup_enforces_the_scan_cutoff(tmp_path: Path) -> pulumi.Output[None]:
    def check(_: Deployment) -> None:
        workgroup = MOCKS.one(WORKGROUP_TYPE).inputs
        assert workgroup["name"] == WORKGROUP == "data-finops-demo"
        configuration = workgroup["configuration"]
        assert configuration["bytesScannedCutoffPerQuery"] == 50_000_000
        assert configuration["enforceWorkgroupConfiguration"] is True
        results = configuration["resultConfiguration"]
        assert results["outputLocation"] == f"s3://{DEMO_BUCKET}/{RESULTS_PREFIX}"
        assert results["encryptionConfiguration"] == {"encryptionOption": "SSE_S3"}

    return _after(tmp_path, check, public_demo=True, demo_scan_cutoff_bytes=50_000_000)


@pulumi.runtime.test
def test_the_glue_tables_read_the_dataset_as_parquet(tmp_path: Path) -> pulumi.Output[None]:
    def check(_: Deployment) -> None:
        assert MOCKS.one(DATABASE_TYPE).inputs["name"] == DATABASE == "data_finops_demo"
        tables = {t.inputs["name"]: t.inputs for t in MOCKS.of_type(TABLE_TYPE)}
        assert set(tables) == set(TABLES) == {"customers", "orders", "events"}
        for name, table in tables.items():
            assert table["databaseName"] == DATABASE
            assert table["tableType"] == "EXTERNAL_TABLE"
            storage = table["storageDescriptor"]
            assert storage["location"] == f"s3://{DEMO_BUCKET}/{DATASET_PREFIX}{name}/"
            assert storage["inputFormat"].endswith("MapredParquetInputFormat")
            assert storage["serDeInfo"]["serializationLibrary"].endswith("ParquetHiveSerDe")
            assert [(c["name"], c["type"]) for c in storage["columns"]] == list(TABLES[name])

    return _after(tmp_path, check, public_demo=True)


@pulumi.runtime.test
def test_the_generator_may_only_write_the_dataset(tmp_path: Path) -> pulumi.Output[None]:
    def check(deployment: Deployment) -> None:
        (function,) = [f.inputs for f in MOCKS.of_type(FUNCTION) if f.name.endswith("generator")]
        assert function["handler"] == "data_finops.lambda_handlers.generator"
        assert function["environment"]["variables"] == {"DATA_FINOPS_DEMO_BUCKET": DEMO_BUCKET}
        assert (function["timeout"], function["memorySize"]) == (900, 3008)
        (policy,) = [
            json.loads(p.inputs["policy"])
            for p in MOCKS.of_type("aws:iam/rolePolicy:RolePolicy")
            if p.name.endswith("-generator")
        ]
        allowed = {
            action: statement["Resource"]
            for statement in policy["Statement"]
            for action in (
                [statement["Action"]]
                if isinstance(statement["Action"], str)
                else statement["Action"]
            )
        }
        assert allowed.pop("s3:PutObject") == [f"arn:aws:s3:::{DEMO_BUCKET}/{DATASET_PREFIX}*"]
        assert set(allowed) == {"logs:CreateLogStream", "logs:PutLogEvents"}
        assert list(deployment.outputs()) == list(OUTPUTS)

    return _after(tmp_path, check, public_demo=True)


@pulumi.runtime.test
def test_the_demo_bucket_is_private_and_removed_with_the_demo(
    tmp_path: Path,
) -> pulumi.Output[None]:
    def check(_: Deployment) -> None:
        (demo,) = [b for b in MOCKS.of_type(BUCKET) if b.name == "data-finops-demo"]
        assert demo.inputs["forceDestroy"] is True
        blocks = [
            r.inputs
            for r in MOCKS.of_type("aws:s3/bucketPublicAccessBlock:BucketPublicAccessBlock")
            if r.name == "data-finops-demo"
        ]
        assert blocks == [
            {
                "bucket": "data-finops-demo-id",
                "blockPublicAcls": True,
                "blockPublicPolicy": True,
                "ignorePublicAcls": True,
                "restrictPublicBuckets": True,
            }
        ]
        (encryption,) = [
            r.inputs["rules"]
            for r in MOCKS.of_type(
                "aws:s3/bucketServerSideEncryptionConfiguration:"
                "BucketServerSideEncryptionConfiguration"
            )
            if r.name == "data-finops-demo"
        ]
        assert encryption == [{"applyServerSideEncryptionByDefault": {"sseAlgorithm": "AES256"}}]

    kms = "arn:aws:kms:us-east-1:123456789012:key/example"
    return _after(tmp_path, check, public_demo=True, kms_key_arn=kms)
