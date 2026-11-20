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
FUNCTION, LOG_GROUP, URL = policies.FUNCTION, policies.LOG_GROUP, policies.FUNCTION_URL
ROLE_POLICY, MANAGED_POLICY = policies.IAM_DOCUMENTS
BUDGET = policies.BUDGET
ROUTE, STAGE = policies.ROUTE, policies.STAGE


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
    validators = policy.validate if isinstance(policy.validate, list) else [policy.validate]
    for validate in validators:  # a policy may hold one validator for each resource type
        validate(args, lambda message, urn=None: found.append(message))
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


# Policy 2: the API (spec 10 §The API)


@pytest.mark.parametrize(
    ("auth", "violations"),
    [
        ("AWS_IAM", []),
        ("NONE", ["the route GET /v1/lineage must use AWS_IAM authorization"]),
        (None, ["the route GET /v1/lineage must use AWS_IAM authorization"]),
    ],
)
def test_an_api_route_must_use_iam(auth: str | None, violations: list[str]) -> None:
    route = {"routeKey": "GET /v1/lineage", "authorizationType": auth}
    assert _violations_of(policies.API_IAM_ONLY, ROUTE, route) == violations


def test_a_function_url_is_refused() -> None:
    assert _violations_of(policies.API_IAM_ONLY, URL, {"authorizationType": "AWS_IAM"}) == [
        "the API is reached through API Gateway only, never a function URL (spec 10)"
    ]


@pytest.mark.parametrize(
    "limits",
    [
        {"throttlingRateLimit": 10, "throttlingBurstLimit": 20},
        {"throttlingRateLimit": 0, "throttlingBurstLimit": 0},  # the kill switch
    ],
)
def test_a_stage_with_both_limits_passes(limits: dict[str, Any]) -> None:
    stage = {"name": "$default", "defaultRouteSettings": limits}
    assert _violations_of(policies.API_THROTTLED, STAGE, stage) == []


@pytest.mark.parametrize(
    "settings",
    [
        None,
        {},
        {"throttlingRateLimit": 10},
        {"throttlingBurstLimit": 20},
        {"throttlingRateLimit": 10, "throttlingBurstLimit": None},
        {"throttlingRateLimit": -1, "throttlingBurstLimit": 20},
    ],
)
def test_a_stage_without_both_limits_is_refused(settings: dict[str, Any] | None) -> None:
    stage = {"name": "$default", "defaultRouteSettings": settings}
    assert _violations_of(policies.API_THROTTLED, STAGE, stage) == [
        "the stage must set a rate limit and a burst limit of 0 or more"
    ]


# Policy 3: functions bounded and logged

BOUNDED = {
    "name": "data-finops-test-api",
    "timeout": 30,
    "memorySize": 1024,
    "ephemeralStorage": {"size": 10240},
}


def test_a_function_must_set_its_timeout_and_memory_within_bounds() -> None:
    assert _violations_of(policies.FUNCTION_BOUNDED, FUNCTION, BOUNDED) == []
    assert _violations_of(policies.FUNCTION_BOUNDED, FUNCTION, {"name": "f"}) == [
        "timeout must be set, from 1 to 900 seconds",
        "memorySize must be set, from 128 to 10240 MB",
        "ephemeralStorage.size must be set, from 512 to 10240 MB",
    ]
    assert _violations_of(policies.FUNCTION_BOUNDED, FUNCTION, BOUNDED | {"memorySize": 20480}) == [
        "memorySize must be set, from 128 to 10240 MB"
    ]
    for storage in ({"size": 511}, {"size": 10241}, {}, None):
        assert _violations_of(
            policies.FUNCTION_BOUNDED, FUNCTION, BOUNDED | {"ephemeralStorage": storage}
        ) == ["ephemeralStorage.size must be set, from 512 to 10240 MB"]


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


# Policy 4: IAM, one allowlist per Deployment policy (spec 08 §IAM)


def _document(*statements: dict[str, Any]) -> dict[str, Any]:
    return {"policy": json.dumps({"Version": "2012-10-17", "Statement": list(statements)})}


DATABASE = "arn:aws:s3:::b/database/data-finops.sqlite"
KEY = "arn:aws:kms:us-east-1:123456789012:key/example"


def _allow(action: Any, resource: Any = DATABASE) -> dict[str, Any]:
    return {"Effect": "Allow", "Action": action, "Resource": resource}


def _iam(props: dict[str, Any], name: str, typ: str = ROLE_POLICY) -> list[str]:
    return _violations_of(policies.IAM_LEAST_PRIVILEGE, typ, props, name)


def test_each_policy_may_allow_its_own_actions() -> None:
    collector = _document(
        _allow(["s3:GetObject", "s3:PutObject"]),
        _allow(["athena:ListQueryExecutions"], ["arn:aws:athena:us-east-1:1:workgroup/demo"]),
        _allow(["logs:PutLogEvents"], ["arn:aws:logs:us-east-1:1:log-group:/aws/lambda/x:*"]),
        _allow("s3:GetObject", "arn:aws:s3:::b/inbox/*"),
        {"Effect": "Deny", "Action": ["athena:StartQueryExecution"], "Resource": "*"},
    )
    assert _iam(collector, "data-finops-collector") == []
    assert _iam(_document(_allow("s3:GetObject")), "data-finops-api") == []
    assert (
        _iam(
            _document(_allow("lambda:InvokeFunction", "arn:aws:lambda:us-east-1:1:function:c")),
            "data-finops-scheduler",
        )
        == []
    )
    producer = _document(_allow("s3:PutObject", "arn:aws:s3:::b/inbox/*"))
    assert _iam(producer, "data-finops-producer", MANAGED_POLICY) == []
    with_key = _document(
        _allow("s3:PutObject", "arn:aws:s3:::b/inbox/*"),
        _allow("kms:GenerateDataKey", KEY),
    )
    assert _iam(with_key, "data-finops-producer", MANAGED_POLICY) == []
    route = "arn:aws:execute-api:us-east-1:123456789012:abc123/*/GET/v1/lineage"
    for role in ("reader", "consumer", "operator"):
        invoke = _document(_allow("execute-api:Invoke", [route]))
        assert _iam(invoke, f"data-finops-{role}", MANAGED_POLICY) == [], role


@pytest.mark.parametrize(
    ("name", "action"),
    [
        ("data-finops-producer", "kms:GenerateDataKey"),
        ("data-finops-collector", "kms:Decrypt"),
        ("data-finops-api", "kms:Decrypt"),
    ],
)
def test_a_key_action_is_allowed_only_on_a_kms_key(name: str, action: str) -> None:
    on_the_inbox = _document(_allow(action, ["arn:aws:s3:::b/inbox/*"]))
    assert _iam(on_the_inbox, name) == [f"{action} is allowed only on a KMS key"]
    assert _iam(_document(_allow(action, KEY)), name) == []


@pytest.mark.parametrize(
    "resource",
    [
        "arn:aws:kms:us-east-1:123456789012:key/*",  # every key in the account
        "arn:aws:kms:us-east-1:123456789012:key/data-*",  # every key whose id starts so
        "arn:aws:kms:us-east-1:123456789012:key/data-????",  # IAM reads ? as one character
        "arn:aws:kms:us-east-?:123456789012:key/example",
        "arn:aws:kms:us-east-1:123456789012:key/a/b",
        # IAM does not grant key use through an alias:
        "arn:aws:kms:us-east-1:123456789012:alias/data-finops",
        [KEY, "arn:aws:s3:::b/inbox/*"],
    ],
)
def test_a_key_action_names_one_key_and_nothing_else(resource: Any) -> None:
    assert _iam(_document(_allow("kms:GenerateDataKey", resource)), "data-finops-producer") == [
        "kms:GenerateDataKey is allowed only on a KMS key"
    ]


@pytest.mark.parametrize(
    ("name", "action"),
    [
        ("data-finops-collector", "s3:*"),
        ("data-finops-collector", "*"),
        ("data-finops-collector", "athena:StartQueryExecution"),
        ("data-finops-collector", "s3:DeleteObject"),
        ("data-finops-collector", "lambda:InvokeFunction"),
        ("data-finops-api", "s3:PutObject"),
        ("data-finops-api", "athena:ListQueryExecutions"),
        ("data-finops-scheduler", "s3:GetObject"),
        ("data-finops-producer", "s3:GetObject"),
        ("data-finops-reader", "s3:GetObject"),
        # The Function URL is gone (spec 10): its actions are no role's.
        ("data-finops-reader", "lambda:InvokeFunctionUrl"),
        ("data-finops-consumer", "lambda:InvokeFunction"),
        ("data-finops-operator", "s3:PutObject"),
    ],
)
def test_a_policy_may_not_allow_another_policys_actions(name: str, action: str) -> None:
    kind = name.rsplit("-", 1)[1]
    assert _iam(_document(_allow(action)), name) == [
        f"{action} is not an action the {kind} policy is granted (spec 08 §IAM)"
    ]


@pytest.mark.parametrize(
    "resource",
    ["*", ["arn:aws:s3:::b/x", "*"], "arn:aws:s3:::*", "arn:aws:lambda:*:*:function:*"],
)
def test_no_action_is_allowed_on_a_wildcard_resource(resource: Any) -> None:
    assert _iam(_document(_allow("s3:GetObject", resource)), "data-finops-collector") == [
        "s3:GetObject is allowed on a wildcard resource"
    ]


@pytest.mark.parametrize(
    "statement",
    [
        {"Effect": "Allow", "NotAction": "s3:GetObject", "Resource": DATABASE},
        {"Effect": "Allow", "Action": "s3:GetObject", "NotResource": DATABASE},
    ],
)
def test_an_allow_may_not_use_not_action_or_not_resource(statement: dict[str, Any]) -> None:
    assert _iam(_document(statement), "data-finops-collector") == [
        "an Allow may not use NotAction or NotResource"
    ]


def test_a_single_statement_is_checked_like_a_list() -> None:
    single = {"policy": json.dumps({"Version": "2012-10-17", "Statement": _allow("*")})}
    assert _iam(single, "data-finops-collector") == [
        "* is not an action the collector policy is granted (spec 08 §IAM)"
    ]


def test_an_unknown_policy_fails_closed() -> None:
    # A renamed or extra policy is never let through unchecked.
    assert _iam(_document(_allow("s3:GetObject")), "data-finops-reporting") == [
        "data-finops-reporting is not a known Deployment policy, so its actions cannot be checked"
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


DISTRIBUTION = policies.DISTRIBUTION
GUARDED_SITE = {
    "origins": [{"originId": "demo-bucket", "domainName": "b.s3", "originAccessControlId": "oac"}],
    "defaultCacheBehavior": {"viewerProtocolPolicy": "redirect-to-https"},
    "orderedCacheBehaviors": [
        {"pathPattern": "current.json", "viewerProtocolPolicy": "https-only"},
    ],
}


def test_a_distribution_through_origin_access_control_and_https_passes() -> None:
    assert _violations_of(policies.CLOUDFRONT_GUARDED, DISTRIBUTION, GUARDED_SITE) == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"origins": [{"originId": "demo-bucket", "domainName": "b.s3"}]}, "origin access control"),
        (
            {"origins": [{"originId": "o", "domainName": "b", "originAccessControlId": ""}]},
            "origin access control",
        ),
        ({"defaultCacheBehavior": {"viewerProtocolPolicy": "allow-all"}}, "HTTPS"),
        (
            {"orderedCacheBehaviors": [{"pathPattern": "x", "viewerProtocolPolicy": "allow-all"}]},
            "HTTPS",
        ),
        ({"defaultCacheBehavior": {}}, "HTTPS"),
    ],
)
def test_a_distribution_without_origin_access_control_or_https_is_refused(
    change: dict[str, Any], message: str
) -> None:
    (violation,) = _violations_of(policies.CLOUDFRONT_GUARDED, DISTRIBUTION, GUARDED_SITE | change)
    assert message in violation
