"""The committed OpenAPI document matches the app (spec 06; ADR 0014: OpenAPI 3.1)."""

import json
from pathlib import Path

from data_finops.api.openapi import document

COMMITTED = Path(__file__).resolve().parents[2] / "schemas" / "openapi.json"


def test_the_committed_openapi_document_matches_the_app():
    assert json.loads(COMMITTED.read_text(encoding="utf-8")) == document(), (
        "schemas/openapi.json is stale; run `python -m data_finops.api.openapi`"
    )


def test_the_document_is_openapi_3_1_and_answers_invalid_input_with_problems():
    generated = document()
    assert generated["openapi"].startswith("3.1.")
    responses = [
        r for path in generated["paths"].values() for op in path.values() for r in op["responses"]
    ]
    assert "422" not in responses
