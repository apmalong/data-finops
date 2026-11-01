"""FOCUS Parquet export reader through its public interface (seam agreed in #6)."""

from datetime import datetime
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
# timestamp_milliseconds (AWS FOCUS 1.2 column dictionary).
SCHEMA = pa.schema(
    [
        ("BilledCost", pa.float64()),
        ("EffectiveCost", pa.float64()),
        ("ListCost", pa.float64()),
        ("ContractedCost", pa.float64()),
        ("ConsumedQuantity", pa.float64()),
        ("BillingAccountId", pa.string()),
        ("BillingCurrency", pa.string()),
        ("BillingPeriodStart", pa.timestamp("ms")),
        ("BillingPeriodEnd", pa.timestamp("ms")),
        ("ChargePeriodStart", pa.timestamp("ms")),
        ("ChargePeriodEnd", pa.timestamp("ms")),
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
        "BillingPeriodStart": datetime(2026, 10, 1),
        "BillingPeriodEnd": datetime(2026, 11, 1),
        "ChargePeriodStart": datetime(2026, 10, 1),
        "ChargePeriodEnd": datetime(2026, 10, 2),
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
    assert raw["BilledCost"] == 0.1
    assert raw["x_Discounts.EDP"] == -0.02


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
