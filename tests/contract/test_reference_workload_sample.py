"""The anonymized reference workload sample (#24, #32) keeps what the real run showed (#35)."""

import json
import re
from collections import Counter
from functools import cache
from pathlib import Path

from data_finops.dbt.artifacts import load_manifest, load_run_results
from data_finops.lineage.openlineage import load_events
from data_finops.providers.athena import AthenaQueryHistory, load_query_executions

SAMPLE = Path(__file__).resolve().parents[2] / "fixtures" / "reference-workload"
COMMENT = re.compile(r"-- /\* (?P<json>\{.*\}) \*/\n")
STEPS = ("1-seed", "2-run", "3-run")


@cache
def _history() -> AthenaQueryHistory:
    # The AWS response names no account; the sample's ARNs use the same documentation placeholder.
    return load_query_executions(
        SAMPLE / "athena" / "query-executions.json", account_id="111122223333", region="us-east-1"
    )


@cache
def _step_by_invocation() -> dict[str, str]:
    return {
        load_run_results(SAMPLE / "dbt" / step / "run_results.json").metadata.invocation_id: step
        for step in STEPS
    }


def _invocation_of(step: str) -> str:
    return next(i for i, s in _step_by_invocation().items() if s == step)


def _comments() -> list[dict[str, str]]:
    found = (COMMENT.match(q.Query) for q in _history().QueryExecutions)
    return [json.loads(m["json"]) for m in found if m]


def test_every_commented_query_links_to_a_run_and_a_manifest_node():
    nodes = set(load_manifest(SAMPLE / "dbt" / "3-run" / "manifest.json").nodes)
    comments = _comments()
    assert len(comments) == 17
    assert all(c["invocation_id"] in _step_by_invocation() for c in comments)
    assert all(c["node_id"] in nodes for c in comments)


def test_one_model_issues_several_queries_in_one_incremental_run():
    run = _invocation_of("3-run")
    per_node = Counter(c["node_id"] for c in _comments() if c["invocation_id"] == run)
    assert per_node["model.reference_workload.daily_orders"] == 3


def test_uncommented_statements_are_exactly_the_alter_tables():
    """dbt-athena sends these without the comment (OQ-22)."""
    uncommented = [q for q in _history().QueryExecutions if not COMMENT.match(q.Query)]
    assert [q.Query for q in uncommented] == ["alter table …"] * 3
    assert all(q.Statistics and q.Statistics.DataScannedInBytes == 0 for q in uncommented)


def test_openlineage_dbt_run_invocations_match_the_runs():
    events = load_events(SAMPLE / "openlineage" / "dbt-events.jsonl")
    steps = Counter(
        _step_by_invocation()[e.run.facets["dbt_run"]["invocation_id"]]
        for e in events
        if "dbt_run" in e.run.facets
    )
    assert len(events) == 22
    assert steps == {"2-run": 10, "3-run": 10}  # dbt-ol seed emits no dbt_run facet
