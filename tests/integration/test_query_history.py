"""Athena query history through the workload store's public interface (M3 seam, #60)."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from data_finops.contracts.workload import QueryExecutionFlag
from data_finops.providers import athena
from data_finops.workload.store import QueryHistoryIngestResult, WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
QUERIES = FIXTURES / "athena" / "query-executions.json"
ACCOUNT, REGION = "123456789012", "us-east-1"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WorkloadStore]:
    with WorkloadStore(tmp_path / "data-finops.sqlite") as opened:
        yield opened


def test_ingesting_query_history_records_each_query_execution_as_reported(
    store: WorkloadStore,
) -> None:
    result = store.ingest_query_history(QUERIES, account_id=ACCOUNT, region=REGION)
    assert (result.accepted, result.duplicates, result.rejected) == (5, 0, ())
    assert result.changed_copies == 1
    query = store.query_execution(ACCOUNT, REGION, "q-001")
    assert query is not None
    assert (query.provider, query.account_id, query.region) == ("aws_athena", ACCOUNT, REGION)
    assert (query.workgroup, query.statement_type, query.state) == ("demo", "DML", "SUCCEEDED")
    assert query.submitted_at == datetime(2026, 10, 1, 1, 0, tzinfo=UTC)
    assert query.completed_at == datetime(2026, 10, 1, 1, 0, 10, tzinfo=UTC)
    assert (query.scanned_bytes, query.billable_bytes) == (1_000_000_000, None)


def _response(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"QueryExecutions": list(entries), "UnprocessedQueryExecutionIds": []}


def _entry(query_id: str = "q-100", **changes: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "QueryExecutionId": query_id,
        "Query": "select secret_column from orders where email = 'a@example.com'",
        "StatementType": "DML",
        "WorkGroup": "primary",
        "Status": {
            "State": "FAILED",
            "StateChangeReason": "COLUMN_NOT_FOUND: secret_column cannot be resolved",
            "SubmissionDateTime": "2026-10-01T01:00:00Z",
            "CompletionDateTime": "2026-10-01T01:00:01Z",
            "AthenaError": {"ErrorCategory": 2, "ErrorType": 1006, "ErrorMessage": "secret_column"},
        },
        "Statistics": {"DataScannedInBytes": 0, "DataManifestLocation": "s3://secret-bucket/"},
        "ExecutionParameters": ["'secret-literal'"],
        "ResultConfiguration": {"OutputLocation": "s3://secret-bucket/results/"},
        "SomeFieldAwsAddsLater": "secret",
    }
    return entry | changes


def _write(path: Path, document: Any) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _ingest(store: WorkloadStore, path: Path) -> QueryHistoryIngestResult:
    return store.ingest_query_history(path, account_id=ACCOUNT, region=REGION)


def test_reingesting_the_same_file_changes_nothing(store: WorkloadStore) -> None:
    _ingest(store, QUERIES)
    before = store.query_execution(ACCOUNT, REGION, "q-001")
    result = _ingest(store, QUERIES)
    assert (result.accepted, result.duplicates, result.changed_copies) == (0, 5, 0)
    assert store.query_execution(ACCOUNT, REGION, "q-001") == before


def test_a_query_execution_reported_with_other_content_is_kept_beside_it_and_flagged(
    store: WorkloadStore, tmp_path: Path
) -> None:
    _ingest(store, _write(tmp_path / "a.json", _response(_entry())))
    changed = _entry(Statistics={"DataScannedInBytes": 42})
    result = _ingest(store, _write(tmp_path / "b.json", _response(changed)))
    assert (result.accepted, result.changed_copies) == (1, 1)
    query = store.query_execution(ACCOUNT, REGION, "q-100")
    assert query is not None and query.flags == (QueryExecutionFlag.CONFLICTING_OBSERVATIONS,)
    assert len(store.stored_query_executions(ACCOUNT, REGION, "q-100")) == 2


def test_the_answer_does_not_depend_on_arrival_order(tmp_path: Path) -> None:
    first = _write(tmp_path / "a.json", _response(_entry()))
    second = _write(tmp_path / "b.json", _response(_entry(WorkGroup="other")))
    answers = []
    for order, name in (((first, second), "1.sqlite"), ((second, first), "2.sqlite")):
        with WorkloadStore(tmp_path / name) as store:
            for path in order:
                _ingest(store, path)
            answers.append(store.query_execution(ACCOUNT, REGION, "q-100"))
    assert answers[0] == answers[1]


def test_only_allowlisted_metadata_is_stored(store: WorkloadStore, tmp_path: Path) -> None:
    _ingest(store, _write(tmp_path / "h.json", _response(_entry())))
    (stored,) = store.stored_query_executions(ACCOUNT, REGION, "q-100")
    assert "secret" not in stored and "example.com" not in stored
    assert '"ErrorType":1006' in stored and '"WorkGroup":"primary"' in stored
    assert '"DataScannedInBytes":0' in stored


def test_a_file_recording_a_null_account_takes_the_one_given(
    store: WorkloadStore, tmp_path: Path
) -> None:
    document = {"account_id": None, "region": REGION, **_response(_entry())}
    assert _ingest(store, _write(tmp_path / "h.json", document)).accepted == 1


def test_entries_differing_only_in_removed_text_are_duplicates(
    store: WorkloadStore, tmp_path: Path
) -> None:
    _ingest(store, _write(tmp_path / "a.json", _response(_entry())))
    result = _ingest(store, _write(tmp_path / "b.json", _response(_entry(Query="select 2"))))
    assert (result.accepted, result.duplicates) == (0, 1)


def test_a_query_execution_without_statistics_has_no_scanned_bytes(
    store: WorkloadStore, tmp_path: Path
) -> None:
    entry = _entry()
    del entry["Statistics"]
    _ingest(store, _write(tmp_path / "h.json", _response(entry)))
    query = store.query_execution(ACCOUNT, REGION, "q-100")
    assert query is not None and query.scanned_bytes is None


def test_an_invalid_entry_is_rejected_by_position_and_the_rest_is_stored(
    store: WorkloadStore, tmp_path: Path
) -> None:
    bad = _entry("q-bad")
    bad["Status"]["SubmissionDateTime"] = "secret-not-a-time"
    path = _write(tmp_path / "h.json", _response(_entry("q-1"), bad, _entry("q-3")))
    result = _ingest(store, path)
    assert result.accepted == 2
    assert [(r.position, r.detail) for r in result.rejected] == [
        (2, "Status.SubmissionDateTime (datetime_from_date_parsing)")
    ]
    assert "secret" not in repr(result)
    assert store.query_execution(ACCOUNT, REGION, "q-bad") is None


@pytest.mark.parametrize(
    "document",
    [[], {"QueryExecution": {}}, {"QueryExecutions": {}}, "not json"],
)
def test_a_file_that_is_not_a_batch_get_query_execution_response_is_refused_whole(
    store: WorkloadStore, tmp_path: Path, document: Any
) -> None:
    path = tmp_path / "h.json"
    if document == "not json":
        path.write_text("{secret", encoding="utf-8")
    else:
        _write(path, document)
    result = _ingest(store, path)
    assert result.file_rejection == "not a BatchGetQueryExecution response"
    assert result.accepted == 0


def test_a_file_over_the_size_cap_is_refused_whole(
    store: WorkloadStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(athena, "MAX_HISTORY_BYTES", 1024)
    result = _ingest(store, QUERIES)
    assert (result.file_rejection, result.accepted) == ("too large", 0)
    assert store.query_execution(ACCOUNT, REGION, "q-001") is None


def test_a_file_recording_another_account_is_refused_whole(store: WorkloadStore) -> None:
    result = store.ingest_query_history(QUERIES, account_id="444455556666", region=REGION)
    assert result.file_rejection == "account_id disagrees with the one recorded in the file"
    assert store.query_execution("444455556666", REGION, "q-001") is None


def test_the_reference_workload_query_history_is_ingested_whole(store: WorkloadStore) -> None:
    path = FIXTURES / "reference-workload" / "athena" / "query-executions.json"
    result = store.ingest_query_history(path, account_id="111122223333", region=REGION)
    assert (result.accepted, result.rejected, result.file_rejection) == (20, (), None)
