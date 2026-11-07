"""The Deployment's mandatory policies (spec 08 §Policy pack): policy 1 (S3) and policy 5 (what
spec 05 rules out). A plain module: only `__main__.py` builds the pack, because constructing
`PolicyPack` starts its server and blocks.

Each policy reads inputs a program sets as literals. A value unknown at preview (computed from
another resource's outputs) only yields Pulumi's advisory "can't run policy during preview";
the TLS-only bucket policy, computed from the bucket's ARN, is therefore checked where it is
known: `pulumi policy analyze` over the program's recorded state (spec 08 seam 7b).
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

POLICIES = [
    S3_PUBLIC_ACCESS_BLOCKED,
    S3_ENCRYPTED,
    S3_VERSIONED,
    S3_TLS_ONLY,
    S3_GUARDED,
    NO_RULED_OUT_RESOURCES,
]
