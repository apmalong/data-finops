"""Readers accept the shapes real tools write (#31): dbt-ol JSON Lines, plain Athena responses.

Identifiers are invented or AWS documentation placeholders.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from data_finops.lineage.openlineage import load_events
from data_finops.providers.athena import load_query_executions

ACCOUNT = "111122223333"


def _event(event_type: str) -> dict[str, Any]:
    return {
        "eventType": event_type,
        "eventTime": "2026-11-01T21:19:14.000Z",
        "producer": "https://github.com/OpenLineage/OpenLineage/tree/1.54.0/integration/dbt",
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
        "run": {"runId": "0190f4a1-7a5e-7c1e-9f3a-1b2c3d4e5f60", "facets": {}},
        "job": {"namespace": "dbt", "name": "dbt-run-example"},
    }


EVENTS = [_event("START"), _event("COMPLETE")]

RESPONSE = {
    "QueryExecutions": [
        {
            "QueryExecutionId": "a1b2c3d4-5678-90ab-cdef-EXAMPLE11111",
            "Query": "select 1",
            "StatementType": "DML",
            "WorkGroup": "example-workgroup",
            "Status": {"State": "SUCCEEDED", "SubmissionDateTime": "2026-11-01T21:19:15+00:00"},
            "Statistics": {"DataScannedInBytes": 0},
        }
    ],
    "UnprocessedQueryExecutionIds": [],
}


def _write(path: Path, text: str, bom: bool = False) -> Path:
    path.write_bytes(("\ufeff" if bom else "").encode("utf-8") + text.encode("utf-8"))
    return path


@pytest.mark.parametrize("bom", [False, True])
def test_events_load_from_a_json_array(tmp_path: Path, bom: bool) -> None:
    path = _write(tmp_path / "events.json", json.dumps(EVENTS), bom)
    assert [e.eventType for e in load_events(path)] == ["START", "COMPLETE"]


def test_events_load_from_json_lines_as_dbt_ol_writes(tmp_path: Path) -> None:
    lines = "".join(json.dumps(e) + "\n" for e in EVENTS) + "\n"
    path = _write(tmp_path / "dbt-events.jsonl", lines)
    assert [e.eventType for e in load_events(path)] == ["START", "COMPLETE"]


def test_malformed_json_line_is_reported_with_its_line_number(tmp_path: Path) -> None:
    path = _write(tmp_path / "dbt-events.jsonl", json.dumps(EVENTS[0]) + "\n{not json\n")
    with pytest.raises(ValueError, match="line 2"):
        load_events(path)


@pytest.mark.parametrize("bom", [False, True])
def test_plain_athena_response_loads_with_caller_supplied_account_and_region(
    tmp_path: Path, bom: bool
) -> None:
    path = _write(tmp_path / "response.json", json.dumps(RESPONSE), bom)
    history = load_query_executions(path, account_id=ACCOUNT, region="us-east-1")
    assert (history.account_id, history.region) == (ACCOUNT, "us-east-1")
    assert [q.QueryExecutionId for q in history.QueryExecutions] == [
        "a1b2c3d4-5678-90ab-cdef-EXAMPLE11111"
    ]


def test_enveloped_athena_history_still_loads(tmp_path: Path) -> None:
    envelope = {"account_id": ACCOUNT, "region": "us-east-1", **RESPONSE}
    path = _write(tmp_path / "history.json", json.dumps(envelope))
    assert load_query_executions(path).account_id == ACCOUNT
    assert load_query_executions(path, account_id=ACCOUNT).region == "us-east-1"


@pytest.mark.parametrize(
    ("document", "given"),
    [
        (RESPONSE, {"region": "us-east-1"}),
        (RESPONSE, {"account_id": ACCOUNT}),
        (
            {"account_id": ACCOUNT, "region": "us-east-1", **RESPONSE},
            {"account_id": "444455556666"},
        ),
        ({"account_id": ACCOUNT, "region": "us-east-1", **RESPONSE}, {"region": "eu-west-1"}),
    ],
)
def test_missing_or_conflicting_account_and_region_are_rejected(
    tmp_path: Path, document: dict[str, Any], given: dict[str, str]
) -> None:
    path = _write(tmp_path / "history.json", json.dumps(document))
    with pytest.raises(ValueError):
        load_query_executions(path, **given)


def test_event_text_may_contain_unicode_line_separators(tmp_path: Path) -> None:
    event = {**EVENTS[0], "job": {"namespace": "dbt", "name": "a\u2028b"}}
    path = _write(tmp_path / "dbt-events.jsonl", json.dumps(event, ensure_ascii=False) + "\n")
    assert [e.job.name for e in load_events(path)] == ["a\u2028b"]


@pytest.mark.parametrize("text", ["[{bad", "{not json", "[1, 2]"])
def test_unreadable_files_are_reported_with_their_path(tmp_path: Path, text: str) -> None:
    events, history = (
        _write(tmp_path / "events.json", text),
        _write(tmp_path / "history.json", text),
    )
    with pytest.raises(ValueError, match="events.json"):
        load_events(events)
    with pytest.raises(ValueError, match="history.json"):
        load_query_executions(history, account_id=ACCOUNT, region="us-east-1")


def test_errors_do_not_echo_query_text(tmp_path: Path) -> None:
    secret = "select customer_email from private_table"
    bad = {"QueryExecutionId": "a1b2c3d4-EXAMPLE", "Query": secret}  # Status missing
    path = _write(tmp_path / "history.json", json.dumps({**RESPONSE, "QueryExecutions": [bad]}))
    with pytest.raises(ValueError) as raised:
        load_query_executions(path, account_id=ACCOUNT, region="us-east-1")
    # pydantic shortens long inputs in the middle, so check fragments, not the whole query
    assert not any(word in str(raised.value) for word in secret.split())
