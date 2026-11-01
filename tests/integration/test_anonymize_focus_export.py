"""FOCUS export anonymizer through its public interface (#18).

All identifiers below are invented or AWS documentation placeholders.
"""

import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "anonymize_focus_export", ROOT / "scripts" / "anonymize_focus_export.py"
)
assert _spec and _spec.loader
anonymizer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(anonymizer)

PAYER = "111122223333"
MEMBER = "444455556666"
DATA = Path("focus/example-export/data/BILLING_PERIOD=2026-11/part-00001.snappy.parquet")
MANIFEST = Path("focus/example-export/metadata/BILLING_PERIOD=2026-11/example-export-Manifest.json")
SCHEMA = pa.schema(
    [
        ("BilledCost", pa.float64()),
        ("ConsumedQuantity", pa.float64()),
        ("ChargePeriodStart", pa.timestamp("ms")),
        ("BillingAccountId", pa.string()),
        ("BillingAccountName", pa.string()),
        ("SubAccountId", pa.string()),
        ("InvoiceId", pa.string()),
        ("ResourceId", pa.string()),
        ("CommitmentDiscountId", pa.string()),
        ("ServiceName", pa.string()),
        ("x_Operation", pa.string()),
        ("ChargeDescription", pa.string()),
        ("x_CostCategories", pa.string()),
        ("Tags", pa.map_(pa.string(), pa.string())),
        ("SkuPriceDetails", pa.map_(pa.string(), pa.string())),
    ]
)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "BilledCost": 0.1,
        "ConsumedQuantity": 1e-07,
        "ChargePeriodStart": datetime(2026, 11, 1),
        "BillingAccountId": PAYER,
        "BillingAccountName": "Example Payer",
        "SubAccountId": MEMBER,
        "InvoiceId": "EXAMPLE-INVOICE-1",
        "ResourceId": f"arn:aws:athena:us-east-1:{MEMBER}:workgroup/example-analytics",
        "CommitmentDiscountId": f"arn:aws:savingsplans::{PAYER}:savingsplan/example-sp-1",
        "ServiceName": "Amazon Athena",
        "x_Operation": f"RunQuery-{MEMBER}",
        "ChargeDescription": "Scanned by example-analytics",
        "x_CostCategories": "team=example-team",
        "Tags": [("user:client", "example-client"), ("team", None)],
        "SkuPriceDetails": [("OwnerAccount", PAYER), ("unit", "TB")],
    }
    row.update(overrides)
    return row


def _rows() -> list[dict[str, object]]:
    return [
        _row(),
        _row(
            BilledCost=6.0,
            ResourceId=f"arn:aws:lambda:us-east-1:{MEMBER}:function:example-fn",
        ),
        _row(
            ResourceId=(
                f"arn:aws:elasticloadbalancing:us-east-1:{MEMBER}"
                ":loadbalancer/app/example-prod-lb/50dc6c495c0c9188"
            )
        ),
        _row(ResourceId="i-0abc123def"),
    ]


@pytest.fixture
def export_dir(tmp_path: Path) -> Path:
    source = tmp_path / "download"
    (source / DATA).parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(_rows(), schema=SCHEMA), source / DATA, compression="zstd")
    (source / MANIFEST).parent.mkdir(parents=True)
    manifest = {"account": PAYER, "dataFiles": [f"s3://example-bucket/{DATA.as_posix()}"]}
    (source / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return source


def _rows_in(path: Path) -> list[dict[str, object]]:
    return pq.read_table(path).to_pylist()  # type: ignore[no-any-return]


def _flat(value: object) -> str:
    return json.dumps(value, default=str)


def test_no_identifying_value_survives(export_dir, tmp_path):
    anonymizer.anonymize_export(export_dir, tmp_path / "out", secret="s3cret")
    output = _flat(_rows_in(tmp_path / "out" / DATA))

    for secret_value in (PAYER, MEMBER, "Example Payer", "EXAMPLE-INVOICE-1", "example-"):
        assert secret_value not in output


def test_unknown_and_free_text_columns_are_pseudonymized_but_allowlisted_ones_are_kept(
    export_dir, tmp_path
):
    anonymizer.anonymize_export(export_dir, tmp_path / "out", secret="s3cret")
    first = _rows_in(tmp_path / "out" / DATA)[0]

    assert str(first["ChargeDescription"]).startswith("anon-")
    assert str(first["x_CostCategories"]).startswith("anon-")
    assert first["ServiceName"] == "Amazon Athena"
    assert re.fullmatch(r"RunQuery-\d{12}", str(first["x_Operation"]))
    assert first["x_Operation"] != f"RunQuery-{MEMBER}"
    assert dict(first["SkuPriceDetails"])["unit"] == "TB"
    assert dict(first["SkuPriceDetails"])["OwnerAccount"] == first["BillingAccountId"]


def test_pseudonyms_are_consistent_and_depend_on_the_secret(export_dir, tmp_path):
    anonymizer.anonymize_export(export_dir, tmp_path / "a", secret="one")
    anonymizer.anonymize_export(export_dir, tmp_path / "b", secret="one")
    anonymizer.anonymize_export(export_dir, tmp_path / "c", secret="two")
    a, b, c = (_rows_in(tmp_path / d / DATA) for d in "abc")

    assert a == b
    assert a[0]["SubAccountId"] == a[1]["SubAccountId"]
    assert a[0]["SubAccountId"] != c[0]["SubAccountId"]


def test_arns_keep_their_shape_and_hide_everything_after_the_resource_type(export_dir, tmp_path):
    anonymizer.anonymize_export(export_dir, tmp_path / "out", secret="s3cret")
    rows = _rows_in(tmp_path / "out" / DATA)
    member = rows[0]["SubAccountId"]

    assert re.fullmatch(
        rf"arn:aws:athena:us-east-1:{member}:workgroup/anon-[0-9a-f]{{12}}",
        str(rows[0]["ResourceId"]),
    )
    assert re.fullmatch(
        rf"arn:aws:lambda:us-east-1:{member}:function:anon-[0-9a-f]{{12}}",
        str(rows[1]["ResourceId"]),
    )
    assert re.fullmatch(
        rf"arn:aws:elasticloadbalancing:us-east-1:{member}:loadbalancer/anon-[0-9a-f]{{12}}",
        str(rows[2]["ResourceId"]),
    )
    assert re.fullmatch(r"anon-[0-9a-f]{12}", str(rows[3]["ResourceId"]))
    assert re.fullmatch(
        rf"arn:aws:savingsplans::{rows[0]['BillingAccountId']}:savingsplan/anon-[0-9a-f]{{12}}",
        str(rows[0]["CommitmentDiscountId"]),
    )
    assert re.fullmatch(r"\d{12}", str(member))


def test_tag_keys_keep_their_prefix_and_null_values_stay_null(export_dir, tmp_path):
    anonymizer.anonymize_export(export_dir, tmp_path / "out", secret="s3cret")
    tags = dict(_rows_in(tmp_path / "out" / DATA)[0]["Tags"])

    keys = sorted(tags)
    assert any(k.startswith("user:anon-") for k in keys)
    assert None in tags.values()


def test_money_quantities_timestamps_schema_and_compression_are_kept(export_dir, tmp_path):
    anonymizer.anonymize_export(export_dir, tmp_path / "out", secret="s3cret")
    before, after = pq.read_table(export_dir / DATA), pq.read_table(tmp_path / "out" / DATA)

    assert after.schema == before.schema
    for column in ("BilledCost", "ConsumedQuantity", "ChargePeriodStart", "ServiceName"):
        assert after.column(column).equals(before.column(column))
    compression = pq.ParquetFile(tmp_path / "out" / DATA).metadata.row_group(0).column(0)
    assert compression.compression == "ZSTD"


def test_manifest_and_paths_have_account_ids_replaced_consistently(export_dir, tmp_path):
    folder = Path(f"focus/acct-{MEMBER}/report.parquet")
    (export_dir / folder).parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([_row()], schema=SCHEMA), export_dir / folder)
    anonymizer.anonymize_export(export_dir, tmp_path / "out", secret="s3cret")

    manifest = (tmp_path / "out" / MANIFEST).read_text(encoding="utf-8")
    written = [p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*")]
    assert PAYER not in manifest and DATA.as_posix() in manifest
    assert not any(MEMBER in p for p in written)
    assert any(re.fullmatch(r"focus/acct-\d{12}/report\.parquet", p) for p in written)


def test_other_files_are_skipped_and_reported(export_dir, tmp_path):
    (export_dir / "focus" / "notes.txt").write_text(f"payer {PAYER}", encoding="utf-8")

    summary = anonymizer.anonymize_export(export_dir, tmp_path / "out", secret="s3cret")

    assert summary[anonymizer.SKIPPED] == 1
    assert not (tmp_path / "out" / "focus" / "notes.txt").exists()


def test_summary_counts_replacements_per_column(export_dir, tmp_path):
    summary = anonymizer.anonymize_export(export_dir, tmp_path / "out", secret="s3cret")

    assert summary["BillingAccountId"] == 4
    assert summary["ResourceId"] == 4
    assert summary["ServiceName"] == 0
    assert summary["BilledCost"] == 0


def test_command_line_reads_the_secret_from_the_environment(
    export_dir, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("DATA_FINOPS_ANON_SECRET", "s3cret")
    assert anonymizer.main([str(export_dir), str(tmp_path / "out")]) == 0
    assert "BillingAccountId: 4" in capsys.readouterr().out

    monkeypatch.delenv("DATA_FINOPS_ANON_SECRET")
    with pytest.raises(ValueError, match="secret"):
        anonymizer.main([str(export_dir), str(tmp_path / "out2")])


def test_refuses_to_write_inside_the_repository_outside_local(export_dir):
    with pytest.raises(ValueError, match=r"\.local"):
        anonymizer.anonymize_export(export_dir, ROOT / "fixtures" / "leak", secret="s3cret")
    assert not (ROOT / "fixtures" / "leak").exists()


def test_uuids_survive_account_scrubbing() -> None:
    """A UUID's last group can be 12 digits; it is an identifier for correlation, not an account."""
    uuid = "6f1e0c3a-1111-4222-8333-444455556666"
    scrubbed = anonymizer.Pseudonyms("s").scrub_accounts(f"run {uuid} in {PAYER}")
    assert uuid in scrubbed and PAYER not in scrubbed


@pytest.mark.parametrize(
    "text",
    [f"{PAYER}-abcd-abcd-abcd-abcdefabcdef", f"x{PAYER}-1111-2222-3333-444455556666"],
)
def test_account_ids_running_into_hex_are_not_taken_for_uuids(text: str) -> None:
    scrubbed = anonymizer.Pseudonyms("s").scrub_accounts(text)
    assert PAYER not in scrubbed
    assert PAYER in anonymizer.Pseudonyms.account_ids(text)
