"""The Deployment's bucket through Pulumi's mocks (spec 08 §Topology Bucket; seam 6)."""

import dataclasses
import json
from collections.abc import Callable

import pulumi
import pytest
from deployment.settings import Settings
from deployment.storage import Storage, kept_on_destroy

from tests.mocks import MOCKS

SETTINGS = Settings(
    region="us-east-1",
    export_bucket="example-export-bucket",
    export_name="example-focus-1-2",
    workgroups=("demo",),
)


@pytest.fixture(autouse=True)
def _fresh_mocks() -> None:
    MOCKS.resources.clear()


def _after(settings: Settings, check: Callable[[], None]) -> pulumi.Output[None]:
    """Declare the storage, then run `check` once every resource is registered."""
    return Storage("data-finops", settings).ready.apply(lambda _: check())


@pulumi.runtime.test
def test_the_bucket_is_private_and_owner_enforced() -> pulumi.Output[None]:
    def check() -> None:
        block = MOCKS.one("aws:s3/bucketPublicAccessBlock:BucketPublicAccessBlock").inputs
        assert [block[k] for k in ("blockPublicAcls", "blockPublicPolicy", "ignorePublicAcls",
                                   "restrictPublicBuckets")] == [True] * 4  # fmt: skip
        ownership = MOCKS.one("aws:s3/bucketOwnershipControls:BucketOwnershipControls").inputs
        assert ownership["rule"]["objectOwnership"] == "BucketOwnerEnforced"

    return _after(SETTINGS, check)


@pulumi.runtime.test
def test_the_bucket_is_encrypted_with_s3_managed_keys_by_default() -> pulumi.Output[None]:
    def check() -> None:
        encryption = MOCKS.one(
            "aws:s3/bucketServerSideEncryptionConfiguration:BucketServerSideEncryptionConfiguration"
        ).inputs
        (rule,) = encryption["rules"]
        assert rule["applyServerSideEncryptionByDefault"] == {"sseAlgorithm": "AES256"}

    return _after(SETTINGS, check)


@pulumi.runtime.test
def test_a_customer_key_encrypts_with_kms_and_a_bucket_key() -> pulumi.Output[None]:
    key = "arn:aws:kms:us-east-1:123456789012:key/example"

    def check() -> None:
        encryption = MOCKS.one(
            "aws:s3/bucketServerSideEncryptionConfiguration:BucketServerSideEncryptionConfiguration"
        ).inputs
        (rule,) = encryption["rules"]
        assert rule["applyServerSideEncryptionByDefault"] == {
            "sseAlgorithm": "aws:kms",
            "kmsMasterKeyId": key,
        }
        assert rule["bucketKeyEnabled"] is True

    return _after(dataclasses.replace(SETTINGS, kms_key_arn=key), check)


@pulumi.runtime.test
def test_the_bucket_is_versioned_and_old_versions_and_inbox_objects_expire() -> pulumi.Output[None]:
    def check() -> None:
        versioning = MOCKS.one("aws:s3/bucketVersioning:BucketVersioning").inputs
        assert versioning["versioningConfiguration"]["status"] == "Enabled"
        lifecycle = MOCKS.one(
            "aws:s3/bucketLifecycleConfiguration:BucketLifecycleConfiguration"
        ).inputs
        rules = {rule["id"]: rule for rule in lifecycle["rules"]}
        assert rules["noncurrent-versions"]["noncurrentVersionExpiration"]["noncurrentDays"] == 7
        assert rules["noncurrent-versions"]["status"] == "Enabled"
        inbox = rules["inbox"]
        assert (inbox["filter"]["prefix"], inbox["expiration"]["days"]) == ("inbox/", 10)

    settings = dataclasses.replace(SETTINGS, noncurrent_version_days=7, inbox_retention_days=10)
    return _after(settings, check)


@pulumi.runtime.test
def test_requests_without_tls_are_denied() -> pulumi.Output[None]:
    def check() -> None:
        policy = json.loads(MOCKS.one("aws:s3/bucketPolicy:BucketPolicy").inputs["policy"])
        (statement,) = policy["Statement"]
        assert (statement["Effect"], statement["Action"]) == ("Deny", "s3:*")
        assert statement["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}
        assert statement["Resource"] == [
            "arn:aws:s3:::mock-deployment-bucket",
            "arn:aws:s3:::mock-deployment-bucket/*",
        ]

    return _after(SETTINGS, check)


@pytest.mark.parametrize("delete", [False, True])
@pulumi.runtime.test
def test_the_bucket_is_kept_on_destroy_unless_deletion_is_opted_in(
    delete: bool,
) -> pulumi.Output[None]:
    def check() -> None:
        assert MOCKS.one("aws:s3/bucket:Bucket").inputs.get("forceDestroy", False) is delete

    return _after(dataclasses.replace(SETTINGS, delete_data_on_destroy=delete), check)


@pulumi.runtime.test
def test_the_storage_is_one_component() -> pulumi.Output[None]:
    def check() -> None:
        (component,) = MOCKS.of_type("data-finops:index:Storage")
        assert component.custom is False

    return _after(SETTINGS, check)


@pytest.mark.parametrize(("delete", "kept"), [(False, True), (True, False)])
def test_the_bucket_and_every_guard_share_its_retention(delete: bool, kept: bool) -> None:
    # Mocks cannot see resource options, so the options every resource of the component is
    # declared with come from one function, checked here.
    options = kept_on_destroy(dataclasses.replace(SETTINGS, delete_data_on_destroy=delete))
    assert options.retain_on_delete is kept
