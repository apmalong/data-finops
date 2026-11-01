import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_fixtures_are_readable_and_have_join_keys():
    def load(name):
        return json.loads((ROOT / "fixtures" / name).read_text())

    billing = load("billing.json")
    manifest = load("manifest.json")
    results = load("run_results.json")
    queries = load("athena_queries.json")
    assert all(x["source_record_id"] and x["currency"] for x in billing)
    assert queries[0]["dbt_node_id"] in manifest["nodes"]
    assert queries[0]["dbt_invocation_id"] == results["metadata"]["invocation_id"]
