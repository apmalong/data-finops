"""The Deployment's settings, from the stack's configuration (spec 08 §Settings).

Every setting is validated before any resource is declared; an error names the setting, never
its value. Defaults are spec 08's. Settings the collector also reads at run time (the export,
the workgroups, the lookback and history pages) are checked again by `data_finops` itself.
"""

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import pulumi

LOG_RETENTION_DAYS = frozenset(
    {1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557,
     2922, 3288, 3653}
)  # fmt: skip
"""The retention periods CloudWatch Logs accepts (PutRetentionPolicy)."""


class SettingsError(ValueError):
    """A setting is missing or out of bounds; the message names it, never its value."""


@dataclass(frozen=True)
class Budget:
    """An account-wide monthly cost alert: AWS Budgets cover the whole account."""

    monthly_usd: Decimal
    email: str


@dataclass(frozen=True)
class Settings:
    region: str
    export_bucket: str
    export_name: str
    workgroups: tuple[str, ...]
    export_prefix: str = ""
    billing_lookback_periods: int = 2
    max_history_pages: int = 200
    schedule: str = "cron(0 6 * * ? *)"
    collector_timeout_seconds: int = 900
    collector_memory_mb: int = 2048
    api_timeout_seconds: int = 30
    api_memory_mb: int = 1024
    api_reserved_concurrency: int | None = None
    log_retention_days: int = 30
    noncurrent_version_days: int = 30
    inbox_retention_days: int = 30
    kms_key_arn: str | None = None
    explanation_detail: str = "minimal"
    budget: Budget | None = None
    alarm_email: str | None = None
    delete_data_on_destroy: bool = False

    @classmethod
    def from_config(cls, config: pulumi.Config, aws_config: pulumi.Config) -> "Settings":
        """The settings from the stack's `data-finops:*` and `aws:region` configuration."""
        return cls(
            region=_required(aws_config.get("region"), "aws:region"),
            export_bucket=_required(config.get("exportBucket"), "exportBucket"),
            export_name=_required(config.get("exportName"), "exportName"),
            workgroups=_workgroups(config.get("workgroups")),
            export_prefix=config.get("exportPrefix") or "",
            billing_lookback_periods=_number(config, "billingLookbackPeriods", 2, 1, 24),
            max_history_pages=_number(config, "maxHistoryPages", 200, 1, 1000),
            schedule=config.get("schedule") or "cron(0 6 * * ? *)",
            collector_timeout_seconds=_number(config, "collectorTimeoutSeconds", 900, 1, 900),
            collector_memory_mb=_number(config, "collectorMemoryMb", 2048, 128, 10240),
            api_timeout_seconds=_number(config, "apiTimeoutSeconds", 30, 1, 900),
            api_memory_mb=_number(config, "apiMemoryMb", 1024, 128, 10240),
            api_reserved_concurrency=_optional_number(config, "apiReservedConcurrency", 0, 1000),
            log_retention_days=_retention(config),
            noncurrent_version_days=_number(config, "noncurrentVersionDays", 30, 1, 3650),
            inbox_retention_days=_number(config, "inboxRetentionDays", 30, 1, 3650),
            kms_key_arn=_kms_key(config.get("kmsKeyArn")),
            explanation_detail=_choice(config, "explanationDetail", "minimal", {"minimal", "full"}),
            budget=_budget(config.get("budgetMonthlyUsd"), config.get("budgetEmail")),
            alarm_email=config.get("alarmEmail") or None,
            delete_data_on_destroy=_flag(config, "deleteDataOnDestroy"),
        )


def _required(value: str | None, name: str) -> str:
    if not value:
        raise SettingsError(f"{name} is required")
    return value


def _workgroups(value: str | None) -> tuple[str, ...]:
    try:
        names: Any = json.loads(value) if value else None
    except ValueError:
        names = None
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names):
        raise SettingsError("workgroups must be a non-empty list of Athena workgroup names")
    return tuple(names)


def _optional_number(config: pulumi.Config, name: str, least: int, most: int) -> int | None:
    value = config.get(name)
    if value is None:
        return None
    try:
        number = int(value)
    except ValueError:
        number = least - 1
    if not least <= number <= most:
        raise SettingsError(f"{name} must be a whole number from {least} to {most}")
    return number


def _number(config: pulumi.Config, name: str, default: int, least: int, most: int) -> int:
    number = _optional_number(config, name, least, most)
    return default if number is None else number


def _retention(config: pulumi.Config) -> int:
    days = _number(config, "logRetentionDays", 30, 1, 3653)
    if days not in LOG_RETENTION_DAYS:
        raise SettingsError("logRetentionDays must be a retention CloudWatch Logs accepts")
    return days


def _kms_key(value: str | None) -> str | None:
    if value and not value.startswith("arn:aws:kms:"):
        raise SettingsError("kmsKeyArn must be a KMS key ARN")
    return value or None


def _choice(config: pulumi.Config, name: str, default: str, choices: set[str]) -> str:
    value = config.get(name) or default
    if value not in choices:
        raise SettingsError(f"{name} must be one of {', '.join(sorted(choices))}")
    return value


def _flag(config: pulumi.Config, name: str) -> bool:
    value = (config.get(name) or "false").lower()
    if value not in ("true", "false"):
        raise SettingsError(f"{name} must be true or false")
    return value == "true"


def _budget(amount: str | None, email: str | None) -> Budget | None:
    if amount is None and email is None:
        return None
    if not email:
        raise SettingsError("budgetEmail is required with budgetMonthlyUsd")
    if not amount:
        raise SettingsError("budgetMonthlyUsd is required with budgetEmail")
    try:
        monthly = Decimal(amount)
    except InvalidOperation:
        raise SettingsError("budgetMonthlyUsd must be a number") from None
    if monthly <= 0:
        raise SettingsError("budgetMonthlyUsd must be positive")
    return Budget(monthly_usd=monthly, email=email)
