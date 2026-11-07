"""The Deployment's mandatory policies (spec 08 §Policy pack): 1 S3, 2 Function URLs, 3 functions
bounded and logged, 4 IAM least privilege, 5 nothing spec 05 rules out, 6 the Budget. A plain
module: only `__main__.py` builds the pack, because constructing `PolicyPack` starts its
server and blocks.

Each policy reads inputs a program sets as literals. A value unknown at preview (computed from
another resource's outputs) only yields Pulumi's advisory "can't run policy during preview".
The TLS-only bucket policy and the IAM documents are computed from other resources' ARNs, so
they are checked where they are known: `pulumi policy analyze` over the program's recorded
state (spec 08 seam 7b).
"""

import json
from collections import Counter
from collections.abc import Callable
from typing import Any

from pulumi_policy import (
    EnforcementLevel,
    ResourceValidationArgs,
    ResourceValidationPolicy,
    StackValidationArgs,
    StackValidationPolicy,
)

MANDATORY = EnforcementLevel.MANDATORY
Report = Callable[..., None]

BUCKET = "aws:s3/bucket:Bucket"
PUBLIC_ACCESS_BLOCK = "aws:s3/bucketPublicAccessBlock:BucketPublicAccessBlock"
ENCRYPTION = (
    "aws:s3/bucketServerSideEncryptionConfiguration:BucketServerSideEncryptionConfiguration"
)
VERSIONING = "aws:s3/bucketVersioning:BucketVersioning"
BUCKET_POLICY = "aws:s3/bucketPolicy:BucketPolicy"
GUARDS = {
    PUBLIC_ACCESS_BLOCK: "BucketPublicAccessBlock",
    ENCRYPTION: "BucketServerSideEncryptionConfiguration",
    VERSIONING: "BucketVersioning",
    BUCKET_POLICY: "BucketPolicy",
}
PUBLIC_ACCESS_FLAGS = (
    "blockPublicAcls",
    "blockPublicPolicy",
    "ignorePublicAcls",
    "restrictPublicBuckets",
)
FUNCTION = "aws:lambda/function:Function"
LOG_GROUP = "aws:cloudwatch/logGroup:LogGroup"
FUNCTION_URL = "aws:lambda/functionUrl:FunctionUrl"
IAM_DOCUMENTS = ("aws:iam/rolePolicy:RolePolicy", "aws:iam/policy:Policy")
BUDGET = "aws:budgets/budget:Budget"
TIMEOUT_SECONDS = (1, 900)
MEMORY_MB = (128, 10240)
"""Spec 08's bounds on every function (the settings table's widest)."""
DEPLOYMENT_ACTIONS = frozenset(
    {
        "s3:ListBucket",
        "s3:GetObject",
        "s3:PutObject",
        "athena:ListQueryExecutions",
        "athena:BatchGetQueryExecution",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "kms:Decrypt",
        "kms:GenerateDataKey",
        "lambda:InvokeFunction",
        "lambda:InvokeFunctionUrl",
    }
)
"""Every action spec 08 §IAM grants anywhere in the Deployment; nothing else may be allowed."""
WRITES = frozenset({"s3:PutObject", "kms:GenerateDataKey"})
"""Allowed actions that write: never in the API role."""
API_ROLE = "-api"
"""The suffix of the API role's policy name (`deployment.compute`)."""
RULED_OUT = (
    "aws:ec2/vpc:",
    "aws:ec2/subnet:",
    "aws:ec2/natGateway:",
    "aws:ec2/eip:",
    "aws:rds/",
    "aws:eks/",
    "aws:ecs/",
    "aws:efs/",
)
"""Resource type prefixes spec 05 rules out: no VPC, subnet, NAT, EIP, RDS, EKS, ECS or EFS."""


def _for(typ: str, check: Callable[[dict[str, Any], Report], None]) -> Any:
    """A validator that runs `check` on resources of type `typ` only."""

    def validate(args: ResourceValidationArgs, report: Report) -> None:
        if args.resource_type == typ:
            check(dict(args.props), report)

    return validate


def _public_access_blocked(props: dict[str, Any], report: Report) -> None:
    for flag in PUBLIC_ACCESS_FLAGS:
        if props.get(flag) is not True:
            report(f"{flag} must be true")


def _encrypted(props: dict[str, Any], report: Report) -> None:
    rules = props.get("rules") or []
    algorithms = [
        (rule.get("applyServerSideEncryptionByDefault") or {}).get("sseAlgorithm") for rule in rules
    ]
    if not algorithms or any(a not in ("AES256", "aws:kms") for a in algorithms):
        report("every rule must set default encryption to AES256 or aws:kms")


def _versioned(props: dict[str, Any], report: Report) -> None:
    if (props.get("versioningConfiguration") or {}).get("status") != "Enabled":
        report("versioning must be Enabled")


def _tls_only(props: dict[str, Any], report: Report) -> None:
    try:
        statements = json.loads(props.get("policy") or "{}").get("Statement") or []
    except (ValueError, AttributeError):
        statements = []
    denies = any(
        s.get("Effect") == "Deny"
        and s.get("Action") in ("s3:*", ["s3:*"])
        and (s.get("Condition") or {}).get("Bool", {}).get("aws:SecureTransport") == "false"
        for s in statements
        if isinstance(s, dict)
    )
    if not denies:
        report("the bucket policy must deny every request made without TLS")


def _guarded(args: StackValidationArgs, report: Report) -> None:
    """Every bucket has each guard, counted by type: a guard names its bucket by the bucket's
    ID, unknown at preview, so it cannot be linked there. Sound for the Deployment's one
    bucket; a stack with several would need the links checked another way."""
    counts = Counter(resource.resource_type for resource in args.resources)
    buckets = counts[BUCKET]
    for typ, name in GUARDS.items():
        if counts[typ] < buckets:
            report(f"each bucket needs a {name}; {buckets} bucket(s), {counts[typ]} found")


def _url_iam_only(props: dict[str, Any], report: Report) -> None:
    if props.get("authorizationType") != "AWS_IAM":
        report("a function URL must use AWS_IAM authorization, never NONE")


def _bounded(props: dict[str, Any], report: Report) -> None:
    for name, (least, most), unit in (
        ("timeout", TIMEOUT_SECONDS, "seconds"),
        ("memorySize", MEMORY_MB, "MB"),
    ):
        value = props.get(name)
        if not isinstance(value, int | float) or not least <= value <= most:
            report(f"{name} must be set, from {least} to {most} {unit}")


def _logged(args: StackValidationArgs, report: Report) -> None:
    """Every function's log group exists with retention, so Lambda never makes one that keeps
    logs forever."""
    retained = {
        r.props.get("name")
        for r in args.resources
        if r.resource_type == LOG_GROUP and r.props.get("retentionInDays")
    }
    for resource in args.resources:
        if resource.resource_type == FUNCTION:
            name = resource.props.get("name")
            if f"/aws/lambda/{name}" not in retained:
                report(f"{name} needs a log group /aws/lambda/{name} with retention")


def _least_privilege(args: ResourceValidationArgs, report: Report) -> None:
    if args.resource_type not in IAM_DOCUMENTS:
        return
    try:
        statements = json.loads(args.props.get("policy") or "{}").get("Statement") or []
    except (ValueError, AttributeError):
        report("the policy is not a JSON policy document")
        return
    for statement in statements:
        if not isinstance(statement, dict) or statement.get("Effect") != "Allow":
            continue  # a Deny only narrows
        actions = statement.get("Action")
        actions = [actions] if isinstance(actions, str) else list(actions or [])
        resources = statement.get("Resource")
        for action in actions:
            if action not in DEPLOYMENT_ACTIONS:
                report(f"{action} is not an action the Deployment uses")
            elif resources in ("*", ["*"]):
                report(f"{action} is allowed on every resource")
            elif args.name.endswith(API_ROLE) and action in WRITES:
                report(f"the API role is read-only: {action} is a write")


def _budget_alerts(props: dict[str, Any], report: Report) -> None:
    kinds = {n.get("notificationType") for n in props.get("notifications") or []}
    if "FORECASTED" not in kinds:
        report("the Budget must alert on forecast spend as well as actual spend")
    if "ACTUAL" not in kinds:
        report("the Budget must alert on actual spend as well as forecast spend")


def _not_ruled_out(args: ResourceValidationArgs, report: Report) -> None:
    if args.resource_type.startswith(RULED_OUT):
        report(f"{args.resource_type} is ruled out by spec 05 (no VPC, NAT, RDS, EKS, ECS or EFS)")


S3_PUBLIC_ACCESS_BLOCKED = ResourceValidationPolicy(
    "s3-public-access-blocked",
    "Every S3 public access block blocks and ignores all public access.",
    _for(PUBLIC_ACCESS_BLOCK, _public_access_blocked),
    enforcement_level=MANDATORY,
)
S3_ENCRYPTED = ResourceValidationPolicy(
    "s3-encrypted",
    "Every bucket encryption configuration sets AES256 or aws:kms by default.",
    _for(ENCRYPTION, _encrypted),
    enforcement_level=MANDATORY,
)
S3_VERSIONED = ResourceValidationPolicy(
    "s3-versioned",
    "Every bucket versioning configuration is Enabled.",
    _for(VERSIONING, _versioned),
    enforcement_level=MANDATORY,
)
S3_TLS_ONLY = ResourceValidationPolicy(
    "s3-tls-only",
    "Every bucket policy denies requests made without TLS (aws:SecureTransport false).",
    _for(BUCKET_POLICY, _tls_only),
    enforcement_level=MANDATORY,
)
S3_GUARDED = StackValidationPolicy(
    "s3-guarded",
    "Every bucket has a public access block, default encryption, versioning and a bucket policy.",
    _guarded,
    enforcement_level=MANDATORY,
)
NO_RULED_OUT_RESOURCES = ResourceValidationPolicy(
    "no-ruled-out-resources",
    "No VPC, subnet, NAT gateway, EIP, RDS, EKS, ECS or EFS resource (spec 05).",
    _not_ruled_out,
    enforcement_level=MANDATORY,
)

URL_IAM_ONLY = ResourceValidationPolicy(
    "url-iam-only",
    "Every Lambda function URL uses AWS_IAM authorization (ADR 0026).",
    _for(FUNCTION_URL, _url_iam_only),
    enforcement_level=MANDATORY,
)
FUNCTION_BOUNDED = ResourceValidationPolicy(
    "function-bounded",
    "Every Lambda function sets its timeout and memory, within spec 08's bounds.",
    _for(FUNCTION, _bounded),
    enforcement_level=MANDATORY,
)
FUNCTION_LOGGED = StackValidationPolicy(
    "function-logged",
    "Every Lambda function has its log group, /aws/lambda/<name>, with retention set.",
    _logged,
    enforcement_level=MANDATORY,
)
IAM_LEAST_PRIVILEGE = ResourceValidationPolicy(
    "iam-least-privilege",
    "IAM allows only the Deployment's own actions, never on every resource, and the API role"
    " never writes (spec 08 §IAM).",
    _least_privilege,
    enforcement_level=MANDATORY,
)
BUDGET_ALERTS = ResourceValidationPolicy(
    "budget-alerts",
    "A configured Budget alerts on both forecast and actual spend.",
    _for(BUDGET, _budget_alerts),
    enforcement_level=MANDATORY,
)

POLICIES = [
    S3_PUBLIC_ACCESS_BLOCKED,
    S3_ENCRYPTED,
    S3_VERSIONED,
    S3_TLS_ONLY,
    S3_GUARDED,
    URL_IAM_ONLY,
    FUNCTION_BOUNDED,
    FUNCTION_LOGGED,
    IAM_LEAST_PRIVILEGE,
    NO_RULED_OUT_RESOURCES,
    BUDGET_ALERTS,
]
