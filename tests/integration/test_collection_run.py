"""The Collection Run (spec 08 seam 2; GLOSSARY): sources fetched through stubbed AWS clients,
ingested into a temporary database, asserted only through `reporting` and the summary.
"""

import json
from pathlib import Path

from aws_stubs import (
    BUCKET,
    REGION,
    expect_denied,
    expect_get,
    expect_listing,
    s3_client,
    stubbed,
)
from fixture_database import ACCOUNT, EXPORT, FIXTURES

from data_finops.collection import CollectionSettings, run_collection
from data_finops.collection.run import MAX_INBOX_FILE_BYTES
from data_finops.ledger import Ledger
from data_finops.reporting import Reporting
from data_finops.workload.store import WorkloadStore

EVENTS = (FIXTURES / "openlineage" / "events.json").read_bytes()
DBT = "inbox/dbt/"
OPENLINEAGE = "inbox/openlineage/"
SETTINGS = CollectionSettings(bucket=BUCKET, region=REGION)


def test_an_inbox_event_file_is_ingested_once_and_shows_in_lineage(tmp_path: Path) -> None:
    db = tmp_path / "data-finops.sqlite"
    s3 = s3_client()
    key = f"{OPENLINEAGE}dbt-20261101.json"
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: EVENTS})
        expect_get(stubber, key, EVENTS)
        first = run_collection(db, SETTINGS, s3=s3)
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: EVENTS})  # listed again, never fetched
        second = run_collection(db, SETTINGS, s3=s3)

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
        summary = run_collection(db, SETTINGS, s3=s3)

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
        summary = run_collection(tmp_path / "data-finops.sqlite", SETTINGS, s3=s3)
    assert [(r.item, r.reason) for r in summary.sources["openlineage"].rejected] == [
        (key, "too large")
    ]
    assert summary.exit_code == 1
    with stubbed(s3) as stubber:  # rejected once, never again
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: b""}, sizes={key: MAX_INBOX_FILE_BYTES + 1})
        again = run_collection(tmp_path / "data-finops.sqlite", SETTINGS, s3=s3)
    assert (again.sources["openlineage"].rejected, again.exit_code) == ([], 0)


def test_an_unreadable_inbox_fails_the_run_after_calculating_without_aws_text(
    tmp_path: Path,
) -> None:
    db = _with_bill(tmp_path)
    s3 = s3_client()
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_denied(stubber, "list_objects_v2")
        summary = run_collection(db, SETTINGS, s3=s3)
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
        run_collection(db, SETTINGS, s3=s3)
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {})
        expect_listing(stubber, OPENLINEAGE, {key: second}, etags={key: '"v2"'})
        expect_get(stubber, key, second, version='"v2"')
        summary = run_collection(db, SETTINGS, s3=s3)
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
        summary = run_collection(db, SETTINGS, s3=s3)

    assert list(summary.sources) == ["dbt", "openlineage"]
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
        waiting = run_collection(db, SETTINGS, s3=s3)
    with stubbed(s3) as stubber:
        expect_listing(stubber, DBT, {manifest: MANIFEST, run_results: RUN_RESULTS})
        expect_get(stubber, manifest, MANIFEST)
        expect_get(stubber, run_results, RUN_RESULTS)
        expect_listing(stubber, OPENLINEAGE, {})
        arrived = run_collection(db, SETTINGS, s3=s3)

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
        summary = run_collection(db, SETTINGS, s3=s3)

    dbt = summary.sources["dbt"]
    assert dbt.stored == 0
    assert [r.item for r in dbt.rejected] == [huge, bad]
    assert dbt.rejected[0].reason == "too large"
    assert summary.exit_code == 1
    assert "secret" not in summary.model_dump_json()
