"""The simulator's templates are the reference workload's real records (#226; spec 09: "The events
and artifacts come from templates in `fixtures/reference-workload/`")."""

import json
from importlib.resources import files
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "reference-workload"


def _lines(name: str) -> list[dict[str, object]]:
    text = (FIXTURES / "airflow" / name).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _templates() -> dict[str, dict[str, object]]:
    text = files("data_finops.public_demo").joinpath("templates.json").read_text(encoding="utf-8")
    loaded: dict[str, dict[str, object]] = json.loads(text)
    return loaded


def test_each_event_template_is_a_reference_event() -> None:
    airflow, dbt = _lines("airflow-events.jsonl"), _lines("dbt-events.jsonl")
    templates = _templates()

    assert templates["dag_start"] == airflow[0] and templates["dag_complete"] == airflow[2]
    assert templates["task_start"] == airflow[1] and templates["task_complete"] == airflow[3]
    assert templates["dbt_start"] == dbt[0] and templates["dbt_complete"] == dbt[9]
    assert templates["model_start"] == dbt[1] and templates["model_complete"] == dbt[5]


def test_the_artifact_templates_are_the_reference_artifacts_cut_to_one_node() -> None:
    manifest = json.loads((FIXTURES / "dbt" / "3-run" / "manifest.json").read_text("utf-8"))
    results = json.loads((FIXTURES / "airflow" / "run_results.json").read_text("utf-8"))
    templates = _templates()

    assert templates["manifest"]["metadata"] == manifest["metadata"]
    assert templates["manifest"]["nodes"] == {
        "model.reference_workload.stg_orders": manifest["nodes"][
            "model.reference_workload.stg_orders"
        ]
    }
    assert set(templates["manifest"]) == set(manifest)
    assert templates["run_results"] == {**results, "results": results["results"][:1]}
