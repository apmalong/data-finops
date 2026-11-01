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


def _dbt_failures(instance: dict[str, Any]) -> list[str]:
    return verify.dbt_failures(verify.dbt_validator(ARTIFACT), instance)


def test_dbt_artifact_may_carry_fields_the_schema_does_not_list() -> None:
    assert _dbt_failures({"name": "m", "added": 1, "macros": {"x": {"config": {}}}}) == []


@pytest.mark.parametrize("instance", [{}, {"name": 1}, {"name": "m", "languages": ["javascript"]}])
def test_dbt_artifact_still_fails_on_missing_fields_wrong_types_and_unknown_values(
    instance: dict[str, Any],
) -> None:
    assert _dbt_failures(instance) != []


def test_known_language_addition_is_allowed_only_in_macros() -> None:
    languages = ["sql", "python", "javascript"]
    assert _dbt_failures({"name": "m", "macros": {"x": {"supported_languages": languages}}}) == []
    assert _dbt_failures({"name": "m", "languages": languages}) != []


def test_openlineage_events_stay_strict() -> None:
    validator = verify.strict_validator(STRICT)
    assert verify.failures(validator, {"added": 1}) != []


@pytest.mark.parametrize("suffix", [".json", ".jsonl"])
def test_events_load_from_json_array_or_json_lines(tmp_path: Path, suffix: str) -> None:
    events = [{"eventType": "START"}, {"eventType": "COMPLETE"}]
    path = tmp_path / f"events{suffix}"
    if suffix == ".json":
        path.write_text(json.dumps(events), encoding="utf-8")
    else:
        path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    assert verify.load_events(path) == events
