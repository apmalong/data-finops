"""The Airflow image and DAG, checked offline: pins, hashes and the parent-run hand-off (#74).

Airflow is not installed here (it does not run on Windows), so the DAG file runs against
stand-ins for the two Airflow names it imports and the test inspects what the task is given.
The image itself is built and smoke-tested with Docker; see the README.
"""

import re
import sys
import tomllib
import types
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKLOAD = Path(__file__).resolve().parents[1]
AIRFLOW = WORKLOAD / "airflow"
PYPROJECT = WORKLOAD / "pyproject.toml"
DBT_PACKAGES = ("dbt-core", "dbt-athena", "openlineage-dbt")


def _pinned(requirements: str) -> dict[str, str]:
    return dict(re.findall(r"^([a-z0-9][a-z0-9._-]*)==([^\s;\\]+)", requirements, re.M))


def test_the_image_is_airflow_3_3_2_pinned_by_digest() -> None:
    dockerfile = (AIRFLOW / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(
        r"^FROM apache/airflow:3\.3\.2-python3\.12@sha256:[0-9a-f]{64}$", dockerfile, re.M
    )
    assert "--require-hashes" in dockerfile


def test_dbt_in_the_image_is_the_version_the_project_pins() -> None:
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = _pinned("\n".join(pyproject["project"]["dependencies"]))
    image = _pinned((AIRFLOW / "requirements-dbt.txt").read_text(encoding="utf-8"))
    assert {name: image[name] for name in DBT_PACKAGES} == {
        name: project[name] for name in DBT_PACKAGES
    }


def test_every_dbt_requirement_carries_a_hash() -> None:
    text = (AIRFLOW / "requirements-dbt.txt").read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[a-z0-9])", text.strip())
    assert blocks and all("--hash=sha256:" in block for block in blocks)


class _Dag:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def __enter__(self) -> "_Dag":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def task(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run the DAG file against stand-ins and return the arguments its task was given."""
    captured: dict[str, Any] = {}
    names = (
        "airflow",
        "airflow.sdk",
        "airflow.providers",
        "airflow.providers.standard",
        "airflow.providers.standard.operators",
        "airflow.providers.standard.operators.bash",
    )
    stubs = {name: types.ModuleType(name) for name in names}
    stubs["airflow.sdk"].DAG = _Dag  # type: ignore[attr-defined]
    stubs["airflow.providers.standard.operators.bash"].BashOperator = (  # type: ignore[attr-defined]
        lambda **kwargs: captured.update(kwargs)
    )
    for name, module in stubs.items():
        monkeypatch.setitem(sys.modules, name, module)
    source = (AIRFLOW / "dags" / "reference_workload.py").read_text(encoding="utf-8")
    exec(compile(source, "reference_workload.py", "exec"), {"__name__": "dag"})  # noqa: S102
    return captured


def test_the_task_hands_its_run_and_the_dag_run_to_dbt_ol(task: dict[str, Any]) -> None:
    macros = "macros.OpenLineageProviderPlugin"
    assert task["env"] == {
        "OPENLINEAGE_PARENT_ID": f"{{{{ {macros}.lineage_parent_id(task_instance) }}}}",
        "OPENLINEAGE_ROOT_PARENT_ID": f"{{{{ {macros}.lineage_root_parent_id(task_instance) }}}}",
    }
    assert task["append_env"] is True


def test_the_task_runs_dbt_ol_from_its_own_virtualenv(task: dict[str, Any]) -> None:
    command = task["bash_command"]
    assert command.startswith('unset PYTHONPATH && export PATH="/home/airflow/dbt-venv/bin:$PATH"')
    assert command.endswith(
        "cd /workspace/examples/reference-workload/dbt && dbt-ol run --profiles-dir ."
    )


def test_events_go_to_the_ignored_local_folder_and_no_credentials_by_default() -> None:
    compose = yaml.safe_load((AIRFLOW / "compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["airflow"]
    assert service["command"] == "standalone"
    transport = yaml.safe_load(service["environment"]["AIRFLOW__OPENLINEAGE__TRANSPORT"])
    assert transport["log_file_path"].startswith("/workspace/.local/openlineage/")
    assert not any(".aws" in volume for volume in service["volumes"])
    assert not any(key.startswith("AWS_") for key in service["environment"])
    # Only the dbt project and the event folder are mounted, not the whole checkout.
    assert sorted(service["volumes"]) == [
        "../../../.local/openlineage:/workspace/.local/openlineage",
        "../dbt:/workspace/examples/reference-workload/dbt",
        "./dags:/opt/airflow/dags:ro",
    ]


def test_a_real_run_gets_short_lived_credentials_and_no_aws_folder() -> None:
    aws = yaml.safe_load((AIRFLOW / "compose.aws.yaml").read_text(encoding="utf-8"))
    service = aws["services"]["airflow"]
    assert "volumes" not in service
    assert {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"} <= set(
        service["environment"]
    )
    assert all(
        ":?" in str(service["environment"][key])
        for key in (
            "AWS_SESSION_TOKEN",
            "REFERENCE_WORKLOAD_BUCKET",
        )
    )
