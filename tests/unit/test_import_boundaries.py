"""Enforce docs/implementation/architecture-boundaries.md with a static import scan."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "data_finops"

FORBIDDEN_IMPORTS = {
    "contracts": ("data_finops.",),
    "attribution": (
        "data_finops.providers",
        "data_finops.api",
        "boto3",
        "botocore",
        "requests",
        "httpx",
        "urllib",
        "socket",
    ),
    "billing": ("data_finops.identity", "data_finops.lineage", "data_finops.dbt"),
    "identity": ("data_finops.billing", "data_finops.ledger", "data_finops.attribution"),
    "lineage": ("data_finops.billing", "data_finops.ledger", "data_finops.attribution"),
    "dbt": ("data_finops.billing", "data_finops.ledger", "data_finops.attribution"),
}
ALLOWED = {"contracts": ("data_finops.contracts",)}
CLOCK_CALLS = {("datetime", "now"), ("datetime", "utcnow"), ("time", "time")}


def _imports(path: Path) -> list[str]:
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


@pytest.mark.parametrize("package", sorted(FORBIDDEN_IMPORTS))
def test_package_respects_import_boundaries(package):
    for path in (SRC / package).rglob("*.py"):
        for name in _imports(path):
            if name.startswith(ALLOWED.get(package, ())):
                continue
            assert not name.startswith(FORBIDDEN_IMPORTS[package]), f"{path}: imports {name}"


def test_attribution_reads_no_clock():
    for path in (SRC / "attribution").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert (node.value.id, node.attr) not in CLOCK_CALLS, f"{path}: clock read"
