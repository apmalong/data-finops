"""The database diagram is generated from the real schema and kept in step with it (#57)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "database_diagram", ROOT / "scripts" / "database_diagram.py"
)
assert _spec and _spec.loader
diagram = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diagram)

COMMITTED = ROOT / "docs" / "architecture" / "database.d2"


def test_the_committed_database_diagram_matches_the_schema() -> None:
    assert COMMITTED.read_text(encoding="utf-8") == diagram.database_d2(), (
        "the schema changed: run `uv run python scripts/database_diagram.py` and re-render"
    )


def test_the_diagram_shows_both_stores_tables_columns_and_references() -> None:
    source = diagram.database_d2()
    assert "ledger: Ledger (billing) {\n  export_snapshot: {\n" in source
    assert "workload: Workload store {\n  workload_lineage_event: {\n" in source
    assert '    "event_type": TEXT\n' in source
    assert '    "run_id": TEXT {constraint: [primary_key]}\n' in source
    assert '    "job_name": TEXT {constraint: [not_null]}\n' in source
    assert 'ledger.billing_line."snapshot_id" -> ledger.export_snapshot."snapshot_id"' in source
