"""Read FOCUS Export Snapshots: the offline JSON fixture format and Parquet exports.

pyarrow is used only here (ADR 0018).
"""

import math
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from data_finops.contracts.billing import ExportSnapshot, FocusBillingLine, FocusExport


def load_export(path: Path) -> FocusExport:
    return FocusExport.model_validate_json(path.read_text(encoding="utf-8"))


def load_parquet_export(files: Iterable[Path], snapshot: ExportSnapshot) -> FocusExport:
    """Read a FOCUS Parquet export (one or more data files) as one Export Snapshot.

    `snapshot` carries the publication metadata, including `published_at`, which the
    caller takes from the provider's export manifest (ADR 0020). Files are read in
    file-name order, so line positions never depend on how the files were listed.
    Provider doubles become their shortest round-trip decimal (OQ-21); timestamps
    without a zone are UTC, as FOCUS requires.
    """
    lines: list[FocusBillingLine] = []
    for path in sorted(files, key=lambda p: (p.name, str(p))):
        table = pq.read_table(path)  # type: ignore[no-untyped-call]
        map_columns = {f.name for f in table.schema if pa.types.is_map(f.type)}
        for row in table.to_pylist():
            lines.append(_focus_line(row, map_columns))
    return FocusExport(snapshot=snapshot, lines=lines)


def _focus_line(row: dict[str, Any], map_columns: set[str]) -> FocusBillingLine:
    raw: dict[str, float] = {}
    values: dict[str, Any] = {}
    for column, value in row.items():
        if column in map_columns and value is not None:
            values[column] = {key: _convert(f"{column}.{key}", item, raw) for key, item in value}
        else:
            values[column] = _convert(column, value, raw)
    if raw:
        values["data_finops_raw_values"] = raw
    return FocusBillingLine.model_validate(values)


def _convert(column: str, value: Any, raw: dict[str, float]) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{column}: non-finite amount {value!r}")
        raw[column] = value
        # repr() is the shortest string that round-trips the double; format "f" avoids
        # exponent notation, which the decimal-string contract does not accept.
        return format(Decimal(repr(value)), "f")
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
