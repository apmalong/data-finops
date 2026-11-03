"""Validate a response body against the committed OpenAPI document (spec 06, seam 2)."""

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

OPENAPI = json.loads(
    (Path(__file__).resolve().parents[2] / "schemas" / "openapi.json").read_text(encoding="utf-8")
)


def valid_response(method: str, path: str, status: int, body: Any) -> None:
    """The body validates against the document's schema for that operation and status."""
    content = OPENAPI["paths"][path][method]["responses"][str(status)]["content"]
    schema = next(iter(content.values()))["schema"]
    Draft202012Validator({**schema, "components": OPENAPI["components"]}).validate(body)
