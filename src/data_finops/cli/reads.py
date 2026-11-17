"""The reads of the command line (spec 10 §Command catalog): each one against a local Database
(`--db`) or a Deployment (`--deployment`), with the same JSON as its HTTP route (spec 06)."""

import argparse
import json
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, get_args

from pydantic import BaseModel

from data_finops.cli.deployments import Deployment, DeploymentFailed
from data_finops.contracts.allocation import AllocationCategory
from data_finops.contracts.billing import CostBasis
from data_finops.reporting import (
    MAX_PAGE_SIZE,
    AmbiguousScope,
    ConflictingFilters,
    DatabaseUnavailable,
    NotFound,
    Reporting,
    RollupLevel,
)


class ReadFailed(Exception):
    """Nothing was read; the message says why, without content from the Database."""


Options = dict[str, Any]
"""The parsed options of one read, by the route's parameter names."""

Route = tuple[str, Options]
"""A path on the API, and its query parameters."""


@dataclass(frozen=True)
class _Read:
    """One read: its options, its route on the API, and the same read on a local Database.
    `route` and `local` both take the parsed options."""

    name: str
    help_text: str
    add_options: Callable[[argparse.ArgumentParser], None]
    route: Callable[[Options], Route]
    local: Callable[[Reporting, Options], BaseModel]


def _page_size(text: str) -> int:
    size = int(text)
    if not 1 <= size <= MAX_PAGE_SIZE:
        raise argparse.ArgumentTypeError(f"give 1 to {MAX_PAGE_SIZE}")
    return size


def _offset(text: str) -> int:
    offset = int(text)
    if offset < 0:
        raise argparse.ArgumentTypeError("give 0 or more")
    return offset


def _scope_options(command: argparse.ArgumentParser, *, run: bool = True) -> None:
    """The options that select a Snapshot Scope, as the routes take them (spec 06)."""
    if run:
        command.add_argument("--calculation-run-id", help="the snapshot of this Calculation Run")
    command.add_argument("--billing-account-id")
    command.add_argument(
        "--billing-period",
        type=date.fromisoformat,
        help="the first day of the period (YYYY-MM-DD)",
    )
    if run:
        command.add_argument("--source", help="the source of the Export Snapshot")


def _cost_basis_option(command: argparse.ArgumentParser, default: str | None = None) -> None:
    command.add_argument(
        "--cost-basis",
        choices=[basis.value for basis in CostBasis],
        default=default,
        help=None if default else "default: each Cost Basis",
    )


def _page_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--limit", type=_page_size, default=100, help=f"1 to {MAX_PAGE_SIZE}")
    command.add_argument("--offset", type=_offset, default=0)


def _summary_options(command: argparse.ArgumentParser) -> None:
    _cost_basis_option(command, CostBasis.BILLED_COST.value)
    _scope_options(command, run=False)


def _allocations_options(command: argparse.ArgumentParser) -> None:
    _scope_options(command)
    command.add_argument("--target-id")
    command.add_argument("--category", choices=[category.value for category in AllocationCategory])
    command.add_argument("--billing-line-ref")
    _cost_basis_option(command)
    _page_options(command)


def _rollups_options(command: argparse.ArgumentParser) -> None:
    level = command.add_mutually_exclusive_group(required=True)
    level.add_argument("--level", choices=list(get_args(RollupLevel)))
    level.add_argument("--dimension", metavar="NAME", help="the Roll-ups of one Cost Dimension")
    _scope_options(command)
    _page_options(command)


def _reconciliation_options(command: argparse.ArgumentParser) -> None:
    _scope_options(command)
    _cost_basis_option(command)
    _page_options(command)


def _lineage_options(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--run-id",
        help="the Workload Run whose lineage to show. If not given, the topmost runs are shown.",
    )
    _page_options(command)


def _explain_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("ref", help="the Allocation, as <calculation_run_id>#<position>")


def _with_enums(options: Options) -> Options:
    """The options with the enum values typed, as the route's validation types them."""
    typed = dict(options)
    if typed.get("cost_basis") is not None:
        typed["cost_basis"] = CostBasis(typed["cost_basis"])
    if typed.get("category") is not None:
        typed["category"] = AllocationCategory(typed["category"])
    return typed


def _route_at(path: str) -> Callable[[Options], Route]:
    """The route of a read whose options are all query parameters of `path`."""
    return lambda options: (path, options)


def _rollups_query(options: Options) -> Options:
    return {key: value for key, value in options.items() if key not in {"level", "dimension"}}


def _rollups_route(options: Options) -> Route:
    if options["dimension"] is not None:
        name = urllib.parse.quote(options["dimension"], safe="")
        return f"/v1/rollups/dimensions/{name}", _rollups_query(options)
    return "/v1/rollups", {"level": options["level"], **_rollups_query(options)}


def _rollups_local(reporting: Reporting, options: Options) -> BaseModel:
    if options["dimension"] is None:
        return reporting.rollups(level=options["level"], **_rollups_query(options))
    return reporting.dimension_rollups(name=options["dimension"], **_rollups_query(options))


def _lineage_local(reporting: Reporting, options: Options) -> BaseModel:
    if options["run_id"] is None:
        return reporting.root_runs(limit=options["limit"], offset=options["offset"])
    return reporting.lineage(options["run_id"])


def _explain_route(options: Options) -> Route:
    return f"/v1/allocations/{urllib.parse.quote(options['ref'], safe='')}/explanation", {}


READS = [
    _Read(
        "summary",
        "the Cost Summary of each Snapshot Scope (GET /v1/costs/summary)",
        _summary_options,
        _route_at("/v1/costs/summary"),
        lambda reporting, options: reporting.cost_summary(**_with_enums(options)),
    ),
    _Read(
        "allocations",
        "one page of one Allocation Snapshot (GET /v1/allocations)",
        _allocations_options,
        _route_at("/v1/allocations"),
        lambda reporting, options: reporting.allocations(**_with_enums(options)),
    ),
    _Read(
        "rollups",
        "what each Model Execution, Workload Run or Cost Dimension value cost (GET /v1/rollups)",
        _rollups_options,
        _rollups_route,
        _rollups_local,
    ),
    _Read(
        "reconciliation",
        "one Allocation Snapshot against the bill (GET /v1/reconciliation)",
        _reconciliation_options,
        _route_at("/v1/reconciliation"),
        lambda reporting, options: reporting.reconciliation(**_with_enums(options)),
    ),
    _Read(
        "provenance",
        "how one Allocation Snapshot was made (GET /v1/provenance)",
        _scope_options,
        _route_at("/v1/provenance"),
        lambda reporting, options: reporting.provenance(**options),
    ),
    _Read(
        "lineage",
        "the topmost Workload Runs, or the lineage of one run (GET /v1/lineage)",
        _lineage_options,
        _route_at("/v1/lineage"),
        _lineage_local,
    ),
    _Read(
        "explain",
        "the Cost Explanation of one Allocation (GET /v1/allocations/{ref}/explanation)",
        _explain_options,
        _explain_route,
        lambda reporting, options: reporting.explanation(options["ref"]),
    ),
]


def add_reads(commands: Any) -> None:
    """Add each read to the command line's subparsers."""
    for read in READS:
        command = commands.add_parser(read.name, help=read.help_text)
        read.add_options(command)
        # In the parser's order, so the query string is the same on every run.
        own = tuple(action.dest for action in command._actions if action.dest != "help")
        target = command.add_mutually_exclusive_group(required=True)
        target.add_argument("--db", type=Path, help="a local database file")
        target.add_argument(
            "--deployment",
            metavar="NAME",
            help="a Deployment: the table [NAME] in ~/.data-finops/deployments.toml, with"
            " api_url, region and profile",
        )
        command.add_argument(
            "--format", choices=["table", "json"], default="table", help="default: table"
        )
        command.set_defaults(run=lambda args, read=read, own=own: _run(read, own, args))


def _run(read: _Read, own: tuple[str, ...], args: argparse.Namespace) -> int:
    options = {name: getattr(args, name) for name in own}
    if args.db is not None:
        data = _local(read, args.db, options)
    else:
        data = _remote(read, args.deployment, options)
    print(json.dumps(data, indent=2) if args.format == "json" else table(data))
    return 0


def _local(read: _Read, db: Path, options: Options) -> Any:
    if not db.is_file():
        raise ReadFailed(f"no database at {db}")
    try:
        with Reporting(db) as reporting:
            return read.local(reporting, options).model_dump(mode="json")
    except DatabaseUnavailable:
        raise ReadFailed(f"{db} is not a current data-finops database") from None
    except NotFound:
        raise ReadFailed("nothing matches the request") from None
    except AmbiguousScope:
        raise ReadFailed(
            "more than one Snapshot Scope matches: give more filters (see --help)"
        ) from None
    except ConflictingFilters:
        raise ReadFailed("give --calculation-run-id or the scope filters, not both") from None


def _remote(read: _Read, name: str, options: Options) -> Any:
    path, query = read.route(options)
    try:
        return Deployment.named(name).get(
            path, {key: _query_value(value) for key, value in query.items() if value is not None}
        )
    except DeploymentFailed as error:
        raise ReadFailed(str(error)) from None


def _query_value(value: Any) -> str:
    """An option as the route's query string spells it."""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def table(data: Any) -> str:
    """A plain-text view of a read's JSON: its single values as `key: value` lines, then each
    list of objects as a table, nested objects flattened to dotted columns. Use `--format
    json` for every field."""
    lines: list[str] = []
    rows_of: dict[str, list[dict[str, Any]]] = {}
    for key, value in _flat(data).items():
        lines.append(f"{key}: {_cell(value)}")
    for key, value in data.items() if isinstance(data, dict) else []:
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            rows_of[key] = [_flat(item) for item in value]
    for key, rows in rows_of.items():
        columns = list(dict.fromkeys(column for row in rows for column in row))
        cells = [[_cell(row.get(column)) for column in columns] for row in rows]
        widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(columns)]
        lines += ["", f"{key}:"]
        lines.append("  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True)))
        lines += ["  ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)) for r in cells]
    return "\n".join(line.rstrip() for line in lines).lstrip("\n")


def _flat(value: Any, prefix: str = "") -> dict[str, Any]:
    """The single values of an object, nested objects as dotted keys; lists are left out."""
    flat: dict[str, Any] = {}
    for key, item in value.items() if isinstance(value, dict) else []:
        if isinstance(item, dict):
            flat.update(_flat(item, f"{prefix}{key}."))
        elif not isinstance(item, list):
            flat[f"{prefix}{key}"] = item
    return flat


def _cell(value: Any) -> str:
    return "" if value is None else str(value)
