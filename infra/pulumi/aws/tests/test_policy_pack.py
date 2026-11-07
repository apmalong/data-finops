"""The policy pack run on the real program (spec 08 seam 7b): the stack is built under Pulumi's
mocks, its recorded resources are written as a state file (`pulumi stack export`'s v3 form),
and `pulumi policy analyze --file` runs the real pack over them, with no backend, no provider
and no account. This is the offline form of "preview policies validated" (spec 08 §Exit)."""

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pulumi
import pytest
from deployment.program import build
from deployment.settings import Settings

from tests.mocks import MOCKS

PACK = Path(__file__).resolve().parents[1] / "policy"
CLI_VERSION = "v3.272.0"
"""The pinned CLI, the same release as the SDK in pyproject.toml (owner, M6 design Q23)."""
STACK = "urn:pulumi:test::data-finops::pulumi:pulumi:Stack::data-finops-test"
SETTINGS = Settings(
    region="us-east-1",
    export_bucket="example-export-bucket",
    export_name="example-focus-1-2",
    workgroups=("demo",),
)
Change = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


@pytest.fixture(autouse=True)
def _fresh_mocks() -> None:
    MOCKS.resources.clear()


def _cli() -> str:
    cli = shutil.which("pulumi")
    assert cli, "the Pulumi CLI is needed to run the policy pack (CI installs it)"
    return cli


def _state(resources: list[dict[str, Any]]) -> dict[str, Any]:
    stack = {"urn": STACK, "custom": False, "type": "pulumi:pulumi:Stack"}
    return {
        "version": 3,
        "deployment": {
            "manifest": {"time": "2026-11-07T00:00:00Z", "magic": "", "version": CLI_VERSION},
            "resources": [stack, *resources],
        },
    }


def _recorded() -> list[dict[str, Any]]:
    """What the mocks recorded, as state resources: inputs as the program set them."""
    return [
        {
            "urn": f"urn:pulumi:test::data-finops::{r.typ}::{r.name}",
            "custom": bool(r.custom),
            "type": r.typ,
            "inputs": r.inputs,
            "outputs": r.inputs,
            "parent": STACK,
        }
        for r in MOCKS.resources
    ]


def _asset(value: object) -> object:
    """An asset input (the Lambda package) as the state records it: by its path."""
    if isinstance(value, pulumi.FileAsset):
        return {"path": value.path}
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _analyze(tmp: Path, resources: list[dict[str, Any]]) -> subprocess.CompletedProcess[str]:
    state = tmp / "state.json"
    state.write_text(json.dumps(_state(resources), default=_asset), encoding="utf-8")
    # The pack runs in its own virtualenv (PulumiPolicy.yaml); this test's must not leak in.
    environ = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    return subprocess.run(  # noqa: S603 (a fixed command over a file this test wrote)
        [_cli(), "policy", "analyze", "--file", str(state), "--policy-pack", str(PACK)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
        env=environ,
    )


def _after_build(
    tmp_path: Path, check: Callable[[list[dict[str, Any]]], None]
) -> pulumi.Output[None]:
    deployment = build(SETTINGS, package=_stand_in(tmp_path))
    return pulumi.Output.all(deployment.storage.ready, deployment.compute.ready).apply(
        lambda _: check(_recorded())
    )


def test_the_cli_is_the_pinned_release() -> None:
    version = subprocess.run(  # noqa: S603
        [_cli(), "version"], capture_output=True, encoding="utf-8", timeout=60, check=True
    ).stdout.strip()
    assert version == CLI_VERSION


@pulumi.runtime.test
def test_the_program_passes_every_policy(tmp_path: Path) -> pulumi.Output[None]:
    def check(resources: list[dict[str, Any]]) -> None:
        result = _analyze(tmp_path, resources)
        assert result.returncode == 0, result.stdout + result.stderr

    return _after_build(tmp_path, check)


def _without_versioning(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in resources if not r["type"].endswith(":BucketVersioning")]


PLAIN_HTTP = json.dumps({"Statement": [{"Effect": "Allow", "Action": "s3:GetObject"}]})


def _changed(typ_suffix: str, change: dict[str, Any], name_suffix: str = "") -> Change:
    """A fixture changing the inputs of every resource of one type (and, if given, name)."""

    def apply(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for resource in resources:
            if resource["type"].endswith(typ_suffix) and resource["urn"].endswith(name_suffix):
                inputs = resource["inputs"] | change
                # A setting left out is absent from the state, never null. Analyze hands
                # resource policies the inputs and stack policies the outputs: change both.
                kept = {k: v for k, v in inputs.items() if v is not None}
                resource["inputs"], resource["outputs"] = kept, dict(kept)
        return resources

    return apply


def _granting(name_suffix: str, statement: dict[str, Any]) -> Change:
    """A fixture adding one statement to the role policy whose name ends with `name_suffix`."""

    def apply(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for resource in resources:
            if resource["type"].endswith(":RolePolicy") and resource["urn"].endswith(name_suffix):
                document = json.loads(resource["inputs"]["policy"])
                document["Statement"].append(statement)
                changed = resource["inputs"] | {"policy": json.dumps(document)}
                resource["inputs"], resource["outputs"] = changed, dict(changed)
        return resources

    return apply


def _with_an_actual_only_budget(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    budget = {
        "urn": "urn:pulumi:test::data-finops::aws:budgets/budget:Budget::budget",
        "custom": True,
        "type": "aws:budgets/budget:Budget",
        "inputs": {
            "budgetType": "COST",
            "notifications": [{"notificationType": "ACTUAL", "threshold": 100}],
        },
        "outputs": {},
        "parent": STACK,
    }
    return [*resources, budget]


def _with_a_vpc(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    vpc = {
        "urn": "urn:pulumi:test::data-finops::aws:ec2/vpc:Vpc::network",
        "custom": True,
        "type": "aws:ec2/vpc:Vpc",
        "inputs": {"cidrBlock": "10.0.0.0/16"},
        "outputs": {},
        "parent": STACK,
    }
    return [*resources, vpc]


@pytest.mark.parametrize(
    ("change", "policy"),
    [
        (
            _changed(":BucketPublicAccessBlock", {"restrictPublicBuckets": False}),
            "s3-public-access-blocked",
        ),
        (_without_versioning, "s3-guarded"),
        (_changed(":BucketServerSideEncryptionConfiguration", {"rules": [{}]}), "s3-encrypted"),
        (_changed(":BucketPolicy", {"policy": PLAIN_HTTP}), "s3-tls-only"),
        (_with_a_vpc, "no-ruled-out-resources"),
        (_changed(":FunctionUrl", {"authorizationType": "NONE"}), "url-iam-only"),
        (_changed(":Function", {"memorySize": None}), "function-bounded"),
        (_changed(":LogGroup", {"retentionInDays": None}), "function-logged"),
        (
            _granting(
                "collector",
                {"Effect": "Allow", "Action": "athena:StartQueryExecution", "Resource": "*"},
            ),
            "iam-least-privilege",
        ),
        (
            _granting(
                "api",
                {
                    "Effect": "Allow",
                    "Action": "s3:PutObject",
                    "Resource": "arn:aws:s3:::mock-deployment-bucket/inbox/*",
                },
            ),
            "iam-least-privilege",
        ),
        (_with_an_actual_only_budget, "budget-alerts"),
    ],
)
@pulumi.runtime.test
def test_a_non_compliant_stack_fails_the_pack(
    tmp_path: Path, change: Change, policy: str
) -> pulumi.Output[None]:
    def check(resources: list[dict[str, Any]]) -> None:
        result = _analyze(tmp_path, change(resources))
        assert result.returncode == 1
        assert policy in result.stdout + result.stderr

    return _after_build(tmp_path, check)


def _stand_in(folder: Path) -> Path:
    """A stand-in for the built package: the program hashes it."""
    package = folder / "data-finops-lambda.zip"
    package.write_bytes(b"PK\x05\x06" + b"\x00" * 18)  # an empty zip
    return package
