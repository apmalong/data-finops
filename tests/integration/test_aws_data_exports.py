"""An AWS Data Exports Delivery, read from its downloaded files as one Export Snapshot (#12).

The layout and both manifests copy the real FOCUS 1.2 Delivery of 2026-11 (field names
and folder naming checked against it; the account and bucket are placeholders).
ADR 0020: `published_at` is the execution's creation time, which AWS writes into the
Delivery's S3 folder name, read from the manifest's `dataFiles` URIs.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from delivery_files import (
    ACCOUNT,
    CREATED,
    EXECUTION,
    SCHEMA,
    row,
    write_delivery,
)

from data_finops.billing.aws_data_exports import DeliveryError, load_delivery
from data_finops.cli import main
from data_finops.ledger import Ledger


def _move_data_files(source: Path, target: Path) -> Path:
    target.mkdir()
    for part in source.glob("*.parquet"):
        part.rename(target / part.name)
    return target


def test_a_delivery_becomes_one_export_snapshot_published_when_aws_created_it(tmp_path):
    manifest = write_delivery(tmp_path, files={"a-00002.parquet": [4.0], "a-00001.parquet": [6.0]})

    export = load_delivery(manifest)

    snapshot = export.snapshot
    assert snapshot.snapshot_id == f"aws-focus-{ACCOUNT}-2026-11-{EXECUTION}"
    assert snapshot.source == "aws-data-exports:FOCUS_1_2_AWS"
    assert snapshot.focus_version == "1.2"
    assert snapshot.billing_account_id == ACCOUNT
    assert snapshot.billing_period_start == datetime(2026, 11, 1, tzinfo=UTC)
    assert snapshot.billing_period_end == datetime(2026, 12, 1, tzinfo=UTC)
    assert snapshot.published_at == datetime(2026, 11, 5, 18, 43, 54, 994000, tzinfo=UTC)
    assert [f.path for f in snapshot.files] == ["a-00001.parquet", "a-00002.parquet"]
    assert [str(line.BilledCost) for line in export.lines] == ["6.0", "4.0"]


def test_only_the_files_the_manifest_lists_are_read(tmp_path):
    manifest = write_delivery(
        tmp_path,
        files={"a-00001.parquet": [6.0], "left-over.parquet": [99.0]},
        listed=["a-00001.parquet"],
    )

    export = load_delivery(manifest)

    assert [str(line.BilledCost) for line in export.lines] == ["6.0"]


def test_a_listed_file_that_was_not_downloaded_is_an_error(tmp_path):
    manifest = write_delivery(
        tmp_path, listed=["example-focus-1-2-00001.snappy.parquet", "missing.parquet"]
    )

    with pytest.raises(DeliveryError, match="missing.parquet"):
        load_delivery(manifest)


def test_data_files_can_lie_in_another_folder(tmp_path):
    manifest = write_delivery(tmp_path / "metadata")
    data = _move_data_files(tmp_path / "metadata", tmp_path / "data")

    export = load_delivery(manifest, data)

    assert [str(line.BilledCost) for line in export.lines] == ["0.0003"]


def test_a_manifest_listing_no_data_files_is_an_error(tmp_path):
    manifest = write_delivery(tmp_path, listed=[])

    with pytest.raises(DeliveryError, match="no data files"):
        load_delivery(manifest)


def test_a_folder_naming_another_execution_is_an_error(tmp_path):
    manifest = write_delivery(tmp_path, execution="0771642f-2e82-3988-b27a-f8171b7deb3d")

    with pytest.raises(DeliveryError, match="execution"):
        load_delivery(manifest)


@pytest.mark.parametrize(
    "folder",
    [
        f"billing_period=2026-11/{EXECUTION}",
        f"billing_period=2026-11/not-a-time-{EXECUTION}",
        f"period=2026-11/{CREATED}-{EXECUTION}",
    ],
    ids=["no creation time", "not a time", "no billing period"],
)
def test_a_folder_without_aws_naming_is_an_error(tmp_path, folder):
    manifest = write_delivery(tmp_path, folder=folder)

    with pytest.raises(DeliveryError):
        load_delivery(manifest)


def test_a_focus_version_other_than_the_pinned_release_is_an_error(tmp_path):
    manifest = write_delivery(tmp_path, focus_version="1.3")

    with pytest.raises(DeliveryError, match="1.3"):
        load_delivery(manifest)


def test_lines_from_another_billing_period_than_the_folder_names_are_an_error(tmp_path):
    manifest = write_delivery(tmp_path, folder=f"billing_period=2026-10/{CREATED}-{EXECUTION}")

    with pytest.raises(ValueError, match="Billing Period"):
        load_delivery(manifest)


def test_the_command_line_ingests_a_delivery_by_its_manifest_and_replays_it(tmp_path, capsys):
    manifest = write_delivery(tmp_path / "metadata")
    data = _move_data_files(tmp_path / "metadata", tmp_path / "data")
    db = tmp_path / "data-finops.sqlite"
    command = ["ingest", "focus", str(manifest), "--data-dir", str(data)]

    assert main([*command, "--db", str(db)]) == 0
    assert main([*command, "--db", str(db)]) == 0

    out = capsys.readouterr().out.splitlines()
    snapshot_id = f"aws-focus-{ACCOUNT}-2026-11-{EXECUTION}"
    assert out == [f"focus: new {snapshot_id}", f"focus: replay {snapshot_id}"]
    with Ledger(db) as ledger:
        export = load_delivery(manifest, data)
        assert ledger.reconcile(export).balanced


def test_a_december_delivery_ends_its_billing_period_on_new_year(tmp_path):
    folder = f"billing_period=2026-12/{CREATED}-{EXECUTION}"
    rows = {"a-00001.parquet": [1.0]}
    manifest = write_delivery(tmp_path, folder=folder, files=rows)
    december = pa.Table.from_pylist(
        [
            row(1.0)
            | {
                "BillingPeriodStart": datetime(2026, 12, 1, tzinfo=UTC),
                "BillingPeriodEnd": datetime(2027, 1, 1, tzinfo=UTC),
            }
        ],
        schema=SCHEMA,
    )
    pq.write_table(december, tmp_path / "a-00001.parquet")

    snapshot = load_delivery(manifest).snapshot

    assert snapshot.billing_period_end == datetime(2027, 1, 1, tzinfo=UTC)


def test_a_manifest_listing_files_of_two_deliveries_is_an_error(tmp_path):
    manifest = write_delivery(tmp_path, files={"a-00001.parquet": [1.0], "a-00002.parquet": [2.0]})
    content = json.loads(manifest.read_text(encoding="utf-8"))
    content["dataFiles"][1] = content["dataFiles"][1].replace(CREATED, "2026-11-05T13:06:53.949Z")
    manifest.write_text(json.dumps(content), encoding="utf-8")

    with pytest.raises(DeliveryError, match="2 deliveries"):
        load_delivery(manifest)


def test_the_command_line_names_what_is_wrong_with_a_delivery_but_not_its_bucket(tmp_path, capsys):
    manifest = write_delivery(
        tmp_path, listed=["example-focus-1-2-00001.snappy.parquet", "missing.parquet"]
    )

    code = main(["ingest", "focus", str(manifest), "--db", str(tmp_path / "db.sqlite")])

    err = capsys.readouterr().err
    assert code == 2
    assert "missing.parquet is listed in the manifest but not downloaded" in err
    assert "example-bucket" not in err
