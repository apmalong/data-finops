"""Write the API's OpenAPI 3.1 document to `schemas/openapi.json` (spec 06).

Run `python -m data_finops.api.openapi` after changing a route or response model;
`tests/contract/test_openapi_up_to_date.py` fails while the file is stale.
"""

import json
from pathlib import Path
from typing import Any

from data_finops.api.app import create_app


def document() -> dict[str, Any]:
    """The generated document; the database path is never opened or written into it."""
    return create_app(Path("data-finops.sqlite")).openapi()


def write(path: Path) -> None:
    path.write_text(json.dumps(document(), indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write(Path(__file__).resolve().parents[3] / "schemas" / "openapi.json")
