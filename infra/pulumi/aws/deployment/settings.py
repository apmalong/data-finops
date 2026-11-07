"""The Deployment's settings, from the stack's configuration (spec 08 §Settings).

Every setting is validated when `Settings` is made, however it is made, before any resource is
declared; an error names the setting as the stack configuration names it, never its value.
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

BOUNDS: dict[str, tuple[str, int, int]] = {
    "billing_lookback_periods": ("billingLookbackPeriods", 1, 24),
    "max_history_pages": ("maxHistoryPages", 1, 1000),
    "collector_timeout_seconds": ("collectorTimeoutSeconds", 1, 900),
    "collector_memory_mb": ("collectorMemoryMb", 128, 10240),
    "api_timeout_seconds": ("apiTimeoutSeconds", 1, 900),
    "api_memory_mb": ("apiMemoryMb", 128, 10240),
    "noncurrent_version_days": ("noncurrentVersionDays", 1, 3650),
    "inbox_retention_days": ("inboxRetentionDays", 1, 3650),
}
"""Each whole-number setting: its configuration name and inclusive bounds (spec 08; the first
two match the collector's own, `data_finops.collection.run`)."""


class SettingsError(ValueError):
    """A setting is missing or out of bounds; the message names it, never its value."""


@dataclass(frozen=True)
class Budget:
    """An account-wide monthly cost alert: AWS Budgets cover the whole account."""

    monthly_usd: Decimal
    email: str

    def __post_init__(self) -> None:
        if self.monthly_usd <= 0:
            raise SettingsError("budgetMonthlyUsd must be positive")
        if not self.email:
            raise SettingsError("budgetEmail is required with budgetMonthlyUsd")


@dataclass(frozen=True)
class Settings:
    region: str
    export_bucket: str
    export_name: str
    workgroups: tuple[str, ...]
    export_prefix: str = ""
    billing_lookback_periods: int = 2
    max_history_pages: int = 200
    schedule: str = DEFAULT_SCHEDULE
    collector_timeout_seconds: int = 900
    collector_memory_mb: int = 2048
    api_timeout_seconds: int = 30
    api_memory_mb: int = 1024
    api_reserved_concurrency: int | None = None
    log_retention_days: int = 30
    noncurrent_version_days: int = 30
    inbox_retention_days: int = 30
    kms_key_arn: str | None = None
    explanation_detail: str = DEFAULT_EXPLANATION_DETAIL
    budget: Budget | None = None
    alarm_email: str | None = None
    delete_data_on_destroy: bool = False

    def __post_init__(self) -> None:
        for field, name in (
            ("region", "aws:region"),
            ("export_bucket", "exportBucket"),
            ("export_name", "exportName"),
        ):
            if not getattr(self, field):
                raise SettingsError(f"{name} is required")
        if not self.workgroups or not all(self.workgroups):
            raise SettingsError("workgroups must be a non-empty list of Athena workgroup names")
        for field, (name, least, most) in BOUNDS.items():
            if not least <= getattr(self, field) <= most:
                raise SettingsError(f"{name} must be a whole number from {least} to {most}")
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
        given: dict[str, Any] = {
            "region": aws_config.get("region") or "",
            "export_bucket": config.get("exportBucket") or "",
            "export_name": config.get("exportName") or "",
            "workgroups": _workgroups(config.get("workgroups")),
            "export_prefix": config.get("exportPrefix") or "",
            "schedule": config.get("schedule") or DEFAULT_SCHEDULE,
            "kms_key_arn": config.get("kmsKeyArn") or None,
            "explanation_detail": config.get("explanationDetail") or DEFAULT_EXPLANATION_DETAIL,
            "budget": _budget(config.get("budgetMonthlyUsd"), config.get("budgetEmail")),
            "alarm_email": config.get("alarmEmail") or None,
            "delete_data_on_destroy": _flag(config, "deleteDataOnDestroy"),
        }
        whole_numbers = {
            **{field: name for field, (name, _, _) in BOUNDS.items()},
            "api_reserved_concurrency": "apiReservedConcurrency",
            "log_retention_days": "logRetentionDays",
        }
        for field, name in whole_numbers.items():
            if (value := config.get(name)) is not None:
                given[field] = _whole_number(value, name)
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


def _flag(config: pulumi.Config, name: str) -> bool:
    value = (config.get(name) or "false").lower()
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
