"""Cost Dimension claims captured at ingest (spec 09 §Cost Dimensions, ADR 0031; #221), through
the workload store's public interface."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from data_finops.contracts.dimensions import CostDimensions
from data_finops.contracts.workload import LinkOutcome, QueryExecutionFlag
from data_finops.workload.store import WorkloadStore

ACCOUNT, REGION = "123456789012", "us-east-1"
CONFIG = CostDimensions.from_mapping(
    {
        "dimensions": {
            "team": {"sources": ["query_comment.team"]},
            "tenant": {"sources": ["query_comment.tenant_id"], "sensitive": True},
        }
    }
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WorkloadStore]:
    with WorkloadStore(tmp_path / "data-finops.sqlite") as opened:
        yield opened


def _history(tmp_path: Path, *comments: dict[str, Any], name: str = "history.json") -> Path:
    entries = [
        {
            "QueryExecutionId": f"q-{index}",
            "Query": f"-- /* {json.dumps(comment)} */\nselect 1",
            "StatementType": "DML",
            "WorkGroup": "primary",
            "Status": {
                "State": "SUCCEEDED",
                "SubmissionDateTime": "2026-10-01T01:00:00Z",
                "CompletionDateTime": "2026-10-01T01:00:10Z",
            },
            "Statistics": {"DataScannedInBytes": 1},
        }
        for index, comment in enumerate(comments)
    ]
    path = tmp_path / name
    path.write_text(json.dumps({"QueryExecutions": entries}), encoding="utf-8")
    return path


def _claims(store: WorkloadStore, query_id: str) -> dict[str, str]:
    query = store.query_execution(ACCOUNT, REGION, query_id)
    assert query is not None
    return dict(query.claims)


def test_only_the_configured_comment_keys_are_kept(store: WorkloadStore, tmp_path: Path) -> None:
    comment = {
        "app": "dbt",
        "node_id": "model.demo.orders",
        "invocation_id": "inv-1",
        "team": "finance",
        "tenant_id": "t-9",
        "owner_email": "someone@example.com",
    }
    store.ingest_query_history(
        _history(tmp_path, comment), account_id=ACCOUNT, region=REGION, dimensions=CONFIG
    )

    assert _claims(store, "q-0") == {"team": "finance", "tenant_id": "t-9"}
    stored = store.stored_query_executions(ACCOUNT, REGION, "q-0")
    assert "someone@example.com" not in "".join(stored)


def test_no_configuration_keeps_no_claims(store: WorkloadStore, tmp_path: Path) -> None:
    store.ingest_query_history(
        _history(tmp_path, {"team": "finance"}), account_id=ACCOUNT, region=REGION
    )

    assert _claims(store, "q-0") == {}


def test_a_claim_never_creates_an_identity_link(store: WorkloadStore, tmp_path: Path) -> None:
    store.ingest_query_history(
        _history(tmp_path, {"team": "finance"}),
        account_id=ACCOUNT,
        region=REGION,
        dimensions=CONFIG,
    )
    query = store.query_execution(ACCOUNT, REGION, "q-0")
    assert query is not None

    assert dict(query.claims) == {"team": "finance"}
    assert query.correlation is None
    link = store.model_execution_of_query(ACCOUNT, REGION, "q-0")
    assert link is not None
    assert link.outcome is LinkOutcome.NO_EVIDENCE


def test_a_value_over_128_characters_is_dropped_and_counted(
    store: WorkloadStore, tmp_path: Path
) -> None:
    history = _history(
        tmp_path,
        {"team": "x" * 128, "tenant_id": "y" * 129},
        {"team": 7, "tenant_id": ""},
    )
    result = store.ingest_query_history(
        history, account_id=ACCOUNT, region=REGION, dimensions=CONFIG
    )

    assert _claims(store, "q-0") == {"team": "x" * 128}
    assert _claims(store, "q-1") == {}
    assert result.claims_dropped == 3


def test_a_key_added_later_applies_only_to_queries_ingested_after_it(
    store: WorkloadStore, tmp_path: Path
) -> None:
    history = _history(tmp_path, {"team": "finance", "tenant_id": "t-9"})
    team_only = CostDimensions.from_mapping(
        {"dimensions": {"team": {"sources": ["query_comment.team"]}}}
    )
    store.ingest_query_history(history, account_id=ACCOUNT, region=REGION, dimensions=team_only)
    again = store.ingest_query_history(
        history, account_id=ACCOUNT, region=REGION, dimensions=CONFIG
    )
    query = store.query_execution(ACCOUNT, REGION, "q-0")
    assert query is not None

    assert (again.accepted, again.duplicates) == (0, 1)
    assert dict(query.claims) == {"team": "finance"}
    assert QueryExecutionFlag.CONFLICTING_OBSERVATIONS not in query.flags


def test_an_ingest_again_counts_no_dropped_claims(store: WorkloadStore, tmp_path: Path) -> None:
    history = _history(tmp_path, {"team": "x" * 129})
    first = store.ingest_query_history(
        history, account_id=ACCOUNT, region=REGION, dimensions=CONFIG
    )
    again = store.ingest_query_history(
        history, account_id=ACCOUNT, region=REGION, dimensions=CONFIG
    )

    assert (first.claims_dropped, again.claims_dropped) == (1, 0)


def test_a_database_from_before_claims_opens_and_keeps_its_query_executions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "older.sqlite"
    with WorkloadStore(path) as store:
        store.ingest_query_history(
            _history(tmp_path, {"team": "finance"}), account_id=ACCOUNT, region=REGION
        )
    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE workload_query_execution DROP COLUMN claims_json")
    connection.commit()
    connection.close()

    with WorkloadStore(path) as store:
        query = store.query_execution(ACCOUNT, REGION, "q-0")
    assert query is not None and dict(query.claims) == {}
