"""The Collection Run (spec 08 seam 2; GLOSSARY): sources fetched through stubbed AWS clients,
ingested into a temporary database, asserted only through `reporting` and the summary.
"""

import dataclasses
import json
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from aws_stubs import (
    ACCOUNT,
    BUCKET,
    EXPORT_BUCKET,
    EXPORT_NAME,
    EXPORT_PREFIX,
    METADATA,
    REGION,
    WORKGROUP,
    athena_client,
    expect_batch,
    expect_denied,
    expect_get,
    expect_history,
    expect_listing,
    expect_prefixes,
    s3_client,
    stubbed,
)
from botocore.stub import Stubber
from delivery_files import write_delivery
from fixture_database import EXPORT, FIXTURES
from fixture_database import OTHER_ACCOUNT as OTHER

from data_finops.collection import (
    AwsClients,
    CollectionSettings,
    CollectionSummary,
    run_collection,
)
from data_finops.collection.run import (
    MAX_DATA_FILE_BYTES,
    MAX_DELIVERY_BYTES,
    MAX_INBOX_FILE_BYTES,
)
from data_finops.ledger import Ledger
from data_finops.lineage.validation import MAX_EVENT_BYTES
from data_finops.providers.aws.athena import UNFINISHED
from data_finops.reporting import Reporting
from data_finops.workload.store import WorkloadStore

EVENTS = (FIXTURES / "openlineage" / "events.json").read_bytes()
DBT = "inbox/dbt/"
OPENLINEAGE = "inbox/openlineage/"
SETTINGS = CollectionSettings(
    bucket=BUCKET,
    region=REGION,
    account_id=ACCOUNT,
    workgroups=(WORKGROUP,),
    export_bucket=EXPORT_BUCKET,
    export_prefix=EXPORT_PREFIX,
    export_name=EXPORT_NAME,
)


def _run(db: Path, s3: Any, s3_stub: Stubber) -> CollectionSummary:
    """A Collection Run whose Athena history and export are empty: the Inbox sources alone.
    `s3_stub` already expects the Inbox calls; the export's listing is expected after them."""
    expect_prefixes(s3_stub, METADATA, [])
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
        first = _run(db, s3, stubber)
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: EVENTS})  # listed again, never fetched
        second = _run(db, s3, stubber)

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
        summary = _run(db, s3, stubber)

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
        summary = _run(tmp_path / "data-finops.sqlite", s3, stubber)
    assert [(r.item, r.reason) for r in summary.sources["openlineage"].rejected] == [
        (key, "too large")
    ]
    assert summary.exit_code == 1
    with stubbed(s3) as stubber:  # rejected once, never again
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: b""}, sizes={key: MAX_INBOX_FILE_BYTES + 1})
        again = _run(tmp_path / "data-finops.sqlite", s3, stubber)
    assert (again.sources["openlineage"].rejected, again.exit_code) == ([], 0)


def test_an_unreadable_inbox_fails_the_run_after_calculating_without_aws_text(
    tmp_path: Path,
) -> None:
    db = _with_bill(tmp_path)
    s3 = s3_client()
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_denied(stubber, "list_objects_v2")
        summary = _run(db, s3, stubber)
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
        _run(db, s3, stubber)
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: second}, etags={key: '"v2"'})
        expect_get(stubber, key, second, version='"v2"')
        summary = _run(db, s3, stubber)
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
        summary = _run(db, s3, stubber)

    assert list(summary.sources) == ["dbt", "openlineage", "athena", "focus"]
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
        waiting = _run(db, s3, stubber)
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {manifest: MANIFEST, run_results: RUN_RESULTS})
        expect_get(stubber, manifest, MANIFEST)
        expect_get(stubber, run_results, RUN_RESULTS)
        expect_listing(stubber, OPENLINEAGE, {})
        arrived = _run(db, s3, stubber)

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
        summary = _run(db, s3, stubber)

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
        return _run(db, s3, stubber)


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
        expect_prefixes(s3_stub, METADATA, [])
        return run_collection(db, settings, clients=AwsClients(s3=s3, athena=athena))


def test_athena_history_is_collected_and_explains_a_metered_share(tmp_path: Path) -> None:
    db = _with_bill(tmp_path)
    summary = _run_history(db, [["q-001", "q-002"]], [Q1, Q2])
    again = _run_history(db, [["q-001", "q-002"]], [])  # both stored: nothing fetched

    assert list(summary.sources) == ["dbt", "openlineage", "athena", "focus"]
    assert (summary.sources["athena"].stored, summary.exit_code) == (2, 0)
    assert (again.sources["athena"].stored, again.exit_code) == (0, 0)
    assert "dbt_version" not in summary.model_dump_json()  # the query text never surfaces
    (run_id,) = summary.calculation_run_ids
    with Reporting(db) as reporting:
        (entry,) = reporting.allocations(calculation_run_id=run_id, target_id="q-001").items[:1]
        query = reporting.explanation(entry.ref).query_execution
    assert query is not None and (query.account_id, query.workgroup) == (ACCOUNT, WORKGROUP)


def _with_state(entry: dict[str, Any], query_id: str, state: str) -> dict[str, Any]:
    status = {"State": state, "SubmissionDateTime": entry["Status"]["SubmissionDateTime"]}
    if state not in UNFINISHED:
        status["CompletionDateTime"] = entry["Status"]["CompletionDateTime"]
    return entry | {"QueryExecutionId": query_id, "Status": status}


@pytest.mark.parametrize("state", ["QUEUED", "RUNNING"])
def test_an_unfinished_query_is_left_for_a_later_run_and_collected_once_finished(
    tmp_path: Path, state: str
) -> None:
    db = tmp_path / "data-finops.sqlite"
    running, finished = _with_state(Q2, "q-late", state), _with_state(Q2, "q-late", "SUCCEEDED")
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
    later = _with_state(Q2, "q-new", "SUCCEEDED")
    gap = _run_history(db, [["q-new"]], [later])  # q-001 has left Athena's history
    assert gap.warnings == [GAP]
    assert gap.exit_code == 0


def test_no_gap_is_reported_with_an_overlap_or_an_unfinished_listing(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    first = _run_history(db, [["q-001"]], [Q1])  # nothing stored before: no gap either
    overlap = _run_history(db, [["q-002", "q-001"]], [Q2])
    capped = dataclasses.replace(SETTINGS, max_history_pages=1)
    later = _with_state(Q2, "q-new", "SUCCEEDED")
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
        expect_prefixes(s3_stub, METADATA, [])
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
        expect_prefixes(s3_stub, METADATA, [])
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


def test_a_failed_query_is_collected_without_its_sql_or_free_text(tmp_path: Path) -> None:
    db = _with_bill(tmp_path)
    failed = Q1 | {
        "Status": {
            "State": "FAILED",
            "SubmissionDateTime": Q1["Status"]["SubmissionDateTime"],
            "CompletionDateTime": Q1["Status"]["CompletionDateTime"],
            "StateChangeReason": "secret reason naming a table",
            "AthenaError": {"ErrorCategory": 2, "ErrorType": 1001, "ErrorMessage": "secret error"},
        },
        "ExecutionParameters": ["secret-parameter"],
    }
    summary = _run_history(db, [["q-001"]], [failed])
    assert summary.sources["athena"].stored == 1
    # Not billable, so no Allocation explains it: read it as `reporting` does, from the store.
    with WorkloadStore(db) as store:
        query = store.query_execution(ACCOUNT, REGION, "q-001")
    assert query is not None and query.state == "FAILED"
    served = query.model_dump_json()
    assert "secret" not in served
    assert "raw_orders" not in served  # the SQL after the Trusted Query Comment's line


ROOT = f"{EXPORT_PREFIX}/{EXPORT_NAME}/"
MANIFEST_NAME = f"{EXPORT_NAME}-Manifest.json"
FOCUS_MANIFEST_NAME = f"{EXPORT_NAME}-Manifest-FOCUS.json"


class Delivery(NamedTuple):
    """One Delivery as its S3 objects: metadata keys and data keys, each to its content."""

    folder: str  # billing_period=YYYY-MM/<CreatedAt>-<executionId>
    metadata: dict[str, bytes]
    data: dict[str, bytes]


def _delivery(tmp_path: Path, folder: str, costs: list[float]) -> Delivery:
    execution = folder.rsplit("-", 5)[-5:]
    local = tmp_path / folder.replace(":", "_").replace("/", "_")
    write_delivery(
        local,
        folder=folder,
        execution="-".join(execution),
        files={f"{EXPORT_NAME}-00001.snappy.parquet": costs},
        bucket=EXPORT_BUCKET,
    )
    # delivery_files names its files after its own example export; rename to this one's
    manifest = json.loads((local / "example-focus-1-2-Manifest.json").read_text(encoding="utf-8"))
    return Delivery(
        folder,
        {
            f"{ROOT}metadata/{folder}/{MANIFEST_NAME}": json.dumps(manifest).encode("utf-8"),
            f"{ROOT}metadata/{folder}/{FOCUS_MANIFEST_NAME}": (
                local / "example-focus-1-2-Manifest-FOCUS.json"
            ).read_bytes(),
        },
        {
            f"{ROOT}data/{folder}/{name}": (local / name).read_bytes()
            for name in (uri.rsplit("/", 1)[1] for uri in manifest["dataFiles"])
        },
    )


def _expect_ingest(stubber: Stubber, delivery: Delivery) -> None:
    """The calls that download one Delivery: its manifests, its data listing, its data."""
    for key, data in delivery.metadata.items():
        expect_get(stubber, key, data, bucket=EXPORT_BUCKET)
    expect_listing(stubber, f"{ROOT}data/{delivery.folder}/", delivery.data, bucket=EXPORT_BUCKET)
    for key, data in delivery.data.items():
        expect_get(stubber, key, data, bucket=EXPORT_BUCKET)


def _run_exports(
    db: Path,
    periods: dict[str, list[Delivery]],
    ingested: list[Delivery],
    *,
    settings: CollectionSettings = SETTINGS,
) -> CollectionSummary:
    """A Collection Run whose only source with content is the export: `periods` lists each
    Billing Period's Deliveries; `ingested` are those the run is expected to download."""
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena) as stubber:
        expect_listing(s3_stub, DBT, {})
        expect_listing(s3_stub, OPENLINEAGE, {})
        expect_history(stubber, [])
        expect_prefixes(s3_stub, f"{ROOT}metadata/", list(periods))
        for period in list(periods)[-settings.billing_lookback_periods :]:
            metadata = {k: v for d in periods[period] for k, v in d.metadata.items()}
            expect_listing(s3_stub, f"{ROOT}metadata/{period}/", metadata, bucket=EXPORT_BUCKET)
            for delivery in ingested:
                if delivery.folder.startswith(period):
                    _expect_ingest(s3_stub, delivery)
        return run_collection(db, settings, clients=AwsClients(s3=s3, athena=athena))


NOV = "billing_period=2026-11"
FIRST = f"{NOV}/2026-11-05T13:06:53.949Z-0771642f-2e82-3988-b27a-f8171b7deb3d"
SECOND = f"{NOV}/2026-11-05T18:43:54.994Z-980aa446-5e52-3927-9bca-c5774fc47fe8"


def test_a_delivery_becomes_the_current_export_snapshot_and_is_calculated(
    tmp_path: Path,
) -> None:
    db = tmp_path / "data-finops.sqlite"
    delivery = _delivery(tmp_path, SECOND, [0.0003])
    summary = _run_exports(db, {NOV: [delivery]}, [delivery])
    again = _run_exports(db, {NOV: [delivery]}, [])  # not newer than the current snapshot

    assert list(summary.sources) == ["dbt", "openlineage", "athena", "focus"]
    assert (summary.sources["focus"].stored, summary.exit_code) == (1, 0)
    assert (again.sources["focus"].stored, again.exit_code) == (0, 0)
    (run_id,) = summary.calculation_run_ids
    with Reporting(db) as reporting:
        provenance = reporting.provenance(calculation_run_id=run_id)
    assert provenance.export_snapshot.published_at.isoformat() == "2026-11-05T18:43:54.994000+00:00"


def _current_billed(db: Path, run_id: str) -> str:
    with Reporting(db) as reporting:
        provenance = reporting.provenance(calculation_run_id=run_id)
    assert provenance.export_snapshot is not None
    return provenance.export_snapshot.snapshot_id


def test_every_newer_delivery_is_ingested_oldest_first_so_the_newest_is_current(
    tmp_path: Path,
) -> None:
    db = tmp_path / "data-finops.sqlite"
    first, second = _delivery(tmp_path, FIRST, [0.0001]), _delivery(tmp_path, SECOND, [0.0003])
    summary = _run_exports(db, {NOV: [second, first]}, [first, second])
    assert summary.sources["focus"].stored == 2
    (run_id,) = summary.calculation_run_ids
    assert _current_billed(db, run_id).endswith("980aa446-5e52-3927-9bca-c5774fc47fe8")


def test_a_delivery_older_than_the_current_snapshot_is_never_downloaded(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    first, second = _delivery(tmp_path, FIRST, [0.0001]), _delivery(tmp_path, SECOND, [0.0003])
    _run_exports(db, {NOV: [second]}, [second])
    late = _run_exports(db, {NOV: [first, second]}, [])  # FIRST appears late: superseded
    assert (late.sources["focus"].stored, late.sources["focus"].duplicates) == (0, 0)


def test_an_overwrite_mode_layout_is_refused_with_a_reason(tmp_path: Path) -> None:
    created = _delivery(tmp_path, SECOND, [0.0003])
    flat = Delivery(  # Data Exports' "overwrite" delivery: no <CreatedAt>-<executionId> folder
        NOV,
        {key.replace(SECOND, NOV): data for key, data in created.metadata.items()},
        {},
    )
    summary = _run_exports(tmp_path / "data-finops.sqlite", {NOV: [flat]}, [])
    assert summary.sources["focus"].stored == 0
    assert summary.warnings == [
        f"{NOV}: an overwrite-mode Delivery is refused; set the export to create new files"
    ]
    assert summary.exit_code == 0


def _with_manifest(delivery: Delivery, change: Any) -> Delivery:
    """The Delivery with its manifest's JSON passed through `change`."""
    (key,) = (k for k in delivery.metadata if k.endswith(MANIFEST_NAME))
    manifest = json.loads(delivery.metadata[key])
    change(manifest)
    return delivery._replace(metadata=delivery.metadata | {key: json.dumps(manifest).encode()})


def _expect_manifests(stubber: Stubber, delivery: Delivery) -> None:
    for key, data in delivery.metadata.items():
        expect_get(stubber, key, data, bucket=EXPORT_BUCKET)


def test_a_manifest_naming_data_outside_the_export_is_rejected_unread(tmp_path: Path) -> None:
    def elsewhere(manifest: dict[str, Any]) -> None:
        manifest["dataFiles"] = [
            uri.replace(f"s3://{EXPORT_BUCKET}/", "s3://secret-other-bucket/")
            for uri in manifest["dataFiles"]
        ]

    delivery = _with_manifest(_delivery(tmp_path, SECOND, [0.0003]), elsewhere)
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena) as stubber:
        expect_listing(s3_stub, DBT, {})
        expect_listing(s3_stub, OPENLINEAGE, {})
        expect_history(stubber, [])
        expect_prefixes(s3_stub, f"{ROOT}metadata/", [NOV])
        expect_listing(s3_stub, f"{ROOT}metadata/{NOV}/", delivery.metadata, bucket=EXPORT_BUCKET)
        _expect_manifests(s3_stub, delivery)  # then nothing: no data is listed or fetched
        summary = run_collection(
            tmp_path / "data-finops.sqlite", SETTINGS, clients=AwsClients(s3=s3, athena=athena)
        )
    assert [(r.item, r.reason) for r in summary.sources["focus"].rejected] == [
        (SECOND, "the manifest names a data file outside the export")
    ]
    assert summary.exit_code == 1
    assert "secret" not in summary.model_dump_json()


def test_only_the_data_files_the_manifest_lists_are_downloaded(tmp_path: Path) -> None:
    delivery = _delivery(tmp_path, SECOND, [0.0003])
    stale = f"{ROOT}data/{SECOND}/{EXPORT_NAME}-00002.snappy.parquet"  # left by an older run
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena) as stubber:
        expect_listing(s3_stub, DBT, {})
        expect_listing(s3_stub, OPENLINEAGE, {})
        expect_history(stubber, [])
        expect_prefixes(s3_stub, f"{ROOT}metadata/", [NOV])
        expect_listing(s3_stub, f"{ROOT}metadata/{NOV}/", delivery.metadata, bucket=EXPORT_BUCKET)
        _expect_manifests(s3_stub, delivery)
        expect_listing(
            s3_stub, f"{ROOT}data/{SECOND}/", delivery.data | {stale: b"x"}, bucket=EXPORT_BUCKET
        )
        for key, data in delivery.data.items():  # the stale file is never fetched
            expect_get(s3_stub, key, data, bucket=EXPORT_BUCKET)
        summary = run_collection(
            tmp_path / "data-finops.sqlite", SETTINGS, clients=AwsClients(s3=s3, athena=athena)
        )
    assert (summary.sources["focus"].stored, summary.exit_code) == (1, 0)


def _run_delivery_listed(
    tmp_path: Path, delivery: Delivery, *, sizes: dict[str, int] | None = None
) -> CollectionSummary:
    """A run that reaches `delivery`'s data listing (with these listed sizes) and fetches no
    data unless the sizes are its own."""
    s3, athena = s3_client(), athena_client()
    with stubbed(s3) as s3_stub, stubbed(athena) as stubber:
        expect_listing(s3_stub, DBT, {})
        expect_listing(s3_stub, OPENLINEAGE, {})
        expect_history(stubber, [])
        expect_prefixes(s3_stub, f"{ROOT}metadata/", [NOV])
        expect_listing(s3_stub, f"{ROOT}metadata/{NOV}/", delivery.metadata, bucket=EXPORT_BUCKET)
        _expect_manifests(s3_stub, delivery)
        expect_listing(
            s3_stub,
            f"{ROOT}data/{delivery.folder}/",
            delivery.data,
            bucket=EXPORT_BUCKET,
            sizes=sizes,
        )
        return run_collection(
            tmp_path / "data-finops.sqlite", SETTINGS, clients=AwsClients(s3=s3, athena=athena)
        )


@pytest.mark.parametrize(
    ("size", "reason"),
    [
        (MAX_DATA_FILE_BYTES + 1, "a file over its cap"),
        (MAX_DELIVERY_BYTES + 1, "the Delivery's data files exceed its cap together"),
    ],
)
def test_a_delivery_over_a_cap_is_rejected_before_any_data_is_downloaded(
    tmp_path: Path, size: int, reason: str
) -> None:
    delivery = _delivery(tmp_path, SECOND, [0.0003])
    (key,) = delivery.data
    summary = _run_delivery_listed(tmp_path, delivery, sizes={key: size})
    assert [(r.item, r.reason) for r in summary.sources["focus"].rejected] == [(SECOND, reason)]


def test_a_delivery_in_another_focus_release_is_rejected_by_the_reader(tmp_path: Path) -> None:
    delivery = _delivery(tmp_path, SECOND, [0.0003])
    (focus_key,) = (k for k in delivery.metadata if k.endswith(FOCUS_MANIFEST_NAME))
    focus = json.loads(delivery.metadata[focus_key])
    focus["Schema"]["FocusVersion"] = "1.3"
    delivery = delivery._replace(
        metadata=delivery.metadata | {focus_key: json.dumps(focus).encode()}
    )
    summary = _run_exports(tmp_path / "data-finops.sqlite", {NOV: [delivery]}, [delivery])
    ((item, reason),) = [(r.item, r.reason) for r in summary.sources["focus"].rejected]
    assert (item, reason) == (SECOND, "FOCUS 1.3, but 1.2 is pinned (ADR 0015)")
    assert summary.exit_code == 1


def test_a_delivery_still_being_written_is_left_for_a_later_run(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    whole = _delivery(tmp_path, SECOND, [0.0003])
    partial = whole._replace(
        metadata={k: v for k, v in whole.metadata.items() if not k.endswith(MANIFEST_NAME)}
    )
    waiting = _run_exports(db, {NOV: [partial]}, [])
    arrived = _run_exports(db, {NOV: [whole]}, [whole])
    assert (waiting.sources["focus"].stored, waiting.sources["focus"].rejected) == (0, [])
    assert waiting.exit_code == 0
    assert arrived.sources["focus"].stored == 1


def test_only_the_most_recent_billing_periods_are_looked_at(tmp_path: Path) -> None:
    oct_folder = (
        "billing_period=2026-10/2026-10-31T13:06:53.949Z-0771642f-2e82-3988-b27a-f8171b7deb3d"
    )
    sep = "billing_period=2026-09"
    october = _delivery(tmp_path, oct_folder, [0.0001])
    november = _delivery(tmp_path, SECOND, [0.0003])
    one = dataclasses.replace(SETTINGS, billing_lookback_periods=1)
    summary = _run_exports(
        tmp_path / "data-finops.sqlite",
        {sep: [], "billing_period=2026-10": [october], NOV: [november]},
        [november],
        settings=one,
    )  # September and October are never listed
    assert summary.sources["focus"].stored == 1
