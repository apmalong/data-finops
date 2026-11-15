"""The demo dataset generator (#225; spec 09 §The demo dataset, seam 2): deterministic for a
seed, with the declared schema and size."""

import ast
import hashlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from data_finops.public_demo.dataset import PREFIX, ROWS, SCHEMAS, DatasetFile, generate

SMALL = {"customers": 300, "orders": 2_500, "events": 5_000}


def _digests(files: list[DatasetFile]) -> dict[str, str]:
    return {f.key: hashlib.sha256(f.data).hexdigest() for f in files}


def _table(files: list[DatasetFile], name: str) -> pa.Table:
    parts = [pq.read_table(pa.BufferReader(f.data)) for f in files if f.key.startswith(f"{name}/")]
    return pa.concat_tables(parts)


def test_the_same_seed_gives_the_same_files() -> None:
    first = list(generate(seed=7, rows=SMALL, part_rows=1_000))
    again = list(generate(seed=7, rows=SMALL, part_rows=1_000))

    assert _digests(first) == _digests(again)


def test_another_seed_gives_other_data() -> None:
    one = _digests(list(generate(seed=7, rows=SMALL, part_rows=1_000)))
    other = _digests(list(generate(seed=8, rows=SMALL, part_rows=1_000)))

    assert one.keys() == other.keys()
    assert all(one[key] != other[key] for key in one)


def test_each_table_has_its_declared_schema_and_rows() -> None:
    files = list(generate(seed=7, rows=SMALL, part_rows=1_000))

    assert {f.key.split("/")[0] for f in files} == set(SCHEMAS) == set(SMALL)
    for name, rows in SMALL.items():
        table = _table(files, name)
        assert table.schema.remove_metadata() == SCHEMAS[name]
        assert table.num_rows == rows


def test_a_table_is_split_into_parts_of_at_most_part_rows() -> None:
    files = list(generate(seed=7, rows=SMALL, part_rows=1_000))

    events = [f for f in files if f.key.startswith("events/")]
    assert [f.key for f in events] == [f"events/part-{n:05d}.parquet" for n in range(5)]
    assert all(pq.read_metadata(pa.BufferReader(f.data)).num_rows == 1_000 for f in events)


def test_orders_and_events_name_existing_customers() -> None:
    files = list(generate(seed=7, rows=SMALL, part_rows=1_000))

    customers = set(_table(files, "customers")["customer_id"].to_pylist())
    for name in ("orders", "events"):
        assert set(_table(files, name)["customer_id"].to_pylist()) <= customers


def test_the_full_dataset_is_declared_in_millions_of_rows() -> None:
    # A few hundred MB of Parquet (spec 09): the PR records the measured size of `ROWS`.
    assert ROWS == {"customers": 200_000, "orders": 2_000_000, "events": 10_000_000}


def test_rows_must_name_each_table_once() -> None:
    with pytest.raises(ValueError, match="events"):
        list(generate(seed=7, rows={"customers": 1, "orders": 1}))


GLUE_TYPE = {
    pa.int64(): "bigint",
    pa.string(): "string",
    pa.date32(): "date",
    pa.timestamp("ms"): "timestamp",
    pa.decimal128(12, 2): "decimal(12,2)",
}
"""How Athena reads each Parquet type of the dataset (Athena, "Data types in Athena")."""


def _stack_literals() -> dict[str, object]:
    """The literals of the stack's demo component, read from its source: the stack is a separate
    project, so the root never imports it."""
    source = Path(__file__).parents[2] / "infra" / "pulumi" / "aws" / "deployment" / "demo.py"
    found: dict[str, object] = {}
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if isinstance(target, ast.Name) and node.value is not None:  # type: ignore[union-attr]
            try:
                found[target.id] = ast.literal_eval(node.value)  # type: ignore[union-attr]
            except ValueError:
                continue
    return found


def test_the_glue_tables_of_the_stack_have_the_columns_of_the_dataset() -> None:
    literals = _stack_literals()

    assert literals["DATASET_PREFIX"] == PREFIX
    assert literals["TABLES"] == {
        name: tuple((field.name, GLUE_TYPE[field.type]) for field in schema)
        for name, schema in SCHEMAS.items()
    }
