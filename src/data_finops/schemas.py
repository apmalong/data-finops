"""Generate the JSON Schemas in `schemas/` from the contract models (spec 02).

Run `uv run python -m data_finops.schemas` after changing a published model;
`tests/contract/test_schemas_up_to_date.py` fails while the files are stale.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from data_finops.contracts.allocation import Allocation
from data_finops.contracts.billing import ExportSnapshot, FocusBillingLine

DIALECT = "https://json-schema.org/draft/2020-12/schema"

PUBLISHED: dict[str, type[BaseModel]] = {
    "allocation": Allocation,
    "billing-line": FocusBillingLine,
    "export-snapshot": ExportSnapshot,
}


def generate(name: str) -> dict[str, Any]:
    return {"$schema": DIALECT, **PUBLISHED[name].model_json_schema(mode="validation")}


def write_all(directory: Path) -> None:
    for name in PUBLISHED:
        path = directory / f"{name}.schema.json"
        text = json.dumps(generate(name), indent=2) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write_all(Path(__file__).resolve().parents[2] / "schemas")
