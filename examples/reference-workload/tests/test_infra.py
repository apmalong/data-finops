"""Reference workload infrastructure through Pulumi's mocks (seam A agreed in #21)."""

from decimal import Decimal

import pulumi
import pytest


class RecordingMocks(pulumi.runtime.Mocks):
    def __init__(self) -> None:
        self.resources: list[pulumi.runtime.MockResourceArgs] = []

    def new_resource(self, args: pulumi.runtime.MockResourceArgs) -> tuple[str, dict]:
        self.resources.append(args)
        return f"{args.name}-id", {**args.inputs, "bucket": args.inputs.get("bucket", args.name)}

    def call(self, args: pulumi.runtime.MockCallArgs) -> dict:
        return {}

    def of_type(self, typ: str) -> list[pulumi.runtime.MockResourceArgs]:
        return [r for r in self.resources if r.typ == typ]


MOCKS = RecordingMocks()
pulumi.runtime.set_mocks(MOCKS, preview=False)

import infra  # noqa: E402  (must load after mocks are set)


@pytest.fixture(autouse=True)
def _fresh_mocks():
    MOCKS.resources.clear()


@pulumi.runtime.test
def test_workgroup_caps_each_query_and_writes_results_to_the_bucket():
    stack = infra.build(infra.Settings())

    def check(_: object) -> None:
        (workgroup,) = MOCKS.of_type("aws:athena/workgroup:Workgroup")
        config = workgroup.inputs["configuration"]
        assert config["bytesScannedCutoffPerQuery"] == 104_857_600
        assert config["enforceWorkgroupConfiguration"] is True
        result = config["resultConfiguration"]
        assert result["outputLocation"].startswith("s3://")
        assert result["outputLocation"].endswith("/athena-results/")
        assert result["encryptionConfiguration"]["encryptionOption"] == "SSE_S3"

    return stack.resource_ids.apply(check)


@pulumi.runtime.test
def test_bucket_is_private_encrypted_and_removable():
    stack = infra.build(infra.Settings())

    def check(_: object) -> None:
        (bucket,) = MOCKS.of_type("aws:s3/bucket:Bucket")
        assert bucket.inputs["forceDestroy"] is True
        (block,) = MOCKS.of_type("aws:s3/bucketPublicAccessBlock:BucketPublicAccessBlock")
        assert all(
            block.inputs[k] is True
            for k in (
                "blockPublicAcls",
                "blockPublicPolicy",
                "ignorePublicAcls",
                "restrictPublicBuckets",
            )
        )
        (encryption,) = MOCKS.of_type(
            "aws:s3/bucketServerSideEncryptionConfiguration:BucketServerSideEncryptionConfiguration"
        )
        rule = encryption.inputs["rules"][0]["applyServerSideEncryptionByDefault"]
        assert rule["sseAlgorithm"] == "AES256"

    return stack.resource_ids.apply(check)


@pulumi.runtime.test
def test_glue_database_holds_the_workload_tables():
    stack = infra.build(infra.Settings())

    def check(_: object) -> None:
        (database,) = MOCKS.of_type("aws:glue/catalogDatabase:CatalogDatabase")
        assert database.inputs["name"] == "reference_workload"

    return stack.resource_ids.apply(check)


@pulumi.runtime.test
def test_no_budget_unless_the_installation_configures_one():
    stack = infra.build(infra.Settings())

    def check(_: object) -> None:
        assert MOCKS.of_type("aws:budgets/budget:Budget") == []

    return stack.resource_ids.apply(check)


@pulumi.runtime.test
def test_configured_budget_alerts_the_configured_recipient():
    stack = infra.build(
        infra.Settings(budget_monthly_usd=Decimal("5"), budget_email="ops@example.com")
    )

    def check(_: object) -> None:
        (budget,) = MOCKS.of_type("aws:budgets/budget:Budget")
        assert budget.inputs["limitAmount"] == "5"
        assert budget.inputs["limitUnit"] == "USD"
        assert budget.inputs["timeUnit"] == "MONTHLY"
        (notification,) = budget.inputs["notifications"]
        assert notification["subscriberEmailAddresses"] == ["ops@example.com"]

    return stack.resource_ids.apply(check)


def test_a_budget_needs_both_amount_and_recipient():
    with pytest.raises(ValueError, match="budget"):
        infra.Settings(budget_monthly_usd=Decimal("5"))
