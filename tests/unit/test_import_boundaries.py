"""Enforce docs/implementation/architecture-boundaries.md with a static import scan."""

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "data_finops"

FORBIDDEN_IMPORTS = {
    "contracts": ("data_finops.",),
    "attribution": (
        "data_finops.providers",
        "data_finops.ledger",
        "data_finops.workload",
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
    "workload": ("data_finops.billing", "data_finops.ledger", "data_finops.attribution"),
    # The read model answers from the stores' public interfaces only (no SQL of its own:
    # `test_the_read_model_writes_no_sql`), nothing from the layers above it (spec 06).
    "reporting": ("data_finops.api", "data_finops.cli", "data_finops.providers"),
    # The HTTP adapter reads only through the read model (spec 06).
    "api": (
        "sqlite3",
        "data_finops.ledger",
        "data_finops.calculation",
        "data_finops.attribution",
        "data_finops.providers",
        "data_finops.cli",
    ),
    # spec 08: the AWS collectors fetch and hand over; they never write a store, and they
    # only type their clients, so importing them never needs the `aws` extra.
    "providers/aws": (
        "sqlite3",
        "boto3",
        "botocore",
        "data_finops.ledger",
        "data_finops.workload",
        "data_finops.calculation",
        "data_finops.collection",
    ),
    # spec 08: the Collection Run orchestrates through public interfaces only; its AWS
    # clients are handed to it.
    "collection": (
        "sqlite3",
        "boto3",
        "botocore",
        "data_finops.attribution",
        "data_finops.reporting",
        "data_finops.api",
        "data_finops.cli",
    ),
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


def test_only_the_calculation_run_and_read_model_bring_the_stores_together():
    # architecture-boundaries.md: `calculation`, `reporting` and `collection` (spec 08) are
    # the only places they meet. `cli` may open both only to hand them to the Calculation
    # Run (#115).
    for path in SRC.rglob("*.py"):
        if path.parent.name in ("calculation", "reporting", "collection"):
            continue
        names = _imports(path)
        if path.parent.name == "cli" and "data_finops.calculation" in names:
            continue
        both = any(n.startswith("data_finops.ledger") for n in names) and any(
            n.startswith("data_finops.workload") for n in names
        )
        assert not both, f"{path}: imports both the Ledger and the workload store"


def test_attribution_reads_no_clock():
    for path in (SRC / "attribution").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert (node.value.id, node.attr) not in CLOCK_CALLS, f"{path}: clock read"


def test_pyarrow_is_used_only_by_the_focus_reader():
    # ADR 0018: pyarrow stays inside the export reader so the core does not depend on it.
    allowed = SRC / "billing" / "focus.py"
    for path in SRC.rglob("*.py"):
        if path == allowed:
            continue
        assert not any(name.startswith("pyarrow") for name in _imports(path)), path


SQL_STATEMENT = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|PRAGMA)\b", re.I)


def test_the_read_model_writes_no_sql():
    # spec 06: `reporting` reads only through the stores' public interfaces.
    for path in (SRC / "reporting").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not SQL_STATEMENT.match(node.value), f"{path}: SQL {node.value[:40]!r}"


def test_only_the_command_line_makes_aws_clients():
    # spec 08: everything else is handed a client, so tests stub it and no import needs boto3.
    for path in SRC.rglob("*.py"):
        if path.parent.name == "cli":
            continue
        assert not any(n.startswith(("boto3", "botocore")) for n in _imports(path)), path
