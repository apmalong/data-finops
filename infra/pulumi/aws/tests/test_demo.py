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
DEPLOYMENT_BUCKET = "mock-deployment-bucket"
ACCOUNT = "123456789012"
ANY_LOG_GROUP = "arn:mock:aws:cloudwatch/logGroup:LogGroup:data-finops-simulator:*"
SCHEDULE = "aws:scheduler/schedule:Schedule"
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


def _role_policy(suffix: str) -> dict[str, list[str]]:
    """Each action a role policy allows, with its resources."""
    (policy,) = [
        json.loads(p.inputs["policy"])
        for p in MOCKS.of_type("aws:iam/rolePolicy:RolePolicy")
        if p.name.endswith(suffix)
    ]
    allowed: dict[str, list[str]] = {}
    for statement in policy["Statement"]:
        actions = statement["Action"]
        for action in [actions] if isinstance(actions, str) else actions:
            allowed.setdefault(action, []).extend(statement["Resource"])
    return allowed


@pulumi.runtime.test
def test_the_simulator_may_only_query_the_demo_workgroup(tmp_path: Path) -> pulumi.Output[None]:
    def check(deployment: Deployment) -> None:
        (function,) = [f.inputs for f in MOCKS.of_type(FUNCTION) if f.name.endswith("simulator")]
        assert function["handler"] == "data_finops.lambda_handlers.simulator"
        assert function["environment"]["variables"] == {"DATA_FINOPS_BUCKET": DEPLOYMENT_BUCKET}
        assert (function["timeout"], function["memorySize"]) == (900, 512)
        workgroup = f"arn:aws:athena:us-east-1:{ACCOUNT}:workgroup/data-finops-demo"
        glue = f"arn:aws:glue:us-east-1:{ACCOUNT}"
        demo = f"arn:aws:s3:::{DEMO_BUCKET}"
        assert _role_policy("-simulator") == {
            "athena:StartQueryExecution": [workgroup],
            "athena:GetQueryExecution": [workgroup],
            "athena:StopQueryExecution": [workgroup],
            "glue:GetDatabase": [
                f"{glue}:catalog",
                f"{glue}:database/data_finops_demo",
                f"{glue}:table/data_finops_demo/*",
            ],
            "glue:GetTable": [
                f"{glue}:catalog",
                f"{glue}:database/data_finops_demo",
                f"{glue}:table/data_finops_demo/*",
            ],
            "glue:GetPartitions": [
                f"{glue}:catalog",
                f"{glue}:database/data_finops_demo",
                f"{glue}:table/data_finops_demo/*",
            ],
            "s3:ListBucket": [demo],
            "s3:GetBucketLocation": [demo],
            "s3:GetObject": [f"{demo}/dataset/*"],
            "s3:PutObject": [
                f"{demo}/athena-results/*",
                f"arn:aws:s3:::{DEPLOYMENT_BUCKET}/inbox/*",
            ],
            "s3:AbortMultipartUpload": [f"{demo}/athena-results/*"],
            "logs:CreateLogStream": [ANY_LOG_GROUP],
            "logs:PutLogEvents": [ANY_LOG_GROUP],
        }
        assert list(deployment.outputs()) == list(OUTPUTS)

    return _after(tmp_path, check, public_demo=True)


@pulumi.runtime.test
def test_the_schedule_starts_the_simulator_with_its_scheduled_time(
    tmp_path: Path,
) -> pulumi.Output[None]:
    def check(_: Deployment) -> None:
        (schedule,) = [s.inputs for s in MOCKS.of_type(SCHEDULE) if s.name == "data-finops-demo"]
        assert schedule["scheduleExpression"] == "cron(30 1/6 * * ? *)"
        assert schedule["flexibleTimeWindow"] == {"mode": "OFF"}
        target = schedule["target"]
        assert target["arn"].endswith(":function:data-finops-test-simulator")
        assert json.loads(target["input"]) == {"scheduled_time": "<aws.scheduler.scheduled-time>"}
        # A retry would run the queries again: their cost twice, for the same records.
        assert target["retryPolicy"] == {"maximumRetryAttempts": 0}
        assert _role_policy("-demo-scheduler") == {
            "lambda:InvokeFunction": [target["arn"]],
        }

    return _after(tmp_path, check, public_demo=True, demo_schedule="cron(30 1/6 * * ? *)")


@pulumi.runtime.test
def test_the_collector_also_reads_the_demo_workgroup(tmp_path: Path) -> pulumi.Output[None]:
    def check(_: Deployment) -> None:
        (collector,) = [f.inputs for f in MOCKS.of_type(FUNCTION) if f.name.endswith("collector")]
        given = json.loads(collector["environment"]["variables"]["DATA_FINOPS_COLLECTOR_SETTINGS"])
        assert given["workgroups"] == ["demo", "data-finops-demo"]
        history = _role_policy("-collector")["athena:ListQueryExecutions"]
        assert history == [
            f"arn:aws:athena:us-east-1:{ACCOUNT}:workgroup/demo",
            f"arn:aws:athena:us-east-1:{ACCOUNT}:workgroup/data-finops-demo",
        ]

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


@pulumi.runtime.test
def test_with_a_kms_key_the_simulator_can_write_the_inbox(tmp_path: Path) -> pulumi.Output[None]:
    kms = "arn:aws:kms:us-east-1:123456789012:key/example"

    def check(_: Deployment) -> None:
        assert _role_policy("-simulator")["kms:GenerateDataKey"] == [kms]

    return _after(tmp_path, check, public_demo=True, kms_key_arn=kms)


@pulumi.runtime.test
def test_lambda_does_not_retry_the_simulator(tmp_path: Path) -> pulumi.Output[None]:
    def check(_: Deployment) -> None:
        config = MOCKS.one("aws:lambda/functionEventInvokeConfig:FunctionEventInvokeConfig")
        assert config.inputs["functionName"] == "data-finops-test-simulator"
        assert config.inputs["maximumRetryAttempts"] == 0

    return _after(tmp_path, check, public_demo=True)
