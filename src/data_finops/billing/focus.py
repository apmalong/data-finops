"""Read a FOCUS Export Snapshot from the offline JSON fixture format."""

from pathlib import Path

from data_finops.contracts.billing import FocusExport


def load_export(path: Path) -> FocusExport:
    return FocusExport.model_validate_json(path.read_text(encoding="utf-8"))
