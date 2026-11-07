"""The draft deployer policy (spec 08 §IAM, Deployer; #192): its shape, and that it reaches
every service the program declares a resource in. It stays a draft, unvalidated, until the
owner's deploy wizard runs IAM Access Analyzer on it."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pulumi
from deployment.program import build
from deployment.settings import Budget, Settings

from tests.mocks import MOCKS

DRAFT = Path(__file__).parents[1] / "iam" / "deployer-policy.draft.json"
# Each Pulumi AWS module the program uses, by the IAM service prefix its calls need.
SERVICE = {
    "s3": "s3",
    "iam": "iam",
    "lambda": "lambda",
    "cloudwatch/logGroup": "logs",
    "cloudwatch/metricAlarm": "cloudwatch",
    "scheduler": "scheduler",
    "sns": "sns",
    "budgets": "budgets",
}
NEEDS_EVERY_RESOURCE = {"sts:GetCallerIdentity", "access-analyzer:ValidatePolicy"}


def _statements() -> list[dict[str, Any]]:
    document = json.loads(DRAFT.read_text(encoding="utf-8"))
    assert document["Version"] == "2012-10-17"
    return list(document["Statement"])


def _actions(statement: dict[str, Any]) -> list[str]:
    action = statement["Action"]
    return [action] if isinstance(action, str) else list(action)


def test_the_draft_allows_named_actions_on_named_resources_only() -> None:
    for statement in _statements():
        assert statement["Effect"] == "Allow" and statement["Sid"]
        assert "NotAction" not in statement and "NotResource" not in statement
        for action in _actions(statement):
            service, name = action.split(":")
            assert name != "*", action
        if statement["Resource"] == "*":
            assert set(_actions(statement)) <= NEEDS_EVERY_RESOURCE, statement["Sid"]
        else:
            assert (
                "data-finops-" in statement["Resource"] or statement["Sid"] == "DescribeLogGroups"
            )


def test_passing_a_role_is_only_to_lambda_and_the_scheduler() -> None:
    (passing,) = [s for s in _statements() if "iam:PassRole" in _actions(s)]
    assert _actions(passing) == ["iam:PassRole"]
    assert passing["Condition"] == {
        "StringEquals": {"iam:PassedToService": ["lambda.amazonaws.com", "scheduler.amazonaws.com"]}
    }


@pulumi.runtime.test
def test_the_draft_reaches_every_service_the_program_declares(
    tmp_path: Path,
) -> pulumi.Output[None]:
    MOCKS.resources.clear()
    package = tmp_path / "data-finops-lambda.zip"
    package.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    settings = Settings(
        region="us-east-1",
        export_bucket="b",
        export_name="e",
        workgroups=("demo",),
        alarm_email="owner@example.com",
        budget=Budget(monthly_usd=Decimal(5), email="owner@example.com"),
    )
    deployment = build(settings, package=package)

    def check(_: object) -> None:
        granted = {a.split(":")[0] for s in _statements() for a in _actions(s)}
        for resource in MOCKS.resources:
            if not resource.typ.startswith("aws:"):
                continue
            module = resource.typ.split(":")[1]
            service = SERVICE.get(module) or SERVICE.get(module.split("/")[0])
            assert service, f"{resource.typ}: add its IAM service to SERVICE and the draft"
            assert service in granted, f"the draft grants nothing in {service} ({resource.typ})"
        assert {r.typ for r in MOCKS.resources} >= {
            "aws:budgets/budget:Budget",
            "aws:sns/topic:Topic",
        }

    # Once both components are ready, every resource has been registered with the mocks.
    ready = pulumi.Output.all(deployment.storage.ready, deployment.compute.ready)
    return ready.apply(check)
