"""Reference workload infrastructure through Pulumi's mocks (seam A agreed in #21)."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pulumi
import pytest


class RecordingMocks(pulumi.runtime.Mocks):
    def __init__(self) -> None:
        self.resources: list[pulumi.runtime.MockResourceArgs] = []

    def new_resource(self, args: pulumi.runtime.MockResourceArgs) -> tuple[str, dict[str, Any]]:
        self.resources.append(args)
        outputs = {**args.inputs}
        if args.typ == "aws:s3/bucket:Bucket":
            outputs["bucket"] = "mock-reference-bucket"
        return f"{args.name}-id", outputs

    def call(
        self, args: pulumi.runtime.MockCallArgs
    ) -> tuple[dict[Any, Any], list[tuple[str, str]] | None]:
        return {}, None

    def of_type(self, typ: str) -> list[pulumi.runtime.MockResourceArgs]:
        return [r for r in self.resources if r.typ == typ]


MOCKS = RecordingMocks()
pulumi.runtime.set_mocks(MOCKS, preview=False)

import infra  # noqa: E402  (must load after mocks are set)


@pytest.fixture(autouse=True)
def _fresh_mocks() -> None:
    MOCKS.resources.clear()


def _after_build(settings: infra.Settings, check: Callable[[], None]) -> pulumi.Output[None]:
    """Build the stack, then run `check` once every resource is registered."""
    return infra.build(settings).resource_ids.apply(lambda _: check())


@pulumi.runtime.test
def test_workgroup_caps_each_query_and_writes_results_to_the_bucket() -> pulumi.Output[None]:
    def check() -> None:
        (workgroup,) = MOCKS.of_type("aws:athena/workgroup:Workgroup")
        config = workgroup.inputs["configuration"]
        assert config["bytesScannedCutoffPerQuery"] == 104_857_600
        assert config["enforceWorkgroupConfiguration"] is True
        result = config["resultConfiguration"]
        assert result["outputLocation"] == "s3://mock-reference-bucket/athena-results/"
        assert result["encryptionConfiguration"]["encryptionOption"] == "SSE_S3"

    return _after_build(infra.Settings(), check)


@pulumi.runtime.test
def test_bucket_is_private_encrypted_and_removable() -> pulumi.Output[None]:
    def check() -> None:
        (bucket,) = MOCKS.of_type("aws:s3/bucket:Bucket")
        assert bucket.inputs["forceDestroy"] is True
        (block,) = MOCKS.of_type("aws:s3/bucketPublicAccessBlock:BucketPublicAccessBlock")
        assert all(
            block.inputs[k] is True
            for k in (
                "blockPublicAcls",
                "blockPublicPolicy",
                "ignorePublicAcls",
                "restrictPublicBuckets",
            )
        )
        (encryption,) = MOCKS.of_type(
            "aws:s3/bucketServerSideEncryptionConfiguration:BucketServerSideEncryptionConfiguration"
        )
        rule = encryption.inputs["rules"][0]["applyServerSideEncryptionByDefault"]
        assert rule["sseAlgorithm"] == "AES256"

    return _after_build(infra.Settings(), check)


@pulumi.runtime.test
def test_every_resource_uses_the_us_east_1_provider() -> pulumi.Output[None]:
    def check() -> None:
        (provider,) = MOCKS.of_type("pulumi:providers:aws")
        assert provider.inputs["region"] == "us-east-1"
        assert provider.inputs["profile"] == "deployer"
        others = [r for r in MOCKS.resources if r.typ != "pulumi:providers:aws"]
        assert others and all(r.provider and "reference-workload" in r.provider for r in others)

    return _after_build(infra.Settings(aws_profile="deployer"), check)


@pulumi.runtime.test
def test_glue_database_holds_the_workload_tables() -> pulumi.Output[None]:
    def check() -> None:
        (database,) = MOCKS.of_type("aws:glue/catalogDatabase:CatalogDatabase")
        assert database.inputs["name"] == "reference_workload"

    return _after_build(infra.Settings(), check)


@pulumi.runtime.test
def test_no_budget_unless_the_installation_configures_one() -> pulumi.Output[None]:
    def check() -> None:
        assert MOCKS.of_type("aws:budgets/budget:Budget") == []

    return _after_build(infra.Settings(), check)


@pulumi.runtime.test
def test_configured_budget_warns_on_forecast_and_actual_spend() -> pulumi.Output[None]:
    settings = infra.Settings(
        budget=infra.Budget(monthly_usd=Decimal("5"), email="ops@example.com")
    )

    def check() -> None:
        (budget,) = MOCKS.of_type("aws:budgets/budget:Budget")
        assert (budget.inputs["limitAmount"], budget.inputs["limitUnit"]) == ("5", "USD")
        assert budget.inputs["timeUnit"] == "MONTHLY"
        notifications = budget.inputs["notifications"]
        assert {n["notificationType"] for n in notifications} == {"FORECASTED", "ACTUAL"}
        assert all(n["subscriberEmailAddresses"] == ["ops@example.com"] for n in notifications)

    return _after_build(settings, check)


@pytest.mark.parametrize(
    ("amount", "email", "message"),
    [
        ("5", None, "both"),
        (None, "ops@example.com", "both"),
        ("", "ops@example.com", "both"),
        ("five", "ops@example.com", "number"),
        ("0", "ops@example.com", "positive"),
    ],
)
def test_budget_configuration_errors_are_clear(
    amount: str | None, email: str | None, message: str
) -> None:
    config = _FakeConfig({"budgetMonthlyUsd": amount, "budgetEmail": email})
    with pytest.raises(ValueError, match=message):
        infra.Settings.from_config(config, _FakeConfig({}))  # type: ignore[arg-type]


def test_scan_cutoff_below_athena_minimum_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 10485760"):
        infra.Settings(bytes_scanned_cutoff_per_query=1_000_000)


def test_explicit_cutoff_is_not_replaced_by_the_default() -> None:
    config = _FakeConfig({"bytesScannedCutoffPerQuery": 20_000_000})
    settings = infra.Settings.from_config(config, _FakeConfig({}))  # type: ignore[arg-type]
    assert settings.bytes_scanned_cutoff_per_query == 20_000_000


def test_hosted_state_backends_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PULUMI_BACKEND_URL", "https://api.pulumi.com")
    with pytest.raises(RuntimeError, match="file://"):
        infra.require_local_state()
    monkeypatch.setenv("PULUMI_BACKEND_URL", "file:///repo/.local/pulumi")
    infra.require_local_state()


class _FakeConfig:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def get(self, key: str) -> Any:
        return self._values.get(key)

    def get_int(self, key: str) -> int | None:
        value = self._values.get(key)
        return None if value is None else int(value)
