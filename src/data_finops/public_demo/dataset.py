"""The demo dataset (#225; spec 09 §The demo dataset; ADR 0030): orders, customers and events as
Parquet, for the simulated workload's queries to scan in the demo workgroup.

The data is fictional. It is deterministic for a seed: each part of each table has its own
random generator, seeded from the seed, the table and the part, so the same seed gives the
same bytes and a part never depends on another. pyarrow writes the Parquet (ADR 0018,
amended for this module). The Glue tables that the stack declares
(`infra/pulumi/aws/deployment/demo.py`) have the same columns; a test compares them.
"""

import random
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from data_finops.providers.aws.calls import call

SEED = 20261107
"""The seed of the demo dataset, so that a rebuild gives the same data."""
ROWS: Mapping[str, int] = {"customers": 200_000, "orders": 2_000_000, "events": 10_000_000}
"""The rows of each table in the full dataset: a few hundred MB of Parquet."""
PART_ROWS = 1_000_000
"""The most rows in one Parquet file."""
PREFIX = "dataset/"
"""Where the dataset goes in the demo bucket. The Glue tables are under it."""
PARQUET_MEDIA_TYPE = "application/vnd.apache.parquet"

SCHEMAS: Mapping[str, pa.Schema] = {
    "customers": pa.schema(
        [
            ("customer_id", pa.int64()),
            ("tenant_id", pa.string()),
            ("signup_date", pa.date32()),
            ("country", pa.string()),
            ("segment", pa.string()),
        ]
    ),
    "orders": pa.schema(
        [
            ("order_id", pa.int64()),
            ("customer_id", pa.int64()),
            ("tenant_id", pa.string()),
            ("order_ts", pa.timestamp("ms")),
            ("status", pa.string()),
            ("amount", pa.decimal128(12, 2)),
        ]
    ),
    "events": pa.schema(
        [
            ("event_id", pa.int64()),
            ("customer_id", pa.int64()),
            ("tenant_id", pa.string()),
            ("event_ts", pa.timestamp("ms")),
            ("event_type", pa.string()),
            ("page", pa.string()),
            ("session_id", pa.string()),
        ]
    ),
}
"""The columns of each table, in order."""

TENANTS = ("northwind", "contoso", "fabrikam", "tailspin", "wingtip", "adventure-works")
COUNTRIES = ("CA", "US", "GB", "DE", "FR", "NL", "SE", "AU", "JP", "BR", "MX", "IN")
SEGMENTS = ("consumer", "small-business", "enterprise")
STATUSES = ("placed", "paid", "shipped", "delivered", "returned", "cancelled")
EVENT_TYPES = ("page_view", "search", "add_to_cart", "checkout", "login", "logout")
PAGES = ("/", "/search", "/product", "/cart", "/checkout", "/account", "/help")
START = datetime(2026, 1, 1)
SECONDS = 304 * 24 * 3600
"""The data covers 2026-01-01 to 2026-10-31."""


Columns = dict[str, list[Any]]
"""One part of a table: each column's values, in row order."""


@dataclass(frozen=True)
class DatasetFile:
    key: str
    """The file's path in the dataset: `<table>/part-<n>.parquet`."""
    data: bytes


def generate(
    seed: int = SEED, rows: Mapping[str, int] = ROWS, part_rows: int = PART_ROWS
) -> Iterator[DatasetFile]:
    """Each Parquet file of the dataset, table by table and part by part. One part is in
    memory at a time."""
    if set(rows) != set(SCHEMAS) or part_rows < 1:
        missing = ", ".join(sorted(set(SCHEMAS) ^ set(rows)))
        raise ValueError(
            f"rows must give each table once, and part_rows must be positive: {missing}"
        )
    customers = rows["customers"]
    for name, schema in SCHEMAS.items():
        for part, first in enumerate(range(0, rows[name], part_rows)):
            count = min(part_rows, rows[name] - first)
            # Fictional data that must repeat for a seed, not a secret.
            rng = random.Random(f"{seed}:{name}:{part}")  # nosec B311
            columns = _PART_MAKERS[name](rng, first, count, customers)
            yield DatasetFile(
                key=f"{name}/part-{part:05d}.parquet",
                data=_parquet(pa.table(columns, schema=schema)),
            )


def upload(s3: Any, bucket: str, files: Iterable[DatasetFile]) -> dict[str, int]:
    """Write each file under `PREFIX` in the bucket, one at a time. A failed write raises
    AwsSourceError with the AWS error code alone; files already written stay, and a new
    invocation writes the same bytes again."""
    count = size = 0
    for file in files:
        call(
            s3.put_object,
            Bucket=bucket,
            Key=f"{PREFIX}{file.key}",
            Body=file.data,
            ContentType=PARQUET_MEDIA_TYPE,
        )
        count, size = count + 1, size + len(file.data)
    return {"files": count, "bytes": size}


def _tenant(customer_id: int) -> str:
    """Each customer belongs to one tenant, so the tables join consistently."""
    return TENANTS[customer_id % len(TENANTS)]


def _customers(rng: random.Random, first: int, count: int, customers: int) -> Columns:
    ids = _ids(first, count)
    return {
        "customer_id": ids,
        "tenant_id": [_tenant(i) for i in ids],
        "signup_date": [date(2024, 1, 1) + timedelta(days=rng.randrange(1035)) for _ in ids],
        "country": [rng.choice(COUNTRIES) for _ in ids],
        "segment": rng.choices(SEGMENTS, weights=(70, 25, 5), k=count),
    }


def _orders(rng: random.Random, first: int, count: int, customers: int) -> Columns:
    buyers = [rng.randint(1, customers) for _ in range(count)]
    return {
        "order_id": _ids(first, count),
        "customer_id": buyers,
        "tenant_id": [_tenant(c) for c in buyers],
        "order_ts": _times(rng, count),
        "status": rng.choices(STATUSES, weights=(5, 10, 15, 60, 6, 4), k=count),
        "amount": [Decimal(rng.randrange(199, 50_000)).scaleb(-2) for _ in range(count)],
    }


def _events(rng: random.Random, first: int, count: int, customers: int) -> Columns:
    visitors = [rng.randint(1, customers) for _ in range(count)]
    return {
        "event_id": _ids(first, count),
        "customer_id": visitors,
        "tenant_id": [_tenant(c) for c in visitors],
        "event_ts": _times(rng, count),
        "event_type": rng.choices(EVENT_TYPES, weights=(50, 20, 12, 5, 8, 5), k=count),
        "page": [rng.choice(PAGES) for _ in range(count)],
        "session_id": [f"{rng.getrandbits(64):016x}" for _ in range(count)],
    }


_PART_MAKERS: Mapping[str, Callable[[random.Random, int, int, int], Columns]] = {
    "customers": _customers,
    "orders": _orders,
    "events": _events,
}


def _ids(first: int, count: int) -> list[int]:
    """The IDs of one part: they continue from the part before, and start at 1."""
    return list(range(first + 1, first + count + 1))


def _times(rng: random.Random, count: int) -> list[datetime]:
    return [START + timedelta(seconds=rng.randrange(SECONDS)) for _ in range(count)]


def _parquet(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="snappy")  # type: ignore[no-untyped-call]
    return bytes(sink.getvalue().to_pybytes())
