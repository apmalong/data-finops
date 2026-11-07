"""The Deployment's settings, from the stack's configuration (spec 08 §Settings).

Every setting is validated when `Settings` is made, however it is made, before any resource is
declared; an error names the setting as the stack configuration names it, never its value.
The site's generated deployment settings reference reads this module's source (`CONFIG_KEYS`,
`BOUNDS`, each field's default and docstring) without importing it, so those stay literals.
Defaults are spec 08's. Settings the collector also reads at run time (the export, the
workgroups, the lookback and history pages) are checked again by `data_finops` itself, with the
same bounds.
"""

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import pulumi

DEFAULT_SCHEDULE = "cron(0 6 * * ? *)"
DEFAULT_EXPLANATION_DETAIL = "minimal"
LOG_RETENTION_DAYS = frozenset(
    {1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557,
     2922, 3288, 3653}
)  # fmt: skip
"""The retention periods CloudWatch Logs accepts (PutRetentionPolicy)."""
KMS_KEY_ARN = re.compile(r"arn:aws[a-z-]*:kms:")
"""A KMS key ARN in any partition (aws, aws-us-gov, aws-cn)."""

CONFIG_KEYS: dict[str, str] = {
    "region": "aws:region",
    "export_bucket": "exportBucket",
    "export_name": "exportName",
    "workgroups": "workgroups",
    "export_prefix": "exportPrefix",
    "billing_lookback_periods": "billingLookbackPeriods",
    "max_history_pages": "maxHistoryPages",
    "schedule": "schedule",
    "collector_timeout_seconds": "collectorTimeoutSeconds",
    "collector_memory_mb": "collectorMemoryMb",
    "api_timeout_seconds": "apiTimeoutSeconds",
    "api_memory_mb": "apiMemoryMb",
    "api_reserved_concurrency": "apiReservedConcurrency",
    "log_retention_days": "logRetentionDays",
    "noncurrent_version_days": "noncurrentVersionDays",
    "inbox_retention_days": "inboxRetentionDays",
    "kms_key_arn": "kmsKeyArn",
    "explanation_detail": "explanationDetail",
    "budget.monthly_usd": "budgetMonthlyUsd",
    "budget.email": "budgetEmail",
    "alarm_email": "alarmEmail",
    "delete_data_on_destroy": "deleteDataOnDestroy",
}
"""Each setting's stack configuration key (`data-finops:<key>`, or `aws:region`); a Budget
field is `budget.<field>`."""
BOUNDS: dict[str, tuple[int, int]] = {
    "billing_lookback_periods": (1, 24),
    "max_history_pages": (1, 1000),
    "collector_timeout_seconds": (1, 900),
    "collector_memory_mb": (128, 10240),
    "api_timeout_seconds": (1, 900),
    "api_memory_mb": (128, 10240),
    "noncurrent_version_days": (1, 3650),
    "inbox_retention_days": (1, 3650),
}
"""Each whole-number setting's inclusive bounds (spec 08; the first two match the collector's
own, `data_finops.collection.run`)."""


class SettingsError(ValueError):
    """A setting is missing or out of bounds; the message names it, never its value."""


@dataclass(frozen=True)
class Budget:
    """An account-wide monthly cost alert: AWS Budgets cover the whole account."""

    monthly_usd: Decimal
    """An optional monthly amount, in US dollars, for an account-wide AWS Budget that alerts on
    forecast and actual spend. AWS Budgets cover the whole account, not just the Deployment.
    Give it with `budgetEmail`, or neither."""
    email: str
    """The address the Budget alerts are sent to. Give it with `budgetMonthlyUsd`, or
    neither."""

    def __post_init__(self) -> None:
        if self.monthly_usd <= 0:
            raise SettingsError("budgetMonthlyUsd must be positive")
        if not self.email:
            raise SettingsError("budgetEmail is required with budgetMonthlyUsd")


@dataclass(frozen=True)
class Settings:
    region: str
    """The AWS Region to deploy to. The collector reads the Athena workgroups in this
    Region."""
    export_bucket: str
    """The S3 bucket the AWS Data Exports FOCUS export is delivered to. The collector only
    reads it."""
    export_name: str
    """The name of the FOCUS export, as AWS Data Exports names it."""
    workgroups: tuple[str, ...]
    """The Athena workgroups whose query history is collected, as a JSON list of names, for
    example `["primary"]`. It may not be empty."""
    export_prefix: str = ""
    """The S3 path prefix the export is delivered under; empty when it has none."""
    billing_lookback_periods: int = 2
    """How many Billing Periods, counting back from the current one, each Collection Run
    lists."""
    max_history_pages: int = 200
    """How many `ListQueryExecutions` pages each Collection Run reads, per workgroup."""
    schedule: str = DEFAULT_SCHEDULE
    """When the Collection Run starts, as an EventBridge Scheduler expression, in UTC."""
    collector_timeout_seconds: int = 900
    """The collector function's timeout, in seconds."""
    collector_memory_mb: int = 2048
    """The collector function's memory, in MB."""
    api_timeout_seconds: int = 30
    """The API function's timeout, in seconds."""
    api_memory_mb: int = 1024
    """The API function's memory, in MB."""
    api_reserved_concurrency: int | None = None
    """An optional cap on the API function's concurrent requests, a whole number of at least
    0; 0 switches the API off. New accounts may lack the concurrency quota to reserve any."""
    log_retention_days: int = 30
    """How long both functions' logs are kept, in days: a period CloudWatch Logs accepts,
    such as 1, 7, 14, 30, 90 or 365."""
    noncurrent_version_days: int = 30
    """How long an old version of the database object is kept, in days, before S3 expires
    it."""
    inbox_retention_days: int = 30
    """How long an object stays in the Inbox, in days, before S3 expires it."""
    kms_key_arn: str | None = None
    """An optional customer-managed KMS key ARN to encrypt the bucket with, at about $1 a
    month. Unset, the bucket uses the S3-managed key."""
    explanation_detail: str = DEFAULT_EXPLANATION_DETAIL
    """How much the API's Cost Explanations show: `minimal` or `full` (ADR 0028)."""
    budget: Budget | None = None
    """An optional account-wide Budget, from `budgetMonthlyUsd` and `budgetEmail`."""
    alarm_email: str | None = None
    """An optional address to send an alarm to when the collector function reports errors,
    through an SNS topic."""
    delete_data_on_destroy: bool = False
    """Whether `pulumi destroy` deletes the bucket and its data. By default the bucket is kept,
    with the database and the Inbox in it."""

    def __post_init__(self) -> None:
        for field in ("region", "export_bucket", "export_name"):
            if not getattr(self, field):
                raise SettingsError(f"{CONFIG_KEYS[field]} is required")
        if not self.workgroups or not all(self.workgroups):
            raise SettingsError("workgroups must be a non-empty list of Athena workgroup names")
        for field, (least, most) in BOUNDS.items():
            if not least <= getattr(self, field) <= most:
                raise SettingsError(
                    f"{CONFIG_KEYS[field]} must be a whole number from {least} to {most}"
                )
        if self.api_reserved_concurrency is not None and not 0 <= self.api_reserved_concurrency:
            raise SettingsError("apiReservedConcurrency must be a whole number, at least 0")
        if self.log_retention_days not in LOG_RETENTION_DAYS:
            raise SettingsError("logRetentionDays must be a retention CloudWatch Logs accepts")
        if self.kms_key_arn is not None and not KMS_KEY_ARN.match(self.kms_key_arn):
            raise SettingsError("kmsKeyArn must be a KMS key ARN")
        if self.explanation_detail not in ("minimal", "full"):
            raise SettingsError("explanationDetail must be one of full, minimal")

    @classmethod
    def from_config(cls, config: pulumi.Config, aws_config: pulumi.Config) -> "Settings":
        """The settings from the stack's `data-finops:*` and `aws:region` configuration; a
        setting left out takes the dataclass's default."""

        def get(field: str) -> str | None:
            name = CONFIG_KEYS[field]
            if name.startswith("aws:"):
                return aws_config.get(name.removeprefix("aws:"))
            return config.get(name)

        given: dict[str, Any] = {
            "region": get("region") or "",
            "export_bucket": get("export_bucket") or "",
            "export_name": get("export_name") or "",
            "workgroups": _workgroups(get("workgroups")),
            "export_prefix": get("export_prefix") or "",
            "schedule": get("schedule") or DEFAULT_SCHEDULE,
            "kms_key_arn": get("kms_key_arn") or None,
            "explanation_detail": get("explanation_detail") or DEFAULT_EXPLANATION_DETAIL,
            "budget": _budget(get("budget.monthly_usd"), get("budget.email")),
            "alarm_email": get("alarm_email") or None,
            "delete_data_on_destroy": _flag(
                get("delete_data_on_destroy"), CONFIG_KEYS["delete_data_on_destroy"]
            ),
        }
        for field in (*BOUNDS, "api_reserved_concurrency", "log_retention_days"):
            if (value := get(field)) is not None:
                given[field] = _whole_number(value, CONFIG_KEYS[field])
        return cls(**given)


def _workgroups(value: str | None) -> tuple[str, ...]:
    try:
        names: Any = json.loads(value) if value else []
    except ValueError:
        raise SettingsError("workgroups must be a JSON list of names") from None
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise SettingsError("workgroups must be a JSON list of names")
    return tuple(names)


def _whole_number(value: str, name: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise SettingsError(f"{name} must be a whole number") from None


def _flag(given: str | None, name: str) -> bool:
    value = (given or "false").lower()
    if value not in ("true", "false"):
        raise SettingsError(f"{name} must be true or false")
    return value == "true"


def _budget(amount: str | None, email: str | None) -> Budget | None:
    if amount is None and email is None:
        return None
    if not amount:
        raise SettingsError("budgetMonthlyUsd is required with budgetEmail")
    try:
        monthly = Decimal(amount)
    except InvalidOperation:
        raise SettingsError("budgetMonthlyUsd must be a number") from None
    return Budget(monthly_usd=monthly, email=email or "")
