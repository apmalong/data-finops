"""FOCUS Parquet export reader through its public interface (seam agreed in #6)."""

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data_finops.billing.focus import load_export, load_parquet_export
from data_finops.ledger import Ledger

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "focus" / "export-2026-10.json"
SNAPSHOT = load_export(FIXTURE).snapshot

# AWS publishes FOCUS cost and quantity columns as double and periods as
# timestamp_milliseconds (AWS FOCUS 1.2 column dictionary), zoned UTC as the real 2026-10 and
# 2026-11 exports are.
SCHEMA = pa.schema(
    [
        ("BilledCost", pa.float64()),
        ("EffectiveCost", pa.float64()),
        ("ListCost", pa.float64()),
        ("ContractedCost", pa.float64()),
        ("ConsumedQuantity", pa.float64()),
        ("BillingAccountId", pa.string()),
        ("BillingCurrency", pa.string()),
        ("BillingPeriodStart", pa.timestamp("ms", tz="UTC")),
        ("BillingPeriodEnd", pa.timestamp("ms", tz="UTC")),
        ("ChargePeriodStart", pa.timestamp("ms", tz="UTC")),
        ("ChargePeriodEnd", pa.timestamp("ms", tz="UTC")),
        ("ChargeCategory", pa.string()),
        ("ProviderName", pa.string()),
        ("ServiceName", pa.string()),
        ("Tags", pa.map_(pa.string(), pa.string())),
        ("x_ServiceCode", pa.string()),
        ("x_Discounts", pa.map_(pa.string(), pa.float64())),
        ("SkuMeter", pa.string()),
    ]
)


def _row(cost: float, service: str = "Amazon Athena", **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "BilledCost": cost,
        "EffectiveCost": cost,
        "ListCost": cost,
        "ContractedCost": cost,
        "ConsumedQuantity": 0.001,
        "BillingAccountId": "123456789012",
        "BillingCurrency": "USD",
        "BillingPeriodStart": datetime(2026, 10, 1, tzinfo=UTC),
        "BillingPeriodEnd": datetime(2026, 11, 1, tzinfo=UTC),
        "ChargePeriodStart": datetime(2026, 10, 1, tzinfo=UTC),
        "ChargePeriodEnd": datetime(2026, 10, 2, tzinfo=UTC),
        "ChargeCategory": "Usage",
        "ProviderName": "AWS",
        "ServiceName": service,
        "Tags": [("team", "data")],
        "x_ServiceCode": "AmazonAthena",
        "x_Discounts": [("EDP", -0.02)],
        "SkuMeter": "DataScanned",
    }
    row.update(overrides)
    return row


def _write(path: Path, rows: list[dict[str, object]]) -> Path:
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), path)
    return path


def test_doubles_become_their_shortest_decimal_and_keep_the_raw_value(tmp_path):
    part = _write(tmp_path / "part-00001.parquet", [_row(0.1), _row(6.0), _row(1e-07)])

    export = load_parquet_export([part], SNAPSHOT)

    assert [line.model_dump(mode="json")["BilledCost"] for line in export.lines] == [
        "0.1",
        "6.0",
        "0.0000001",
    ]
    assert str(export.lines[0].ConsumedQuantity) == "0.001"
    raw = export.lines[0].model_dump(mode="json")["data_finops_raw_values"]
    assert raw["BilledCost"] == "0.1"
    assert float(raw["BilledCost"]) == 0.1
    assert raw["x_Discounts.EDP"] == "-0.02"


def test_line_order_does_not_depend_on_file_listing_order(tmp_path):
    first = _write(tmp_path / "part-00001.parquet", [_row(1.5), _row(2.5)])
    second = _write(tmp_path / "part-00002.parquet", [_row(3.5)])

    listed = load_parquet_export([second, first], SNAPSHOT)
    sorted_ = load_parquet_export([first, second], SNAPSHOT)

    assert [str(line.BilledCost) for line in listed.lines] == ["1.5", "2.5", "3.5"]
    assert listed.model_dump(mode="json") == sorted_.model_dump(mode="json")
    assert listed.line_ref(2) == "aws-focus-123456789012-2026-10-v1#2"


def test_unknown_and_extension_columns_are_preserved(tmp_path):
    part = _write(tmp_path / "part-00001.parquet", [_row(6.0)])

    line = load_parquet_export([part], SNAPSHOT).lines[0].model_dump(mode="json")

    assert line["x_ServiceCode"] == "AmazonAthena"
    assert line["SkuMeter"] == "DataScanned"
    assert line["x_Discounts"] == {"EDP": "-0.02"}
    assert line["Tags"] == {"team": "data"}
    assert line["BillingPeriodStart"] == "2026-10-01T00:00:00Z"


def test_parquet_snapshot_imports_and_reconciles_with_zero_delta(tmp_path):
    part = _write(tmp_path / "part-00001.parquet", [_row(6.0), _row(4.0, "Amazon S3")])
    export = load_parquet_export([part], SNAPSHOT)

    with Ledger(tmp_path / "ledger.db") as ledger:
        ledger.import_snapshot(export)
        report = ledger.reconcile(export)

    assert report.balanced
    assert str(report.totals["USD"].ledger_amount) == "10.0"
    assert report.lines[1].ledger is not None
    assert report.lines[1].ledger.amount == Decimal("4.0")


def test_non_finite_amounts_are_rejected(tmp_path):
    part = _write(tmp_path / "part-00001.parquet", [_row(float("nan"))])

    with pytest.raises(ValueError, match="BilledCost"):
        load_parquet_export([part], SNAPSHOT)


def test_the_snapshot_records_a_sha256_for_every_file_read(tmp_path):
    first = _write(tmp_path / "part-00001.parquet", [_row(1.5)])
    second = _write(tmp_path / "part-00002.parquet", [_row(2.5)])

    files = load_parquet_export([second, first], SNAPSHOT).snapshot.files

    assert [(f.path, f.sha256) for f in files] == [
        ("part-00001.parquet", hashlib.sha256(first.read_bytes()).hexdigest()),
        ("part-00002.parquet", hashlib.sha256(second.read_bytes()).hexdigest()),
    ]


def test_doubles_nested_in_struct_and_list_columns_are_converted_with_raw_values(tmp_path):
    nested = pa.schema(
        [
            *SCHEMA,
            ("x_Detail", pa.struct([("rate", pa.float64()), ("note", pa.string())])),
            ("x_Tiers", pa.list_(pa.float64())),
        ]
    )
    row = {**_row(6.0), "x_Detail": {"rate": 0.3, "note": "tiered"}, "x_Tiers": [0.25, 1e-07]}
    pq.write_table(pa.Table.from_pylist([row], schema=nested), tmp_path / "part-00001.parquet")

    line = load_parquet_export([tmp_path / "part-00001.parquet"], SNAPSHOT).lines[0]
    dumped = line.model_dump(mode="json")

    assert dumped["x_Detail"] == {"rate": "0.3", "note": "tiered"}
    assert dumped["x_Tiers"] == ["0.25", "0.0000001"]
    assert dumped["data_finops_raw_values"]["x_Detail.rate"] == "0.3"
    assert dumped["data_finops_raw_values"]["x_Tiers[1]"] == "1e-07"


def test_files_with_the_same_name_in_different_folders_order_by_full_path(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    in_a = _write(tmp_path / "a" / "part-00001.parquet", [_row(1.5)])
    in_b = _write(tmp_path / "b" / "part-00001.parquet", [_row(2.5)])

    export = load_parquet_export([in_b, in_a], SNAPSHOT)

    assert [line.model_dump(mode="json")["BilledCost"] for line in export.lines] == ["1.5", "2.5"]


def test_timestamps_without_a_zone_are_read_as_utc(tmp_path):
    rows = [_row(6.0, BillingPeriodStart=datetime(2026, 10, 1))]
    naive = SCHEMA.set(
        SCHEMA.get_field_index("BillingPeriodStart"),
        pa.field("BillingPeriodStart", pa.timestamp("ms")),
    )
    part = tmp_path / "part-00001.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=naive), part)

    line = load_parquet_export([part], SNAPSHOT).lines[0]

    assert line.BillingPeriodStart == datetime(2026, 10, 1, tzinfo=UTC)


def test_zoned_timestamps_nested_in_structs_lists_and_maps_are_read_as_utc(tmp_path):
    zoned = pa.timestamp("ms", tz="UTC")
    schema = (
        SCHEMA.append(pa.field("x_Window", pa.struct([("start", zoned)])))
        .append(pa.field("x_Times", pa.list_(zoned)))
        .append(pa.field("x_Pair", pa.list_(zoned, 2)))
        .append(pa.field("x_ByName", pa.map_(pa.string(), zoned)))
    )
    six = datetime(2026, 10, 1, 6, tzinfo=UTC)
    rows = [
        _row(6.0, x_Window={"start": six}, x_Times=[six], x_Pair=[six, six], x_ByName=[("a", six)])
    ]
    part = tmp_path / "part-00001.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), part)

    line = load_parquet_export([part], SNAPSHOT).lines[0].model_dump(mode="json")

    assert line["x_Window"] == {"start": "2026-10-01T06:00:00Z"}
    assert line["x_Times"] == ["2026-10-01T06:00:00Z"]
    assert line["x_Pair"] == ["2026-10-01T06:00:00Z"] * 2
    assert line["x_ByName"] == {"a": "2026-10-01T06:00:00Z"}


def test_a_timestamp_in_another_zone_is_read_as_its_utc_instant(tmp_path):
    rows = [_row(6.0)]
    schema = SCHEMA.set(
        SCHEMA.get_field_index("ChargePeriodStart"),
        pa.field("ChargePeriodStart", pa.timestamp("ms", tz="+05:00")),
    )
    part = tmp_path / "part-00001.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), part)

    line = load_parquet_export([part], SNAPSHOT).lines[0]

    assert line.ChargePeriodStart == datetime(2026, 10, 1, tzinfo=UTC)
