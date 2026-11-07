"""The Collection Run (spec 08 seam 2; GLOSSARY): sources fetched through stubbed AWS clients,
ingested into a temporary database, asserted only through `reporting` and the summary.
"""

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from aws_stubs import (
    ACCOUNT,
    BUCKET,
    REGION,
    WORKGROUP,
    athena_client,
    expect_batch,
    expect_denied,
    expect_get,
    expect_history,
    expect_listing,
    s3_client,
    stubbed,
)
from fixture_database import EXPORT, FIXTURES
from fixture_database import OTHER_ACCOUNT as OTHER

from data_finops.collection import (
    AwsClients,
    CollectionSettings,
    CollectionSummary,
    run_collection,
)
from data_finops.collection.run import MAX_INBOX_FILE_BYTES
from data_finops.ledger import Ledger
from data_finops.lineage.validation import MAX_EVENT_BYTES
from data_finops.reporting import Reporting
from data_finops.workload.store import WorkloadStore

EVENTS = (FIXTURES / "openlineage" / "events.json").read_bytes()
DBT = "inbox/dbt/"
OPENLINEAGE = "inbox/openlineage/"
SETTINGS = CollectionSettings(
    bucket=BUCKET, region=REGION, account_id=ACCOUNT, workgroups=(WORKGROUP,)
)


def _run(db: Path, s3: Any) -> CollectionSummary:
    """A Collection Run whose Athena history is empty: the Inbox sources alone."""
    athena = athena_client()
    with stubbed(athena) as stubber:
        expect_history(stubber, [])
        return run_collection(db, SETTINGS, clients=AwsClients(s3=s3, athena=athena))


def test_an_inbox_event_file_is_ingested_once_and_shows_in_lineage(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    s3 = s3_client()
    key = f"{OPENLINEAGE}dbt-20261101.json"
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: EVENTS})
        expect_get(stubber, key, EVENTS)
        first = _run(db, s3)
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: EVENTS})  # listed again, never fetched
        second = _run(db, s3)

    assert first.sources["openlineage"].stored > 0
    assert (first.sources["openlineage"].rejected, first.exit_code) == ([], 0)
    assert (second.sources["openlineage"].stored, second.exit_code) == (0, 0)
    with Reporting(db) as reporting:
        roots = reporting.root_runs()
    assert roots.total > 0


def _with_bill(tmp_path: Path) -> Path:
    db = tmp_path / "data-finops.sqlite"
    with Ledger(db) as ledger:
        ledger.import_snapshot(EXPORT)
    return db


def test_a_rejected_event_is_named_by_key_and_position_and_the_run_still_calculates(
    tmp_path: Path,
) -> None:
    db = _with_bill(tmp_path)
    good = json.loads(EVENTS)[0]
    lines = [json.dumps(good), json.dumps({"secret-field": "secret-value"})]
    data = "\n".join(lines).encode("utf-8")
    key = f"{OPENLINEAGE}mixed.jsonl"
    s3 = s3_client()
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: data})
        expect_get(stubber, key, data)
        summary = _run(db, s3)

    source = summary.sources["openlineage"]
    assert source.stored == 1
    assert [(r.item, r.reason.split(":")[0]) for r in source.rejected] == [
        (f"{key}#2", "invalid RunEvent")
    ]
    assert summary.exit_code == 1
    assert "secret" not in summary.model_dump_json()
    (run_id,) = summary.calculation_run_ids
    with Reporting(db) as reporting:
        assert reporting.provenance(calculation_run_id=run_id).calculation_run_id == run_id


def test_an_inbox_file_over_the_cap_is_rejected_without_being_downloaded(tmp_path: Path) -> None:
    key = f"{OPENLINEAGE}huge.json"
    s3 = s3_client()
    with stubbed(s3) as stubber:  # no GetObject is expected: the Stubber fails on one
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: b""}, sizes={key: MAX_INBOX_FILE_BYTES + 1})
        summary = _run(tmp_path / "data-finops.sqlite", s3)
    assert [(r.item, r.reason) for r in summary.sources["openlineage"].rejected] == [
        (key, "too large")
    ]
    assert summary.exit_code == 1
    with stubbed(s3) as stubber:  # rejected once, never again
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: b""}, sizes={key: MAX_INBOX_FILE_BYTES + 1})
        again = _run(tmp_path / "data-finops.sqlite", s3)
    assert (again.sources["openlineage"].rejected, again.exit_code) == ([], 0)


def test_an_unreadable_inbox_fails_the_run_after_calculating_without_aws_text(
    tmp_path: Path,
) -> None:
    db = _with_bill(tmp_path)
    s3 = s3_client()
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_denied(stubber, "list_objects_v2")
        summary = _run(db, s3)
    assert summary.sources["openlineage"].failed == "AccessDenied"
    assert summary.exit_code == 2
    assert len(summary.calculation_run_ids) == 1
    assert "secret" not in summary.model_dump_json()


def test_an_inbox_object_overwritten_with_new_content_is_collected_again(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    key = f"{OPENLINEAGE}dbt.json"
    first, second = (json.dumps([e]).encode("utf-8") for e in json.loads(EVENTS)[:2])
    s3 = s3_client()
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: first}, etags={key: '"v1"'})
        expect_get(stubber, key, first, version='"v1"')
        _run(db, s3)
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: second}, etags={key: '"v2"'})
        expect_get(stubber, key, second, version='"v2"')
        summary = _run(db, s3)
    assert summary.sources["openlineage"].stored == 1


MANIFEST = (FIXTURES / "dbt" / "manifest.json").read_bytes()
RUN_RESULTS = (FIXTURES / "dbt" / "run_results.json").read_bytes()


def _with_bill_and_queries(tmp_path: Path) -> Path:
    db = _with_bill(tmp_path)
    with WorkloadStore(db) as store:
        store.ingest_query_history(
            FIXTURES / "athena" / "query-executions.json", account_id=ACCOUNT, region=REGION
        )
    return db


def test_dbt_artifacts_from_the_inbox_explain_a_cost_down_to_the_model(tmp_path: Path) -> None:
    db = _with_bill_and_queries(tmp_path)
    folder = f"{DBT}inv-001/"
    dbt = {f"{folder}manifest.json": MANIFEST, f"{folder}run_results.json": RUN_RESULTS}
    s3 = s3_client()
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, dbt)
        for key, data in dbt.items():
            expect_get(stubber, key, data)
        expect_listing(stubber, OPENLINEAGE, {f"{OPENLINEAGE}run.json": EVENTS})
        expect_get(stubber, f"{OPENLINEAGE}run.json", EVENTS)
        summary = _run(db, s3)

    assert list(summary.sources) == ["dbt", "openlineage", "athena"]
    assert (summary.sources["dbt"].stored, summary.exit_code) == (2, 0)
    (run_id,) = summary.calculation_run_ids
    with Reporting(db) as reporting:
        (entry,) = reporting.allocations(calculation_run_id=run_id, target_id="q-001").items[:1]
        model = reporting.explanation(entry.ref).model_execution
    assert model is not None and model.execution is not None and model.dbt_node is not None
    assert (model.invocation_id, model.node_id) == ("inv-001", "model.demo.orders")


def test_an_invocation_folder_waits_until_both_artifacts_have_arrived(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    manifest, run_results = f"{DBT}inv-001/manifest.json", f"{DBT}inv-001/run_results.json"
    s3 = s3_client()
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {manifest: MANIFEST})  # nothing fetched yet
        expect_listing(stubber, OPENLINEAGE, {})
        waiting = _run(db, s3)
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {manifest: MANIFEST, run_results: RUN_RESULTS})
        expect_get(stubber, manifest, MANIFEST)
        expect_get(stubber, run_results, RUN_RESULTS)
        expect_listing(stubber, OPENLINEAGE, {})
        arrived = _run(db, s3)

    assert (waiting.sources["dbt"].stored, waiting.exit_code) == (0, 0)
    assert waiting.warnings == [f"{DBT}inv-001/: waiting for run_results.json"]
    assert (arrived.sources["dbt"].stored, arrived.warnings) == (2, [])


def test_a_dbt_artifact_over_the_cap_or_unreadable_is_rejected_by_key(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    huge, bad = f"{DBT}inv-001/manifest.json", f"{DBT}inv-001/run_results.json"
    secret = b'{"secret": "not a dbt artifact"}'
    s3 = s3_client()
    with stubbed(s3) as stubber:  # the huge manifest is never fetched
        expect_listing(
            stubber, DBT, {huge: b"", bad: secret}, sizes={huge: MAX_INBOX_FILE_BYTES + 1}
        )
        expect_get(stubber, bad, secret)
        expect_listing(stubber, OPENLINEAGE, {})
        summary = _run(db, s3)

    dbt = summary.sources["dbt"]
    assert dbt.stored == 0
    assert [r.item for r in dbt.rejected] == [huge, bad]
    assert dbt.rejected[0].reason == "too large"
    assert summary.exit_code == 1
    assert "secret" not in summary.model_dump_json()


MIXED = (FIXTURES / "openlineage" / "invalid" / "mixed.jsonl").read_bytes().split(b"\n")


def _collect_one(db: Path, key: str, data: bytes) -> CollectionSummary:
    s3 = s3_client()
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: data})
        expect_get(stubber, key, data)
        return _run(db, s3)


def test_a_changed_copy_of_an_event_is_stored_once_and_counted_once(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    event = json.loads(EVENTS)[0]
    changed = event | {"producer": "https://example.invalid/another-producer"}
    _collect_one(db, f"{OPENLINEAGE}a.json", json.dumps(event).encode("utf-8"))
    summary = _collect_one(db, f"{OPENLINEAGE}b.json", json.dumps(changed).encode("utf-8"))
    assert (summary.sources["openlineage"].stored, summary.exit_code) == (1, 0)


def test_unsupported_events_are_listed_by_key_and_position_without_failing_the_run(
    tmp_path: Path,
) -> None:
    key = f"{OPENLINEAGE}kinds.jsonl"
    data = b"\n".join([json.dumps(json.loads(EVENTS)[0]).encode("utf-8"), MIXED[5], MIXED[6]])
    summary = _collect_one(tmp_path / "data-finops.sqlite", key, data)
    assert summary.warnings == [
        f"{key}#2: unsupported JobEvent",
        f"{key}#3: unsupported DatasetEvent",
    ]
    assert (summary.sources["openlineage"].rejected, summary.exit_code) == ([], 0)


def test_an_event_over_the_event_cap_is_rejected_by_key_and_position(tmp_path: Path) -> None:
    key = f"{OPENLINEAGE}big.jsonl"
    event = json.loads(EVENTS)[0]
    big = event | {"producer": "x" * MAX_EVENT_BYTES}
    data = b"\n".join([json.dumps(event).encode("utf-8"), json.dumps(big).encode("utf-8")])
    summary = _collect_one(tmp_path / "data-finops.sqlite", key, data)
    assert [(r.item, r.reason) for r in summary.sources["openlineage"].rejected] == [
        (f"{key}#2", "too large")
    ]


HISTORY = json.loads((FIXTURES / "athena" / "query-executions.json").read_text(encoding="utf-8"))
Q1, Q2 = HISTORY["QueryExecutions"]


def _run_history(
    db: Path,
    pages: list[list[str]],
    fetched: list[dict[str, Any]],
    *,
    settings: CollectionSettings = SETTINGS,
    more: bool = False,
) -> CollectionSummary:
    """A Collection Run with empty Inboxes and this Athena history, page by page; with
    `more`, the last page expected says another follows."""
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena) as stubber:
        expect_listing(s3_stub, DBT, {})
        expect_listing(s3_stub, OPENLINEAGE, {})
        for number, ids in enumerate(pages):
            last = number + 1 == len(pages)
            expect_history(
                stubber,
                ids,
                token=f"t{number}" if number else None,
                next_token=f"t{number + 1}" if not last or more else None,
            )
        if fetched:
            expect_batch(stubber, fetched)
        return run_collection(db, settings, clients=AwsClients(s3=s3, athena=athena))


def test_athena_history_is_collected_and_explains_a_metered_share(tmp_path: Path) -> None:
    db = _with_bill(tmp_path)
    summary = _run_history(db, [["q-001", "q-002"]], [Q1, Q2])
    again = _run_history(db, [["q-001", "q-002"]], [])  # both stored: nothing fetched

    assert list(summary.sources) == ["dbt", "openlineage", "athena"]
    assert (summary.sources["athena"].stored, summary.exit_code) == (2, 0)
    assert (again.sources["athena"].stored, again.exit_code) == (0, 0)
    assert "dbt_version" not in summary.model_dump_json()  # the query text never surfaces
    (run_id,) = summary.calculation_run_ids
    with Reporting(db) as reporting:
        (entry,) = reporting.allocations(calculation_run_id=run_id, target_id="q-001").items[:1]
        query = reporting.explanation(entry.ref).query_execution
    assert query is not None and (query.account_id, query.workgroup) == (ACCOUNT, WORKGROUP)


def _as(entry: dict[str, Any], query_id: str, state: str) -> dict[str, Any]:
    status = {"State": state, "SubmissionDateTime": entry["Status"]["SubmissionDateTime"]}
    if state not in ("QUEUED", "RUNNING"):
        status["CompletionDateTime"] = entry["Status"]["CompletionDateTime"]
    return entry | {"QueryExecutionId": query_id, "Status": status}


@pytest.mark.parametrize("state", ["QUEUED", "RUNNING"])
def test_an_unfinished_query_is_left_for_a_later_run_and_collected_once_finished(
    tmp_path: Path, state: str
) -> None:
    db = tmp_path / "data-finops.sqlite"
    running, finished = _as(Q2, "q-late", state), _as(Q2, "q-late", "SUCCEEDED")
    first = _run_history(db, [["q-001", "q-late"]], [Q1, running])
    second = _run_history(db, [["q-001", "q-late"]], [finished])
    assert (first.sources["athena"].stored, second.sources["athena"].stored) == (1, 1)
    assert second.sources["athena"].duplicates == 0


def test_the_history_page_cap_stops_listing_and_says_so(tmp_path: Path) -> None:
    capped = dataclasses.replace(SETTINGS, max_history_pages=1)
    summary = _run_history(
        tmp_path / "data-finops.sqlite", [["q-001"]], [Q1], settings=capped, more=True
    )
    assert summary.sources["athena"].stored == 1
    assert summary.warnings == [
        f"workgroup {WORKGROUP}: history page cap of 1 reached; older queries were not listed"
    ]
    assert summary.exit_code == 0


GAP = (
    f"workgroup {WORKGROUP}: no overlap with stored history;"
    " queries older than Athena's 45-day history may be missing"
)


def test_history_with_no_overlap_with_what_is_stored_warns_of_a_gap(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    _run_history(db, [["q-001"]], [Q1])
    later = _as(Q2, "q-new", "SUCCEEDED")
    gap = _run_history(db, [["q-new"]], [later])  # q-001 has left Athena's history
    assert gap.warnings == [GAP]
    assert gap.exit_code == 0


def test_no_gap_is_reported_with_an_overlap_or_an_unfinished_listing(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    first = _run_history(db, [["q-001"]], [Q1])  # nothing stored before: no gap either
    overlap = _run_history(db, [["q-002", "q-001"]], [Q2])
    capped = dataclasses.replace(SETTINGS, max_history_pages=1)
    later = _as(Q2, "q-new", "SUCCEEDED")
    unfinished = _run_history(db, [["q-new"]], [later], settings=capped, more=True)
    assert (first.warnings, overlap.warnings) == ([], [])
    assert GAP not in unfinished.warnings


def test_an_idle_workgroup_is_no_gap(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    _run_history(db, [["q-001"]], [Q1])
    idle = _run_history(db, [[]], [])  # nothing ran in 45 days, so nothing expired unread
    assert idle.warnings == []


def test_a_rejected_or_unprocessed_query_is_named_by_workgroup_and_id(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    broken = Q2 | {"QueryExecutionId": "q-bad", "Status": {"State": "SUCCEEDED"}}
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena) as stubber:
        expect_listing(s3_stub, DBT, {})
        expect_listing(s3_stub, OPENLINEAGE, {})
        expect_history(stubber, ["q-001", "q-bad", "q-gone"])
        expect_batch(stubber, [Q1, broken], unprocessed={"q-gone": "INVALID_QUERY_EXECUTION_ID"})
        summary = run_collection(db, SETTINGS, clients=AwsClients(s3=s3, athena=athena))

    athena_summary = summary.sources["athena"]
    assert athena_summary.stored == 1
    assert [(r.item, r.reason.split(" ")[0]) for r in athena_summary.rejected] == [
        (f"{WORKGROUP}/q-bad", "Status.SubmissionDateTime")
    ]
    assert summary.warnings == [f"{WORKGROUP}/q-gone: unprocessed (INVALID_QUERY_EXECUTION_ID)"]
    assert summary.exit_code == 1
    assert "secret" not in summary.model_dump_json()


def test_history_athena_refuses_fails_the_source_and_the_run_still_calculates(
    tmp_path: Path,
) -> None:
    db = _with_bill(tmp_path)
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena) as stubber:
        expect_listing(s3_stub, DBT, {})
        expect_listing(s3_stub, OPENLINEAGE, {})
        expect_denied(stubber, "list_query_executions")
        summary = run_collection(db, SETTINGS, clients=AwsClients(s3=s3, athena=athena))
    assert summary.sources["athena"].failed == "AccessDenied"
    assert (summary.exit_code, len(summary.calculation_run_ids)) == (2, 1)


def test_a_query_stored_for_another_account_is_still_collected_for_this_one(
    tmp_path: Path,
) -> None:
    db = tmp_path / "data-finops.sqlite"
    elsewhere = tmp_path / "other-account.json"
    elsewhere.write_text(json.dumps(HISTORY | {"account_id": OTHER}), encoding="utf-8")
    with WorkloadStore(db) as store:
        result = store.ingest_query_history(elsewhere, account_id=OTHER, region=REGION)
    assert result.accepted == 2
    summary = _run_history(db, [["q-001"]], [Q1])  # same ID, other account: not seen here
    assert summary.sources["athena"].stored == 1
    assert summary.warnings == []  # nothing stored for this account's workgroup, so no gap
