"""Reference workload infrastructure (ADR 0021): S3, a Glue database and an Athena workgroup.

A test harness for validating data-finops against real billing, not product code.
Optional cost guardrails are per-installation settings, never hard-coded.
"""

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import pulumi
import pulumi_aws as aws

NAME = "reference-workload"
REGION = "us-east-1"  # matches the FOCUS export (#21)
WORKGROUP = "data-finops-reference"  # also named in dbt/profiles.yml
DATABASE = "reference_workload"  # the dbt profile's schema
ATHENA_MIN_CUTOFF_BYTES = 10 * 1024 * 1024  # Athena rejects lower per-query limits
DEFAULT_SCAN_CUTOFF_BYTES = 100 * 1024 * 1024  # ample for the tiny models


@dataclass(frozen=True)
class Budget:
    """An account-wide monthly cost alert; AWS Budgets here are not scoped to the workload."""

    monthly_usd: Decimal
    email: str


@dataclass(frozen=True)
class Settings:
    bytes_scanned_cutoff_per_query: int = DEFAULT_SCAN_CUTOFF_BYTES
    budget: Budget | None = None
    aws_profile: str | None = None

    def __post_init__(self) -> None:
        if self.bytes_scanned_cutoff_per_query < ATHENA_MIN_CUTOFF_BYTES:
            raise ValueError(
                f"bytesScannedCutoffPerQuery must be at least {ATHENA_MIN_CUTOFF_BYTES} bytes"
            )

    @classmethod
    def from_config(cls, config: pulumi.Config, aws_config: pulumi.Config) -> "Settings":
        cutoff = config.get_int("bytesScannedCutoffPerQuery")
        return cls(
            bytes_scanned_cutoff_per_query=DEFAULT_SCAN_CUTOFF_BYTES if cutoff is None else cutoff,
            budget=_budget_from(config.get("budgetMonthlyUsd"), config.get("budgetEmail")),
            aws_profile=aws_config.get("profile"),
        )


def _budget_from(amount: str | None, email: str | None) -> Budget | None:
    if amount is None and email is None:
        return None
    if not amount or not email:
        raise ValueError("set both budgetMonthlyUsd and budgetEmail, or neither")
    try:
        monthly = Decimal(amount)
    except InvalidOperation:
        raise ValueError(f"budgetMonthlyUsd must be a number, got {amount!r}") from None
    if monthly <= 0:
        raise ValueError("budgetMonthlyUsd must be positive")
    return Budget(monthly_usd=monthly, email=email)


def require_local_state() -> None:
    """Refuse to run against a hosted state backend (ADR 0001, #21)."""
    if not os.environ.get("PULUMI_BACKEND_URL", "").startswith("file://"):
        raise RuntimeError("set PULUMI_BACKEND_URL to a file:// backend under .local/ (see README)")


@dataclass(frozen=True)
class WorkloadOutputs:
    bucket_name: pulumi.Output[str]
    database_name: pulumi.Output[str]
    workgroup_name: pulumi.Output[str]
    resource_ids: pulumi.Output[list[str]]
    """Settles once every resource is registered."""


def build(settings: Settings) -> WorkloadOutputs:
    provider = aws.Provider(NAME, region=REGION, profile=settings.aws_profile)
    opts = pulumi.ResourceOptions(provider=provider)
    bucket, bucket_guards = _private_bucket(opts)
    database = aws.glue.CatalogDatabase(NAME, name=DATABASE, opts=opts)
    workgroup = _workgroup(bucket, settings, opts)
    resources = [bucket, *bucket_guards, database, workgroup]
    if settings.budget is not None:
        resources.append(_budget_alert(settings.budget, opts))
    return WorkloadOutputs(
        bucket_name=bucket.bucket,
        database_name=database.name,
        workgroup_name=workgroup.name,
        resource_ids=pulumi.Output.all(*[r.id for r in resources]),
    )


def _private_bucket(
    opts: pulumi.ResourceOptions,
) -> tuple[aws.s3.Bucket, list[pulumi.CustomResource]]:
    bucket = aws.s3.Bucket(NAME, force_destroy=True, opts=opts)
    guards: list[pulumi.CustomResource] = [
        aws.s3.BucketPublicAccessBlock(
            NAME,
            bucket=bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=opts,
        ),
        aws.s3.BucketOwnershipControls(
            NAME,
            bucket=bucket.id,
            rule=aws.s3.BucketOwnershipControlsRuleArgs(object_ownership="BucketOwnerEnforced"),
            opts=opts,
        ),
        aws.s3.BucketServerSideEncryptionConfiguration(
            NAME,
            bucket=bucket.id,
            rules=[
                aws.s3.BucketServerSideEncryptionConfigurationRuleArgs(
                    apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultArgs(
                        sse_algorithm="AES256"
                    )
                )
            ],
            opts=opts,
        ),
    ]
    return bucket, guards


def _workgroup(
    bucket: aws.s3.Bucket, settings: Settings, opts: pulumi.ResourceOptions
) -> aws.athena.Workgroup:
    return aws.athena.Workgroup(
        NAME,
        name=WORKGROUP,
        force_destroy=True,
        configuration=aws.athena.WorkgroupConfigurationArgs(
            bytes_scanned_cutoff_per_query=settings.bytes_scanned_cutoff_per_query,
            enforce_workgroup_configuration=True,
            publish_cloudwatch_metrics_enabled=False,
            result_configuration=aws.athena.WorkgroupConfigurationResultConfigurationArgs(
                output_location=bucket.bucket.apply(lambda name: f"s3://{name}/athena-results/"),
                encryption_configuration=aws.athena.WorkgroupConfigurationResultConfigurationEncryptionConfigurationArgs(
                    encryption_option="SSE_S3"
                ),
            ),
        ),
        opts=opts,
    )


def _budget_alert(budget: Budget, opts: pulumi.ResourceOptions) -> aws.budgets.Budget:
    return aws.budgets.Budget(
        NAME,
        budget_type="COST",
        limit_amount=str(budget.monthly_usd),
        limit_unit="USD",
        time_unit="MONTHLY",
        notifications=[
            aws.budgets.BudgetNotificationArgs(
                comparison_operator="GREATER_THAN",
                notification_type=kind,
                threshold=100,
                threshold_type="PERCENTAGE",
                subscriber_email_addresses=[budget.email],
            )
            for kind in ("FORECASTED", "ACTUAL")
        ],
        opts=opts,
    )
