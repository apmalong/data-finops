"""Read FOCUS Export Snapshots: the offline JSON fixture format and Parquet exports.

pyarrow is used only here (ADR 0018).
"""

import hashlib
import math
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from data_finops.contracts.billing import (
    ExportSnapshot,
    FocusBillingLine,
    FocusExport,
    SnapshotFile,
)
from data_finops.contracts.types import format_decimal


def load_export(path: Path) -> FocusExport:
    return FocusExport.model_validate_json(path.read_text(encoding="utf-8"))


def load_parquet_export(files: Iterable[Path], snapshot: ExportSnapshot) -> FocusExport:
    """Read a FOCUS Parquet export (one or more data files) as one Export Snapshot.

    `snapshot` carries the publication metadata, including `published_at`, which the
    caller must take from the provider's export manifest (ADR 0020); manifest parsing
    is not done here because AWS does not document the manifest's fields (#12). The
    reader records a sha256 for every file it reads, replacing `snapshot.files`.
    Files are read in path order (file name first), so line positions never depend on
    how the files were listed. Provider doubles, at any nesting depth, become their
    shortest round-trip decimal and keep their raw value (OQ-21). Timestamps are UTC, as
    FOCUS requires: a zoned one (AWS writes `timestamp[ms, tz=UTC]`) is read as its UTC
    instant without a time-zone database, which Windows lacks; one without a zone is UTC.
    """
    lines: list[FocusBillingLine] = []
    hashes: list[SnapshotFile] = []
    for path in sorted(files, key=lambda p: (p.name, str(p))):
        content = path.read_bytes()
        hashes.append(SnapshotFile(path=path.name, sha256=hashlib.sha256(content).hexdigest()))
        table = pq.read_table(pa.BufferReader(content))  # type: ignore[no-untyped-call]
        table = table.cast(zoneless_utc_schema(table.schema))
        map_columns = {f.name for f in table.schema if pa.types.is_map(f.type)}
        lines += [_focus_line(row, map_columns) for row in table.to_pylist()]
    return FocusExport(snapshot=snapshot.model_copy(update={"files": hashes}), lines=lines)


def billing_accounts(files: Iterable[Path]) -> set[str]:
    """The billing accounts named in the FOCUS Parquet `files`, read without loading lines,
    so a caller can build the Export Snapshot `load_parquet_export` needs."""
    return {
        str(account)
        for path in files
        for account in pq.read_table(path, columns=["BillingAccountId"]).column(0).to_pylist()  # type: ignore[no-untyped-call]
        if account is not None
    }


def zoneless_utc_schema(schema: pa.Schema) -> pa.Schema:
    """`schema` with every zoned timestamp as a zoneless one holding its UTC wall clock.

    Arrow stores a zoned timestamp as its UTC instant, so casting a table to this schema
    keeps every value, and converting it to Python never resolves a zone's name (which
    needs a time-zone database; Windows has none). Timestamps inside structs, maps, lists,
    large lists and fixed-size lists are rewritten too; other nested types are left alone.
    """
    return pa.schema([_zoneless_field(field) for field in schema], metadata=schema.metadata)


def _zoneless_field(field: pa.Field) -> pa.Field:
    return field.with_type(_zoneless(field.type))


def _zoneless(type_: pa.DataType) -> pa.DataType:
    if pa.types.is_timestamp(type_):
        return pa.timestamp(type_.unit)
    if pa.types.is_struct(type_):
        return pa.struct([_zoneless_field(type_.field(i)) for i in range(type_.num_fields)])
    if pa.types.is_map(type_):
        return pa.map_(_zoneless_field(type_.key_field), _zoneless_field(type_.item_field))
    if pa.types.is_fixed_size_list(type_):
        return pa.list_(_zoneless_field(type_.value_field), type_.list_size)
    if pa.types.is_large_list(type_):
        return pa.large_list(_zoneless_field(type_.value_field))
    if pa.types.is_list(type_):
        return pa.list_(_zoneless_field(type_.value_field))
    return type_


def _focus_line(row: dict[str, Any], map_columns: set[str]) -> FocusBillingLine:
    values: dict[str, Any] = {}
    raw: dict[str, str] = {}
    for column, value in row.items():
        if column in map_columns and value is not None:
            value = dict(value)  # pyarrow yields maps as (key, value) pairs
        values[column], column_raw = _from_parquet(column, value)
        raw |= column_raw
    if raw:
        values["data_finops_raw_values"] = raw
    return FocusBillingLine.model_validate(values)


def _from_parquet(path: str, value: Any) -> tuple[Any, dict[str, str]]:
    """Convert one value for the contract, returning it with the raw doubles it held."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path}: non-finite amount {value!r}")
        # repr() is the shortest string that round-trips the double (OQ-21).
        return format_decimal(Decimal(repr(value))), {path: repr(value)}
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC), {}
    raw: dict[str, str] = {}
    if isinstance(value, dict):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            converted[key], item_raw = _from_parquet(f"{path}.{key}", item)
            raw |= item_raw
        return converted, raw
    if isinstance(value, list):
        items: list[Any] = []
        for index, item in enumerate(value):
            converted_item, item_raw = _from_parquet(f"{path}[{index}]", item)
            items.append(converted_item)
            raw |= item_raw
        return items, raw
    return value, {}
