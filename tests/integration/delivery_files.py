"""AWS Data Exports Deliveries written for tests, shaped as the real FOCUS 1.2 Delivery of
2026-11 (field names and folder naming checked against it; the account and bucket are
placeholders). Shared by the reader tests (#12) and the collector tests (#182)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ACCOUNT = "123456789012"
EXPORT = "focus/example-focus-1-2"
EXECUTION = "980aa446-5e52-3927-9bca-c5774fc47fe8"
CREATED = "2026-11-05T18:43:54.994Z"
FOLDER = f"billing_period=2026-11/{CREATED}-{EXECUTION}"
SCHEMA = pa.schema(
    [
        ("BilledCost", pa.float64()),
        ("EffectiveCost", pa.float64()),
        ("ListCost", pa.float64()),
        ("ContractedCost", pa.float64()),
        ("BillingAccountId", pa.string()),
        ("BillingCurrency", pa.string()),
        ("BillingPeriodStart", pa.timestamp("ms", tz="UTC")),
        ("BillingPeriodEnd", pa.timestamp("ms", tz="UTC")),
        ("ChargePeriodStart", pa.timestamp("ms", tz="UTC")),
        ("ChargePeriodEnd", pa.timestamp("ms", tz="UTC")),
        ("ChargeCategory", pa.string()),
        ("ProviderName", pa.string()),
        ("ServiceName", pa.string()),
    ]
)


def row(cost: float) -> dict[str, object]:
    return {
        "BilledCost": cost,
        "EffectiveCost": cost,
        "ListCost": cost,
        "ContractedCost": cost,
        "BillingAccountId": ACCOUNT,
        "BillingCurrency": "USD",
        "BillingPeriodStart": datetime(2026, 11, 1, tzinfo=UTC),
        "BillingPeriodEnd": datetime(2026, 12, 1, tzinfo=UTC),
        "ChargePeriodStart": datetime(2026, 11, 1, 21, tzinfo=UTC),
        "ChargePeriodEnd": datetime(2026, 11, 1, 22, tzinfo=UTC),
        "ChargeCategory": "Usage",
        "ProviderName": "AWS",
        "ServiceName": "Amazon Athena",
    }


def write_delivery(
    root: Path,
    *,
    folder: str = FOLDER,
    execution: str = EXECUTION,
    files: dict[str, list[float]] | None = None,
    listed: list[str] | None = None,
    focus_version: str = "1.2",
    bucket: str = "example-bucket",
) -> Path:
    """Write one delivery's manifests and data files flat into `root` (S3's folder names
    hold colons, which Windows cannot store); return the manifest."""
    files = files if files is not None else {"example-focus-1-2-00001.snappy.parquet": [0.0003]}
    root.mkdir(parents=True, exist_ok=True)
    for name, costs in files.items():
        pq.write_table(pa.Table.from_pylist([row(c) for c in costs], schema=SCHEMA), root / name)
    names = listed if listed is not None else list(files)
    manifest = root / "example-focus-1-2-Manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "executionId": execution,
                "exportArn": f"arn:aws:bcm-data-exports:us-east-1:{ACCOUNT}:export/example",
                "columns": [{"name": f.name, "type": str(f.type)} for f in SCHEMA],
                "dataFiles": [f"s3://{bucket}/{EXPORT}/data/{folder}/{n}" for n in names],
                "additionalOutputFiles": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "example-focus-1-2-Manifest-FOCUS.json").write_text(
        json.dumps(
            {
                "DataGenerator": "Amazon Web Services, Inc.",
                "DataGeneratorVersion": "1.0.0",
                "Schema": {
                    "SchemaId": "1.0.0",
                    "CreationDate": "2025-11-19T00:00:00Z",
                    "FocusVersion": focus_version,
                    "ColumnDefinition": [],
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest
