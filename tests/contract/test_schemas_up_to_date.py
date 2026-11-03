import json
from pathlib import Path

from data_finops.schemas import PUBLISHED, generate

ROOT = Path(__file__).resolve().parents[2]


def test_committed_schemas_match_models():
    for name in PUBLISHED:
        committed = json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text())
        assert committed == generate(name), (
            f"schemas/{name}.schema.json is stale; run `uv run python -m data_finops.schemas`"
        )


def test_no_unpublished_schema_files():
    # openapi.json is the API's document, kept current by test_openapi_up_to_date.py.
    on_disk = {
        p.name.removesuffix(".schema.json")
        for p in (ROOT / "schemas").glob("*.json")
        if p.name != "openapi.json"
    }
    assert on_disk == set(PUBLISHED)
