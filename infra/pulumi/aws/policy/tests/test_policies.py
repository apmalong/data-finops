"""The policy pack's policies, one passing and one violating case each (spec 08 seam 7a).

Arguments are built as Pulumi hands them to a policy: a resource type and its inputs, camel-
cased. Policies are a plain module, so importing them never starts the pack's server.
"""

import json
from typing import Any

import policies
import pytest
from pulumi_policy import PolicyResource, ResourceValidationArgs, StackValidationArgs
from pulumi_policy.policy import PolicyCustomTimeouts, PolicyResourceOptions

BUCKET = "aws:s3/bucket:Bucket"
BLOCK = "aws:s3/bucketPublicAccessBlock:BucketPublicAccessBlock"
ENCRYPTION = (
    "aws:s3/bucketServerSideEncryptionConfiguration:BucketServerSideEncryptionConfiguration"
)
VERSIONING = "aws:s3/bucketVersioning:BucketVersioning"
POLICY = "aws:s3/bucketPolicy:BucketPolicy"


def _options() -> PolicyResourceOptions:
    return PolicyResourceOptions(False, [], None, [], PolicyCustomTimeouts(0, 0, 0), [])


def _resource(typ: str, props: dict[str, Any], name: str = "data-finops") -> PolicyResource:
    urn = f"urn:pulumi:test::data-finops::{typ}::{name}"
    return PolicyResource(typ, props, urn, name, _options(), None, None, [], {})


def _violations_of(policy: Any, typ: str, props: dict[str, Any]) -> list[str]:
    found: list[str] = []
    args = ResourceValidationArgs(
        typ, props, f"urn:pulumi:test::data-finops::{typ}::x", "x", _options(), None
    )
    policy.validate(args, lambda message, urn=None: found.append(message))
    return found


def _stack_violations(policy: Any, resources: list[PolicyResource]) -> list[str]:
    found: list[str] = []
    policy.validate(StackValidationArgs(resources), lambda message, urn=None: found.append(message))
    return found


ALL_BLOCKED = {
    "blockPublicAcls": True,
    "blockPublicPolicy": True,
    "ignorePublicAcls": True,
    "restrictPublicBuckets": True,
}
TLS_ONLY = json.dumps(
    {
        "Statement": [
            {
                "Effect": "Deny",
                "Action": "s3:*",
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            }
        ]
    }
)


def _guarded_bucket() -> list[PolicyResource]:
    return [
        _resource(BUCKET, {}),
        _resource(BLOCK, ALL_BLOCKED),
        _resource(
            ENCRYPTION,
            {"rules": [{"applyServerSideEncryptionByDefault": {"sseAlgorithm": "AES256"}}]},
        ),
        _resource(VERSIONING, {"versioningConfiguration": {"status": "Enabled"}}),
        _resource(POLICY, {"policy": TLS_ONLY}),
    ]


# Policy 1: S3


def test_a_public_access_block_must_block_everything() -> None:
    assert _violations_of(policies.S3_PUBLIC_ACCESS_BLOCKED, BLOCK, ALL_BLOCKED) == []
    assert _violations_of(
        policies.S3_PUBLIC_ACCESS_BLOCKED, BLOCK, ALL_BLOCKED | {"ignorePublicAcls": False}
    ) == ["ignorePublicAcls must be true"]


@pytest.mark.parametrize("algorithm", ["AES256", "aws:kms"])
def test_default_encryption_may_be_s3_or_kms(algorithm: str) -> None:
    props = {"rules": [{"applyServerSideEncryptionByDefault": {"sseAlgorithm": algorithm}}]}
    assert _violations_of(policies.S3_ENCRYPTED, ENCRYPTION, props) == []


def test_an_encryption_configuration_without_a_default_is_refused() -> None:
    assert _violations_of(policies.S3_ENCRYPTED, ENCRYPTION, {"rules": [{}]}) == [
        "every rule must set default encryption to AES256 or aws:kms"
    ]


def test_versioning_must_be_enabled() -> None:
    enabled = {"versioningConfiguration": {"status": "Enabled"}}
    suspended = {"versioningConfiguration": {"status": "Suspended"}}
    assert _violations_of(policies.S3_VERSIONED, VERSIONING, enabled) == []
    assert _violations_of(policies.S3_VERSIONED, VERSIONING, suspended) == [
        "versioning must be Enabled"
    ]


def test_a_bucket_policy_must_deny_requests_without_tls() -> None:
    allow_all = json.dumps({"Statement": [{"Effect": "Allow", "Action": "s3:GetObject"}]})
    assert _violations_of(policies.S3_TLS_ONLY, POLICY, {"policy": TLS_ONLY}) == []
    assert _violations_of(policies.S3_TLS_ONLY, POLICY, {"policy": allow_all}) == [
        "the bucket policy must deny every request made without TLS"
    ]


def test_every_bucket_must_have_all_its_guards() -> None:
    assert _stack_violations(policies.S3_GUARDED, _guarded_bucket()) == []
    without_versioning = [r for r in _guarded_bucket() if r.resource_type != VERSIONING]
    assert _stack_violations(policies.S3_GUARDED, without_versioning) == [
        "each bucket needs a BucketVersioning; 1 bucket(s), 0 found"
    ]


# Policy 5: nothing spec 05 rules out


@pytest.mark.parametrize(
    "typ",
    [
        "aws:ec2/vpc:Vpc",
        "aws:ec2/subnet:Subnet",
        "aws:ec2/natGateway:NatGateway",
        "aws:ec2/eip:Eip",
        "aws:rds/instance:Instance",
        "aws:rds/cluster:Cluster",
        "aws:eks/cluster:Cluster",
        "aws:ecs/service:Service",
        "aws:efs/fileSystem:FileSystem",
    ],
)
def test_resources_spec_05_rules_out_are_refused(typ: str) -> None:
    (message,) = _violations_of(policies.NO_RULED_OUT_RESOURCES, typ, {})
    assert typ in message


@pytest.mark.parametrize(
    "typ", [BUCKET, "aws:lambda/function:Function", "aws:ec2/securityGroup:SecurityGroup"]
)
def test_other_resources_are_allowed(typ: str) -> None:
    assert _violations_of(policies.NO_RULED_OUT_RESOURCES, typ, {}) == []


def test_every_policy_is_mandatory() -> None:
    assert policies.POLICIES
    assert {p.enforcement_level for p in policies.POLICIES} == {policies.MANDATORY}
