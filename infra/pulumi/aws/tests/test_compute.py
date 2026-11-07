"""The Deployment's functions, schedule and IAM through Pulumi's mocks (spec 08 §Topology,
§IAM, §Cost limits; seam 6)."""

import dataclasses
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pulumi
import pytest
from deployment.compute import Compute
from deployment.settings import Budget, Settings
from deployment.storage import Storage

from tests.mocks import MOCKS

ACCOUNT = "123456789012"
SETTINGS = Settings(
    region="us-east-1",
    export_bucket="example-export-bucket",
    export_prefix="focus",
    export_name="example-focus-1-2",
    workgroups=("demo", "etl"),
)
FUNCTION = "aws:lambda/function:Function"
ROLE_POLICY = "aws:iam/rolePolicy:RolePolicy"


@pytest.fixture(autouse=True)
def _fresh_mocks() -> None:
    MOCKS.resources.clear()


def _after(settings: Settings, check: Callable[[], None]) -> pulumi.Output[None]:
    storage = Storage("data-finops", settings)
    compute = Compute(
        "data-finops",
        settings,
        storage,
        package=Path("build/data-finops-lambda.zip"),
        account_id=pulumi.Output.from_input(ACCOUNT),
    )
    return pulumi.Output.all(storage.ready, compute.ready).apply(lambda _: check())


def _function(handler: str) -> dict[str, Any]:
    (function,) = [f.inputs for f in MOCKS.of_type(FUNCTION) if f.inputs["handler"] == handler]
    return function


def _policy_of(role_name: str) -> dict[str, Any]:
    (policy,) = [
        json.loads(p.inputs["policy"])
        for p in MOCKS.of_type(ROLE_POLICY)
        if p.name.endswith(role_name)
    ]
    found: dict[str, Any] = policy
    return found


def _actions(policy: dict[str, Any], effect: str = "Allow") -> dict[str, Any]:
    """Each allowed (or denied) action with the resources it is granted on."""
    granted: dict[str, Any] = {}
    for statement in policy["Statement"]:
        if statement["Effect"] == effect:
            actions = statement["Action"]
            for action in [actions] if isinstance(actions, str) else actions:
                granted[action] = statement["Resource"]
    return granted


@pulumi.runtime.test
def test_the_functions_run_python_3_12_on_arm64_from_the_package() -> pulumi.Output[None]:
    def check() -> None:
        collector = _function("data_finops.lambda_handlers.collector")
        api = _function("data_finops.lambda_handlers.api")
        for function in (collector, api):
            assert (function["runtime"], function["architectures"]) == ("python3.12", ["arm64"])
            assert function["s3Bucket"] == "mock-deployment-bucket"
            assert function["s3Key"] == "lambda/data-finops-lambda.zip"
        assert (collector["timeout"], collector["memorySize"]) == (600, 3008)
        assert (api["timeout"], api["memorySize"]) == (20, 512)
        assert "reservedConcurrentExecutions" not in api

    settings = dataclasses.replace(
        SETTINGS,
        collector_timeout_seconds=600,
        collector_memory_mb=3008,
        api_timeout_seconds=20,
        api_memory_mb=512,
    )
    return _after(settings, check)


@pulumi.runtime.test
def test_the_collector_gets_its_settings_as_the_collect_files_keys() -> pulumi.Output[None]:
    def check() -> None:
        variables = _function("data_finops.lambda_handlers.collector")["environment"]["variables"]
        assert json.loads(variables["DATA_FINOPS_COLLECTOR_SETTINGS"]) == {
            "bucket": "mock-deployment-bucket",
            "region": "us-east-1",
            "account_id": ACCOUNT,
            "workgroups": ["demo", "etl"],
            "export_bucket": "example-export-bucket",
            "export_prefix": "focus",
            "export_name": "example-focus-1-2",
            "billing_lookback_periods": 2,
            "max_history_pages": 200,
        }

    return _after(SETTINGS, check)


@pulumi.runtime.test
def test_the_api_reads_the_bucket_at_its_detail_behind_iam() -> pulumi.Output[None]:
    def check() -> None:
        api = _function("data_finops.lambda_handlers.api")
        assert api["environment"]["variables"] == {
            "DATA_FINOPS_BUCKET": "mock-deployment-bucket",
            "DATA_FINOPS_EXPLANATION_DETAIL": "full",
        }
        assert api["reservedConcurrentExecutions"] == 3
        (url,) = MOCKS.of_type("aws:lambda/functionUrl:FunctionUrl")
        assert url.inputs["authorizationType"] == "AWS_IAM"

    settings = dataclasses.replace(SETTINGS, explanation_detail="full", api_reserved_concurrency=3)
    return _after(settings, check)


@pulumi.runtime.test
def test_the_collector_runs_on_the_schedule_with_bounded_retries() -> pulumi.Output[None]:
    def check() -> None:
        (schedule,) = MOCKS.of_type("aws:scheduler/schedule:Schedule")
        inputs = schedule.inputs
        assert inputs["scheduleExpression"] == "cron(15 4 * * ? *)"
        assert inputs["scheduleExpressionTimezone"] == "UTC"
        assert inputs["flexibleTimeWindow"] == {"mode": "OFF"}
        assert inputs["target"]["retryPolicy"]["maximumRetryAttempts"] == 2
        invoke = _actions(_policy_of("scheduler"))
        assert list(invoke) == ["lambda:InvokeFunction"]

    return _after(dataclasses.replace(SETTINGS, schedule="cron(15 4 * * ? *)"), check)


@pulumi.runtime.test
def test_each_function_logs_to_its_own_group_kept_for_the_retention() -> pulumi.Output[None]:
    def check() -> None:
        groups = {
            g.inputs["name"]: g.inputs for g in MOCKS.of_type("aws:cloudwatch/logGroup:LogGroup")
        }
        names = sorted(f.inputs["name"] for f in MOCKS.of_type(FUNCTION))
        assert sorted(groups) == [f"/aws/lambda/{name}" for name in names]
        assert {g["retentionInDays"] for g in groups.values()} == {14}

    return _after(dataclasses.replace(SETTINGS, log_retention_days=14), check)


@pulumi.runtime.test
def test_the_collector_reads_only_the_export_and_its_workgroups_and_writes_one_key() -> (
    pulumi.Output[None]
):
    def check() -> None:
        policy = _policy_of("collector")
        allowed = _actions(policy)
        assert set(allowed) == {
            "s3:ListBucket",
            "s3:GetObject",
            "s3:PutObject",
            "athena:ListQueryExecutions",
            "athena:BatchGetQueryExecution",
            "logs:CreateLogStream",
            "logs:PutLogEvents",
        }
        assert allowed["s3:PutObject"] == [
            "arn:aws:s3:::mock-deployment-bucket/database/data-finops.sqlite"
        ]
        assert allowed["athena:ListQueryExecutions"] == [
            f"arn:aws:athena:us-east-1:{ACCOUNT}:workgroup/demo",
            f"arn:aws:athena:us-east-1:{ACCOUNT}:workgroup/etl",
        ]
        export_list = next(
            s for s in policy["Statement"]
            if s["Effect"] == "Allow" and s["Resource"] == ["arn:aws:s3:::example-export-bucket"]
        )  # fmt: skip
        assert export_list["Condition"] == {
            "StringLike": {"s3:prefix": ["focus/example-focus-1-2/*"]}
        }
        assert set(_actions(policy, "Deny")) == {
            "athena:StartQueryExecution",
            "athena:StopQueryExecution",
            "athena:GetQueryResults",
            "athena:GetQueryRuntimeStatistics",
        }
        assert not any("*" in action for action in allowed)
        assert not any(resources in ("*", ["*"]) for resources in allowed.values())

    return _after(SETTINGS, check)


@pulumi.runtime.test
def test_the_api_can_only_read_the_database_object() -> pulumi.Output[None]:
    def check() -> None:
        allowed = _actions(_policy_of("api"))
        assert set(allowed) == {
            "s3:GetObject",
            "s3:ListBucket",
            "logs:CreateLogStream",
            "logs:PutLogEvents",
        }
        assert allowed["s3:GetObject"] == [
            "arn:aws:s3:::mock-deployment-bucket/database/data-finops.sqlite"
        ]

    return _after(SETTINGS, check)


@pulumi.runtime.test
def test_a_customer_key_lets_both_functions_use_it() -> pulumi.Output[None]:
    key = f"arn:aws:kms:us-east-1:{ACCOUNT}:key/example"

    def check() -> None:
        assert _actions(_policy_of("collector"))["kms:GenerateDataKey"] == [key]
        assert _actions(_policy_of("api"))["kms:Decrypt"] == [key]

    return _after(dataclasses.replace(SETTINGS, kms_key_arn=key), check)


@pulumi.runtime.test
def test_producers_may_only_put_into_the_inbox_and_readers_only_call_the_url() -> pulumi.Output[
    None
]:
    def check() -> None:
        policies = {
            p.name: json.loads(p.inputs["policy"]) for p in MOCKS.of_type("aws:iam/policy:Policy")
        }
        producer = next(v for k, v in policies.items() if k.endswith("producer"))
        reader = next(v for k, v in policies.items() if k.endswith("reader"))
        assert _actions(producer) == {
            "s3:PutObject": ["arn:aws:s3:::mock-deployment-bucket/inbox/*"]
        }
        ((statement),) = reader["Statement"]
        assert statement["Action"] == ["lambda:InvokeFunctionUrl"]
        assert statement["Condition"] == {"StringEquals": {"lambda:FunctionUrlAuthType": "AWS_IAM"}}

    return _after(SETTINGS, check)


@pulumi.runtime.test
def test_the_alarm_and_the_budget_exist_only_when_configured() -> pulumi.Output[None]:
    def check() -> None:
        for typ in (
            "aws:sns/topic:Topic",
            "aws:cloudwatch/metricAlarm:MetricAlarm",
            "aws:budgets/budget:Budget",
        ):
            assert MOCKS.of_type(typ) == [], typ

    return _after(SETTINGS, check)


@pulumi.runtime.test
def test_a_configured_alarm_watches_the_collectors_errors_and_a_budget_both_spends() -> (
    pulumi.Output[None]
):
    def check() -> None:
        (alarm,) = MOCKS.of_type("aws:cloudwatch/metricAlarm:MetricAlarm")
        assert (alarm.inputs["namespace"], alarm.inputs["metricName"]) == ("AWS/Lambda", "Errors")
        (subscription,) = MOCKS.of_type("aws:sns/topicSubscription:TopicSubscription")
        assert (subscription.inputs["protocol"], subscription.inputs["endpoint"]) == (
            "email",
            "owner@example.com",
        )
        (budget,) = MOCKS.of_type("aws:budgets/budget:Budget")
        assert budget.inputs["limitAmount"] == "25"
        kinds = {n["notificationType"] for n in budget.inputs["notifications"]}
        assert kinds == {"ACTUAL", "FORECASTED"}

    settings = dataclasses.replace(
        SETTINGS,
        alarm_email="owner@example.com",
        budget=Budget(monthly_usd=Decimal("25"), email="owner@example.com"),
    )
    return _after(settings, check)


@pulumi.runtime.test
def test_the_compute_is_one_component_with_its_outputs() -> pulumi.Output[None]:
    storage = Storage("data-finops", SETTINGS)
    compute = Compute(
        "data-finops",
        SETTINGS,
        storage,
        package=Path("build/data-finops-lambda.zip"),
        account_id=pulumi.Output.from_input(ACCOUNT),
    )

    def check(values: dict[str, Any]) -> None:
        assert MOCKS.of_type("data-finops:index:Compute")
        assert set(values) == {
            "apiUrl",
            "producerPolicyArn",
            "readerPolicyArn",
            "collectorFunction",
            "apiFunction",
        }
        assert values["apiUrl"].startswith("https://")

    return pulumi.Output.all(**compute.outputs()).apply(check)
