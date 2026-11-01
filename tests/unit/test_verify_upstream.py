"""Upstream checker strictness, offline (#29): dbt additions allowed, OpenLineage strict."""

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "verify_upstream", ROOT / "scripts" / "verify_upstream.py"
)
assert _spec and _spec.loader
verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify)

STRICT: dict[str, Any] = {"type": "object", "additionalProperties": False}
ARTIFACT: dict[str, Any] = {
    **STRICT,
    "required": ["name"],
    "properties": {
        "name": {"type": "string"},
        "languages": {"type": "array", "items": {"enum": ["sql", "python"]}},
        "macros": {
            "type": "object",
            "additionalProperties": {
                **STRICT,
                "properties": {
                    "supported_languages": {
                        "anyOf": [
                            {"type": "array", "items": {"enum": ["sql", "python"]}},
                            {"type": "null"},
                        ]
                    }
                },
            },
        },
    },
}


FUNCTION_MACRO = "macro.dbt.materialization_function_default"


def _failures_against_artifact(instance: dict[str, Any]) -> list[str]:
    return verify.dbt_failures(verify.dbt_validator(ARTIFACT), instance)


def _with_macro_languages(macro_id: str, languages: Any) -> dict[str, Any]:
    return {"name": "m", "macros": {macro_id: {"supported_languages": languages}}}


def test_dbt_artifact_may_carry_fields_the_schema_does_not_list() -> None:
    instance = {"name": "m", "added": 1, "macros": {"x": {"config": {}}}}
    assert _failures_against_artifact(instance) == []


@pytest.mark.parametrize("instance", [{}, {"name": 1}, {"name": "m", "languages": ["javascript"]}])
def test_dbt_artifact_still_fails_on_missing_fields_wrong_types_and_unknown_values(
    instance: dict[str, Any],
) -> None:
    assert _failures_against_artifact(instance) != []


def test_known_language_addition_is_allowed_in_its_macro() -> None:
    instance = _with_macro_languages(FUNCTION_MACRO, ["sql", "python", "javascript"])
    assert _failures_against_artifact(instance) == []


@pytest.mark.parametrize(
    "instance",
    [
        _with_macro_languages("macro.dbt.other", ["sql", "javascript"]),
        _with_macro_languages(FUNCTION_MACRO, ["sql", "cobol"]),
        _with_macro_languages(FUNCTION_MACRO, "sql"),
        {"name": "m", "languages": ["javascript"]},
    ],
)
def test_known_language_addition_is_allowed_nowhere_else(instance: dict[str, Any]) -> None:
    assert _failures_against_artifact(instance) != []


def test_openlineage_events_stay_strict() -> None:
    validator = verify.strict_validator(STRICT)
    assert verify.failures(validator, {"added": 1}) != []


@pytest.mark.parametrize("lines", [False, True])
def test_events_load_from_json_array_or_json_lines(tmp_path: Path, lines: bool) -> None:
    events = [{"eventType": "START"}, {"eventType": "COMPLETE"}]
    path = tmp_path / "events.json"  # the format is read from the content, not the name
    text = "".join(json.dumps(e) + "\n" for e in events) if lines else json.dumps(events)
    path.write_text(text, encoding="utf-8")
    assert verify.load_events(path) == events
