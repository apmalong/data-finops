"""The limits that pages state, stamped from the constants that enforce them (#247).

A page marks each limit inline as `<!-- limit:NAME -->512 MiB<!-- /limit -->`. The build
replaces the text between the markers with the value of NAME's constant, read from the
source without importing it: the site tool stays stdlib-only, and the root environment has
no Pulumi. A number with a unit that a page states by hand fails the build, unless
`EXCEPTIONS` lists it with its reason.
"""

import ast
import html
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from scripts.site.nav import SiteError

__all__ = ["EXCEPTIONS", "LIMITS", "stamp_limits"]


def _bytes(units: tuple[tuple[str, int], ...]) -> Callable[[Any], str]:
    def shown(value: Any) -> str:
        for unit, size in units:
            if value >= size and value % size == 0:
                return f"{value // size:,} {unit}"
        return f"{value:,} bytes"

    return shown


binary = _bytes((("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024)))
decimal = _bytes((("GB", 1000**3), ("MB", 1000**2), ("kB", 1000)))


def count(value: Any) -> str:
    return f"{value:,}"


def megabytes(value: Any) -> str:
    return f"{value:,} MB"


def as_text(value: Any) -> str:
    return str(value)


class Limit(NamedTuple):
    path: str
    """The source file, from the repository root."""
    name: str
    """A module-level constant, or `Class.field` for a dataclass field's default."""
    shown: Callable[[Any], str]


_SRC = "src/data_finops"
_RUN = f"{_SRC}/collection/run.py"
_SETTINGS = "infra/pulumi/aws/deployment/settings.py"
_WORKLOAD = "examples/reference-workload/infra.py"
_BILLING = f"{_SRC}/providers/athena_billing.py"

LIMITS: dict[str, Limit] = {
    "input-file": Limit(f"{_SRC}/contracts/limits.py", "MAX_INPUT_FILE_BYTES", binary),
    "event": Limit(f"{_SRC}/lineage/validation.py", "MAX_EVENT_BYTES", binary),
    "data-file": Limit(_RUN, "MAX_DATA_FILE_BYTES", binary),
    "delivery": Limit(_RUN, "MAX_DELIVERY_BYTES", binary),
    "database": Limit(f"{_SRC}/providers/aws/s3.py", "MAX_DATABASE_BYTES", decimal),
    "expiry-warning-days": Limit(_RUN, "EXPIRY_WARNING_DAYS", count),
    "history-pages-default": Limit(_RUN, "DEFAULT_HISTORY_PAGES", count),
    "history-pages-max": Limit(_RUN, "MAX_HISTORY_PAGES", count),
    "history-page-size": Limit(f"{_SRC}/providers/aws/athena.py", "PAGE_SIZE", count),
    "aws-attempts": Limit(f"{_SRC}/aws_clients.py", "MAX_ATTEMPTS", count),
    "token-min": Limit(f"{_SRC}/api/token.py", "MIN_TOKEN_LENGTH", count),
    "claim-chars": Limit(f"{_SRC}/contracts/dimensions.py", "MAX_CLAIM_CHARS", count),
    "policy-version": Limit(f"{_SRC}/calculation/__init__.py", "POLICY_VERSION", as_text),
    "billable-usage-version": Limit(_BILLING, "POLICY_VERSION", as_text),
    "billable-usage-minimum": Limit(_BILLING, "_V3_MINIMUM", as_text),
    "billable-usage-step": Limit(_BILLING, "_V3_STEP", as_text),
    "ephemeral-storage-default": Limit(_SETTINGS, "Settings.ephemeral_storage_mb", megabytes),
    "collector-memory-default": Limit(_SETTINGS, "Settings.collector_memory_mb", megabytes),
    "inbox-retention-default": Limit(_SETTINGS, "Settings.inbox_retention_days", count),
    "demo-snapshot-days": Limit("infra/pulumi/aws/deployment/demo.py", "SNAPSHOT_DAYS", count),
    "workload-scan-default": Limit(_WORKLOAD, "DEFAULT_SCAN_CUTOFF_BYTES", binary),
    "workload-scan-min": Limit(_WORKLOAD, "ATHENA_MIN_CUTOFF_BYTES", binary),
}
"""Each limit a page can stamp: where its constant is, and how the page shows it."""

EXCEPTIONS: dict[str, dict[str, str]] = {
    "how-to/ingest-athena-history.html": {
        "45 days": "Athena's history retention, an AWS fact that no constant enforces",
    },
    "explanation/deployment-architecture.html": {
        "45 days": "Athena's history retention, an AWS fact that no constant enforces",
        "60 seconds": "an assumption of the cost estimate",
        "300 seconds": "an assumption of the cost estimate",
        "1 GB": "an assumption of the cost estimate",
        "0.1 GB": "an assumption of the cost estimate",
        "12 GB": "an assumption of the cost estimate",
    },
    "explanation/compared-with-aws-tools.html": {
        "45 days": "Athena's history retention, an AWS fact that no constant enforces",
        "7 days": "the length of the evaluation (#232), not a limit of data-finops",
    },
    "explanation/public-demo.html": {
        "10 MB": "the demo's typical query scan, a measured size, not a limit",
    },
    "explanation/allocation-and-coverage.html": {
        "0.001 TB": "the tutorial's bill, a worked example",
        "1,000,000,000 bytes": "the tutorial's query, a worked example",
        "0.000905 TB": "a worked example's result",
        "0.00090949 TB": "a worked example's result",
        "0 bytes": "a query that scans nothing",
        "0.0005 TB": "half the bill's rounding step in the worked example",
        "1 TB": "the unit that versions 2 and 3 read in different ways",
        "10 MB": "version 2's minimum, a superseded rule",
        "40 TB": "the exponent of 2<sup>40</sup> TB, which the text check reads apart",
        "40 bytes": "the exponent of 2<sup>40</sup> bytes, which the text check reads apart",
        "12 bytes": "the exponent of 10<sup>12</sup> bytes, which the text check reads apart",
    },
    "how-to/deploy-to-aws.html": {
        "66 MB": "the size of the Lambda package, measured when the page was written",
    },
    "how-to/tear-down-deployment.html": {
        "66 MB": "the size of the Lambda package, measured when the page was written",
    },
}
"""Each page's numbers that are stated by hand on purpose, with the reason."""

_STAMP = re.compile(r"<!-- limit:([a-z0-9-]+) -->(.*?)<!-- /limit -->")
_HAND_STATED = re.compile(
    r"\b\d[\d,.]*\s(?:KiB|MiB|GiB|kB|MB|GB|TB|bytes|days|characters|attempts)\b"
)
_NOT_PROSE = re.compile(
    r"<pre\b.*?</pre>|<!-- reference:start -->.*?<!-- reference:end -->", re.DOTALL
)
_TAG = re.compile(r"<[^>]+>")


def stamp_limits(repo: Path, page: str, text: str) -> str:
    """`text` with each limit stamped from its constant. Raises SiteError for an unknown
    limit, a constant that cannot be read, or a number with a unit stated by hand."""

    def stamped(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in LIMITS:
            raise SiteError(f"{page} stamps {name}, which is not a limit in LIMITS")
        limit = LIMITS[name]
        shown = limit.shown(_value(repo, limit.path, limit.name))
        return f"<!-- limit:{name} -->{shown}<!-- /limit -->"

    text = _STAMP.sub(stamped, text)
    prose = html.unescape(_TAG.sub(" ", _NOT_PROSE.sub(" ", _STAMP.sub(" ", text))))
    allowed = EXCEPTIONS.get(page, {})
    for found in _HAND_STATED.findall(" ".join(prose.split())):
        if found not in allowed:
            raise SiteError(
                f"{page} states '{found}' by hand: stamp it from its constant, or list it in"
                " EXCEPTIONS (scripts/site/limits.py)"
            )
    return text


def _value(repo: Path, path: str, name: str) -> Any:
    module = _parse(repo / path)
    if "." in name:
        cls, field = name.split(".")
        for node in module.body:
            if isinstance(node, ast.ClassDef) and node.name == cls:
                for item in node.body:
                    if (
                        isinstance(item, ast.AnnAssign)
                        and isinstance(item.target, ast.Name)
                        and item.target.id == field
                        and item.value is not None
                    ):
                        return _evaluate(repo, path, item.value)
        raise SiteError(f"{path} has no default for {name}")
    value = _assigned(module, name)
    if value is None:
        raise SiteError(f"{path} has no constant {name}")
    return _evaluate(repo, path, value)


def _assigned(module: ast.Module, name: str) -> ast.expr | None:
    for node in module.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return node.value
    return None


_OPERATORS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Pow: lambda a, b: a**b,
}


def _evaluate(repo: Path, path: str, node: ast.expr) -> Any:
    """The value of a constant expression: literals, arithmetic, names of this module or
    imported from `data_finops`, `module.NAME`, `Decimal("…")` (as its text) and
    `"sep".join((…))`."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Tuple | ast.List):
        return tuple(_evaluate(repo, path, item) for item in node.elts)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left, right = _evaluate(repo, path, node.left), _evaluate(repo, path, node.right)
        return _OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.Name):
        return _named(repo, path, node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        module = _imported_module(repo, path, node.value.id)
        if module is not None:
            return _value(repo, module, node.attr)
    if isinstance(node, ast.Call) and len(node.args) == 1 and not node.keywords:
        func, (arg,) = node.func, node.args
        if isinstance(func, ast.Name) and func.id == "Decimal":
            return _evaluate(repo, path, arg)
        if isinstance(func, ast.Attribute) and func.attr == "join":
            return str(_evaluate(repo, path, func.value)).join(_evaluate(repo, path, arg))
    raise SiteError(f"{path} has a limit the site cannot read: {ast.unparse(node)}")


def _named(repo: Path, path: str, name: str) -> Any:
    if _assigned(_parse(repo / path), name) is not None:
        return _value(repo, path, name)
    for node in _parse(repo / path).body:
        if isinstance(node, ast.ImportFrom) and any(a.name == name for a in node.names):
            source = _module_path(repo, node.module or "")
            if source is not None:
                return _value(repo, source, name)
    raise SiteError(f"{path} has no constant {name}")


def _imported_module(repo: Path, path: str, name: str) -> str | None:
    """The source of `name` when `path` imports it as a module (`from pkg import name`)."""
    for node in _parse(repo / path).body:
        if isinstance(node, ast.ImportFrom) and any(a.name == name for a in node.names):
            return _module_path(repo, f"{node.module}.{name}")
    return None


def _module_path(repo: Path, module: str) -> str | None:
    if module.split(".")[0] != "data_finops":
        return None
    base = "src/" + module.replace(".", "/")
    for candidate in (f"{base}.py", f"{base}/__init__.py"):
        if (repo / candidate).is_file():
            return candidate
    return None


def _parse(file: Path) -> ast.Module:
    if not file.is_file():
        raise SiteError(f"{file.name}: no such source for a limit")
    return ast.parse(file.read_text(encoding="utf-8"))
