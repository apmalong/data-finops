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

BUCKET, BLOCK, ENCRYPTION = policies.BUCKET, policies.PUBLIC_ACCESS_BLOCK, policies.ENCRYPTION
VERSIONING, POLICY = policies.VERSIONING, policies.BUCKET_POLICY


def _options() -> PolicyResourceOptions:
    return PolicyResourceOptions(False, [], None, [], PolicyCustomTimeouts(0, 0, 0), [])


def _resource(typ: str, props: dict[str, Any], name: str = "data-finops") -> PolicyResource:
    urn = f"urn:pulumi:test::data-finops::{typ}::{name}"
    return PolicyResource(typ, props, urn, name, _options(), None, None, [], {})


def _violations_of(policy: Any, typ: str, props: dict[str, Any], name: str = "x") -> list[str]:
    found: list[str] = []
    args = ResourceValidationArgs(
        typ, props, f"urn:pulumi:test::data-finops::{typ}::{name}", name, _options(), None
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


FUNCTION = "aws:lambda/function:Function"
LOG_GROUP = "aws:cloudwatch/logGroup:LogGroup"
URL = "aws:lambda/functionUrl:FunctionUrl"
ROLE_POLICY = "aws:iam/rolePolicy:RolePolicy"
MANAGED_POLICY = "aws:iam/policy:Policy"
BUDGET = "aws:budgets/budget:Budget"


# Policy 2: Function URLs


@pytest.mark.parametrize(("auth", "violations"), [("AWS_IAM", 0), ("NONE", 1)])
def test_a_function_url_must_use_iam(auth: str, violations: int) -> None:
    found = _violations_of(policies.URL_IAM_ONLY, URL, {"authorizationType": auth})
    assert len(found) == violations


# Policy 3: functions bounded and logged

BOUNDED = {"name": "data-finops-test-api", "timeout": 30, "memorySize": 1024}


def test_a_function_must_set_its_timeout_and_memory_within_bounds() -> None:
    assert _violations_of(policies.FUNCTION_BOUNDED, FUNCTION, BOUNDED) == []
    assert _violations_of(policies.FUNCTION_BOUNDED, FUNCTION, {"name": "f"}) == [
        "timeout must be set, from 1 to 900 seconds",
        "memorySize must be set, from 128 to 10240 MB",
    ]
    assert _violations_of(policies.FUNCTION_BOUNDED, FUNCTION, BOUNDED | {"memorySize": 20480}) == [
        "memorySize must be set, from 128 to 10240 MB"
    ]


def test_every_function_needs_its_log_group_with_retention() -> None:
    function = _resource(FUNCTION, BOUNDED)
    group = {"name": "/aws/lambda/data-finops-test-api", "retentionInDays": 30}
    assert (
        _stack_violations(policies.FUNCTION_LOGGED, [function, _resource(LOG_GROUP, group)]) == []
    )
    unretained = _resource(LOG_GROUP, group | {"retentionInDays": None})
    assert _stack_violations(policies.FUNCTION_LOGGED, [function, unretained]) == [
        "data-finops-test-api needs a log group /aws/lambda/data-finops-test-api with retention"
    ]
    assert _stack_violations(policies.FUNCTION_LOGGED, [function]) == [
        "data-finops-test-api needs a log group /aws/lambda/data-finops-test-api with retention"
    ]


# Policy 4: IAM


def _document(*statements: dict[str, Any]) -> dict[str, Any]:
    return {"policy": json.dumps({"Version": "2012-10-17", "Statement": list(statements)})}


def _allow(
    action: Any, resource: Any = "arn:aws:s3:::b/database/data-finops.sqlite"
) -> dict[str, Any]:
    return {"Effect": "Allow", "Action": action, "Resource": resource}


def test_iam_may_allow_only_the_deployments_actions() -> None:
    fine = _document(
        _allow(["s3:GetObject", "s3:PutObject"]),
        _allow(["athena:ListQueryExecutions"], ["arn:aws:athena:us-east-1:1:workgroup/demo"]),
        {"Effect": "Deny", "Action": ["athena:StartQueryExecution"], "Resource": "*"},
    )
    assert (
        _violations_of(policies.IAM_LEAST_PRIVILEGE, ROLE_POLICY, fine, "data-finops-collector")
        == []
    )


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (_allow("s3:*"), "s3:* is not an action the Deployment uses"),
        (_allow("*"), "* is not an action the Deployment uses"),
        (
            _allow("athena:StartQueryExecution"),
            "athena:StartQueryExecution is not an action the Deployment uses",
        ),
        (_allow("s3:DeleteObject"), "s3:DeleteObject is not an action the Deployment uses"),
        (_allow("s3:GetObject", "*"), "s3:GetObject is allowed on every resource"),
    ],
)
def test_iam_refuses_wildcards_query_running_and_unknown_actions(
    statement: dict[str, Any], message: str
) -> None:
    for typ in (ROLE_POLICY, MANAGED_POLICY):
        found = _violations_of(policies.IAM_LEAST_PRIVILEGE, typ, _document(statement))
        assert found == [message]


def test_the_api_role_may_not_write_to_the_bucket() -> None:
    writes = _document(_allow("s3:PutObject"))
    assert _violations_of(policies.IAM_LEAST_PRIVILEGE, ROLE_POLICY, writes, "data-finops-api") == [
        "the API role is read-only: s3:PutObject is a write"
    ]


# Policy 6: Budget


def _notification(kind: str) -> dict[str, Any]:
    return {"notificationType": kind, "comparisonOperator": "GREATER_THAN", "threshold": 100}


def test_a_budget_must_alert_on_forecast_and_actual_spend() -> None:
    both = {"notifications": [_notification("FORECASTED"), _notification("ACTUAL")]}
    actual = {"notifications": [_notification("ACTUAL")]}
    assert _violations_of(policies.BUDGET_ALERTS, BUDGET, both) == []
    assert _violations_of(policies.BUDGET_ALERTS, BUDGET, actual) == [
        "the Budget must alert on forecast spend as well as actual spend"
    ]
    forecast = {"notifications": [_notification("FORECASTED")]}
    assert _violations_of(policies.BUDGET_ALERTS, BUDGET, forecast) == [
        "the Budget must alert on actual spend as well as forecast spend"
    ]
