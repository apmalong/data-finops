"""The stack's settings (spec 08 §Settings; seam 6: settings validation)."""

import dataclasses
from decimal import Decimal
from typing import Any

import pulumi
import pytest
from deployment.settings import Budget, Settings, SettingsError

REQUIRED = {
    "aws:region": "us-east-1",
    "data-finops:exportBucket": "example-export-bucket",
    "data-finops:exportName": "example-focus-1-2",
    "data-finops:workgroups": '["demo"]',
}


def _from_config(values: dict[str, str]) -> Settings:
    pulumi.runtime.set_all_config(values)
    return Settings.from_config(pulumi.Config("data-finops"), pulumi.Config("aws"))


def test_the_defaults_are_spec_08s() -> None:
    settings = _from_config(REQUIRED)
    assert (settings.region, settings.export_bucket, settings.export_prefix) == (
        "us-east-1",
        "example-export-bucket",
        "",
    )
    assert settings.workgroups == ("demo",)
    assert (settings.billing_lookback_periods, settings.max_history_pages) == (2, 200)
    assert settings.schedule == "cron(0 6 * * ? *)"
    assert (settings.collector_timeout_seconds, settings.collector_memory_mb) == (900, 2048)
    assert (settings.api_timeout_seconds, settings.api_memory_mb) == (30, 1024)
    assert settings.api_reserved_concurrency is None
    assert (settings.log_retention_days, settings.noncurrent_version_days) == (30, 30)
    assert settings.inbox_retention_days == 30
    assert (settings.kms_key_arn, settings.budget, settings.alarm_email) == (None, None, None)
    assert (settings.explanation_detail, settings.delete_data_on_destroy) == ("minimal", False)


def test_every_setting_can_be_given() -> None:
    settings = _from_config(
        REQUIRED
        | {
            "data-finops:exportPrefix": "focus",
            "data-finops:billingLookbackPeriods": "3",
            "data-finops:maxHistoryPages": "500",
            "data-finops:schedule": "cron(30 5 * * ? *)",
            "data-finops:collectorTimeoutSeconds": "600",
            "data-finops:collectorMemoryMb": "3008",
            "data-finops:apiTimeoutSeconds": "20",
            "data-finops:apiMemoryMb": "512",
            "data-finops:apiReservedConcurrency": "2",
            "data-finops:logRetentionDays": "14",
            "data-finops:noncurrentVersionDays": "7",
            "data-finops:inboxRetentionDays": "10",
            "data-finops:kmsKeyArn": "arn:aws:kms:us-east-1:123456789012:key/example",
            "data-finops:explanationDetail": "full",
            "data-finops:budgetMonthlyUsd": "25",
            "data-finops:budgetEmail": "owner@example.com",
            "data-finops:alarmEmail": "owner@example.com",
            "data-finops:deleteDataOnDestroy": "true",
        }
    )
    assert settings.export_prefix == "focus"
    assert settings.budget == Budget(monthly_usd=Decimal("25"), email="owner@example.com")
    assert (settings.api_reserved_concurrency, settings.delete_data_on_destroy) == (2, True)
    assert settings.kms_key_arn == "arn:aws:kms:us-east-1:123456789012:key/example"


@pytest.mark.parametrize(
    ("change", "names"),
    [
        ({"data-finops:exportBucket": None}, "exportBucket"),
        ({"data-finops:exportName": None}, "exportName"),
        ({"data-finops:workgroups": None}, "workgroups"),
        ({"data-finops:workgroups": "[]"}, "workgroups"),
        ({"aws:region": None}, "aws:region"),
        ({"data-finops:billingLookbackPeriods": "25"}, "billingLookbackPeriods"),
        ({"data-finops:maxHistoryPages": "0"}, "maxHistoryPages"),
        ({"data-finops:collectorTimeoutSeconds": "901"}, "collectorTimeoutSeconds"),
        ({"data-finops:collectorMemoryMb": "64"}, "collectorMemoryMb"),
        ({"data-finops:apiTimeoutSeconds": "0"}, "apiTimeoutSeconds"),
        ({"data-finops:apiReservedConcurrency": "-1"}, "apiReservedConcurrency"),
        ({"data-finops:logRetentionDays": "31"}, "logRetentionDays"),
        ({"data-finops:noncurrentVersionDays": "0"}, "noncurrentVersionDays"),
        ({"data-finops:inboxRetentionDays": "0"}, "inboxRetentionDays"),
        ({"data-finops:kmsKeyArn": "secret-not-an-arn"}, "kmsKeyArn"),
        ({"data-finops:explanationDetail": "everything"}, "explanationDetail"),
        ({"data-finops:budgetMonthlyUsd": "25"}, "budgetEmail"),
        ({"data-finops:budgetEmail": "owner@example.com"}, "budgetMonthlyUsd"),
        (
            {"data-finops:budgetMonthlyUsd": "0", "data-finops:budgetEmail": "o@example.com"},
            "budgetMonthlyUsd",
        ),
    ],
)
def test_a_bad_setting_is_refused_by_name(change: dict[str, Any], names: str) -> None:
    values = {k: v for k, v in (REQUIRED | change).items() if v is not None}
    with pytest.raises(SettingsError, match=names) as raised:
        _from_config(values)
    assert "secret" not in str(raised.value)


def test_settings_are_immutable() -> None:
    settings = _from_config(REQUIRED)
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.region = "eu-west-1"  # type: ignore[misc]
