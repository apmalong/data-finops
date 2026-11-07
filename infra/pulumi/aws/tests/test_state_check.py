"""The owner's wizards' checks on an exported stack state (#192; `pulumi stack export`)."""

import json
from pathlib import Path
from typing import Any

import pytest
from deployment.state_check import main, remaining, retention_problems

STACK = "urn:pulumi:dev::data-finops::pulumi:pulumi:Stack::data-finops-dev"
STORAGE = "urn:pulumi:dev::data-finops::data-finops:index:Storage::data-finops"
COMPUTE = "urn:pulumi:dev::data-finops::data-finops:index:Compute::data-finops"


def _resource(typ: str, name: str, parent: str | None, retained: bool = False) -> dict[str, Any]:
    resource: dict[str, Any] = {"urn": f"urn:pulumi:dev::data-finops::{typ}::{name}", "type": typ}
    if parent:
        resource["parent"] = parent
    if retained:
        resource["retainOnDelete"] = True
    return resource


def _state(*resources: dict[str, Any]) -> dict[str, Any]:
    return {"version": 3, "deployment": {"resources": list(resources)}}


def _deployment(*, kept: bool) -> dict[str, Any]:
    return _state(
        {"urn": STACK, "type": "pulumi:pulumi:Stack"},
        {
            "urn": "urn:pulumi:dev::data-finops::pulumi:providers:aws::default",
            "type": "pulumi:providers:aws",
        },
        _resource("data-finops:index:Storage", "data-finops", STACK, retained=kept),
        _resource("aws:s3/bucket:Bucket", "data-finops", STORAGE, retained=kept),
        _resource(
            "aws:s3/bucketVersioning:BucketVersioning", "data-finops", STORAGE, retained=kept
        ),
        _resource("data-finops:index:Compute", "data-finops", STACK),
        _resource("aws:cloudwatch/logGroup:LogGroup", "data-finops-collector", COMPUTE),
        _resource("aws:lambda/function:Function", "data-finops-collector", COMPUTE),
    )


def test_a_kept_bucket_keeps_every_storage_resource_and_nothing_else() -> None:
    assert retention_problems(_deployment(kept=True), delete_data=False) == []
    assert retention_problems(_deployment(kept=False), delete_data=True) == []


def test_a_storage_resource_not_kept_is_a_problem_when_the_data_is_kept() -> None:
    state = _deployment(kept=True)
    del state["deployment"]["resources"][4]["retainOnDelete"]  # the versioning
    assert retention_problems(state, delete_data=False) == [
        "aws:s3/bucketVersioning:BucketVersioning data-finops would be deleted by destroy"
    ]


def test_a_kept_resource_is_a_problem_when_the_data_is_to_be_deleted() -> None:
    # A component is not an AWS resource: what it keeps is its children.
    assert retention_problems(_deployment(kept=True), delete_data=True) == [
        "aws:s3/bucket:Bucket data-finops would be kept by destroy",
        "aws:s3/bucketVersioning:BucketVersioning data-finops would be kept by destroy",
    ]


def test_a_kept_resource_outside_the_storage_is_a_problem() -> None:
    # A kept log group would collide with the next deployment's fixed name.
    state = _deployment(kept=True)
    state["deployment"]["resources"][6]["retainOnDelete"] = True
    assert retention_problems(state, delete_data=False) == [
        "aws:cloudwatch/logGroup:LogGroup data-finops-collector would be kept by destroy"
    ]


def test_a_state_without_a_bucket_is_a_problem() -> None:
    assert retention_problems(_state(), delete_data=False) == ["the state has no bucket"]


def test_remaining_lists_every_resource_but_the_stack_and_its_providers() -> None:
    assert remaining(_deployment(kept=True))[:2] == [
        "data-finops:index:Storage data-finops",
        "aws:s3/bucket:Bucket data-finops",
    ]
    assert len(remaining(_deployment(kept=True))) == 6
    assert remaining(_state({"urn": STACK, "type": "pulumi:pulumi:Stack"})) == []


def test_an_empty_stack_exports_no_resources_at_all() -> None:
    assert remaining({"version": 3, "deployment": {}}) == []


@pytest.mark.parametrize(
    ("argv", "state", "code", "out"),
    [
        (["retention", "--delete-data", "false"], _deployment(kept=True), 0, "ok\n"),
        (
            ["retention", "--delete-data", "true"],
            _deployment(kept=False) | {},
            0,
            "ok\n",
        ),
        (["remaining"], _state(), 0, "ok\n"),
        (
            ["remaining"],
            _deployment(kept=True),
            1,
            "data-finops:index:Storage data-finops\n",
        ),
    ],
)
def test_the_command_line_prints_ok_or_the_problems(
    argv: list[str],
    state: dict[str, Any],
    code: int,
    out: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exported = tmp_path / "state.json"
    exported.write_text(json.dumps(state), encoding="utf-8")
    assert main([argv[0], str(exported), *argv[1:]]) == code
    assert capsys.readouterr().out.startswith(out)


def test_the_command_line_never_prints_a_physical_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = _deployment(kept=True)
    state["deployment"]["resources"][3]["outputs"] = {"bucket": "owner-secret-bucket-1a2b"}
    del state["deployment"]["resources"][3]["retainOnDelete"]
    exported = tmp_path / "state.json"
    exported.write_text(json.dumps(state), encoding="utf-8")
    assert main(["retention", str(exported), "--delete-data", "false"]) == 1
    assert "owner-secret-bucket" not in capsys.readouterr().out


def _with_policies() -> dict[str, Any]:
    state = _deployment(kept=True)
    resources = state["deployment"]["resources"]
    for typ, name, document in (
        ("aws:iam/rolePolicy:RolePolicy", "data-finops-collector", {"Statement": [1]}),
        ("aws:iam/policy:Policy", "data-finops-reader", {"Statement": [2]}),
        ("aws:s3/bucketPolicy:BucketPolicy", "data-finops", {"Statement": [3]}),
    ):
        resource = _resource(typ, name, COMPUTE)
        resource["outputs"] = {"policy": json.dumps(document), "arn": "arn:aws:iam::123456789012:x"}
        resources.append(resource)
    resources[3]["id"] = "owner-secret-bucket-1a2b"
    return state


def test_policies_writes_each_iam_document_by_its_logical_name(tmp_path: Path) -> None:
    exported = tmp_path / "state.json"
    exported.write_text(json.dumps(_with_policies()), encoding="utf-8")
    out = tmp_path / "policies"
    assert main(["policies", str(exported), str(out)]) == 0
    # A bucket policy is a resource policy, which IAM Access Analyzer validates as one.
    assert sorted(p.name for p in out.iterdir()) == [
        "data-finops-collector.json",
        "data-finops-reader.json",
        "data-finops.resource.json",
    ]
    assert json.loads((out / "data-finops-reader.json").read_text(encoding="utf-8")) == {
        "Statement": [2]
    }


def test_ids_prints_the_physical_ids_of_one_type_for_the_wizard_to_keep(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exported = tmp_path / "state.json"
    exported.write_text(json.dumps(_with_policies()), encoding="utf-8")
    assert main(["ids", str(exported), "aws:s3/bucket:Bucket"]) == 0
    assert capsys.readouterr().out == "owner-secret-bucket-1a2b\n"
    assert main(["ids", str(exported), "aws:budgets/budget:Budget"]) == 0
    assert capsys.readouterr().out == ""
