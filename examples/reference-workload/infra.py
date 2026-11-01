"""Reference workload infrastructure (ADR 0021): S3, a Glue database and an Athena workgroup.

A test harness for validating data-finops against real billing, not product code.
Optional cost guardrails are per-installation settings, never hard-coded.
"""

from dataclasses import dataclass
from decimal import Decimal

import pulumi
import pulumi_aws as aws

# Athena's per-query minimum is 10 MB; 100 MiB is ample for the tiny models.
DEFAULT_SCAN_CUTOFF_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    bytes_scanned_cutoff_per_query: int = DEFAULT_SCAN_CUTOFF_BYTES
    budget_monthly_usd: Decimal | None = None
    budget_email: str | None = None

    def __post_init__(self) -> None:
        if (self.budget_monthly_usd is None) != (self.budget_email is None):
            raise ValueError("a budget needs both budget_monthly_usd and budget_email")

    @classmethod
    def from_config(cls, config: pulumi.Config) -> "Settings":
        amount = config.get("budgetMonthlyUsd")
        return cls(
            bytes_scanned_cutoff_per_query=config.get_int("bytesScannedCutoffPerQuery")
            or DEFAULT_SCAN_CUTOFF_BYTES,
            budget_monthly_usd=Decimal(amount) if amount else None,
            budget_email=config.get("budgetEmail"),
        )


@dataclass(frozen=True)
class Stack:
    bucket_name: pulumi.Output[str]
    database_name: pulumi.Output[str]
    workgroup_name: pulumi.Output[str]
    resource_ids: pulumi.Output[list[str]]
    """Settles once every resource in the stack is registered."""


def build(settings: Settings) -> Stack:
    resources: list[pulumi.CustomResource] = []
    bucket = aws.s3.Bucket("reference-workload", force_destroy=True)
    resources.append(bucket)
    block = aws.s3.BucketPublicAccessBlock(
        "reference-workload",
        bucket=bucket.id,
        block_public_acls=True,
        block_public_policy=True,
        ignore_public_acls=True,
        restrict_public_buckets=True,
    )
    resources.append(block)
    ownership = aws.s3.BucketOwnershipControls(
        "reference-workload",
        bucket=bucket.id,
        rule=aws.s3.BucketOwnershipControlsRuleArgs(object_ownership="BucketOwnerEnforced"),
    )
    resources.append(ownership)
    encryption = aws.s3.BucketServerSideEncryptionConfiguration(
        "reference-workload",
        bucket=bucket.id,
        rules=[
            aws.s3.BucketServerSideEncryptionConfigurationRuleArgs(
                apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultArgs(
                    sse_algorithm="AES256"
                )
            )
        ],
    )
    resources.append(encryption)
    database = aws.glue.CatalogDatabase("reference-workload", name="reference_workload")
    resources.append(database)
    workgroup = aws.athena.Workgroup(
        "reference-workload",
        name="data-finops-reference",
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
    )
    resources.append(workgroup)
    if settings.budget_monthly_usd is not None and settings.budget_email is not None:
        budget = aws.budgets.Budget(
            "reference-workload",
            budget_type="COST",
            limit_amount=str(settings.budget_monthly_usd),
            limit_unit="USD",
            time_unit="MONTHLY",
            notifications=[
                aws.budgets.BudgetNotificationArgs(
                    comparison_operator="GREATER_THAN",
                    notification_type="ACTUAL",
                    threshold=100,
                    threshold_type="PERCENTAGE",
                    subscriber_email_addresses=[settings.budget_email],
                )
            ],
        )
        resources.append(budget)
    return Stack(
        bucket_name=bucket.bucket,
        database_name=database.name,
        workgroup_name=workgroup.name,
        resource_ids=pulumi.Output.all(*[r.id for r in resources]),
    )
