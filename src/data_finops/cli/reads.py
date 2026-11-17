"""The reads of the command line (spec 10 §Command catalog): each one against a local Database
(`--db`) or a Deployment (`--deployment`), with the same JSON as its HTTP route (spec 06)."""

import argparse
import ipaddress
import json
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, get_args

from pydantic import BaseModel

from data_finops import aws_clients
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


Params = dict[str, Any]


Route = tuple[str, Params]
"""A path on the API, and its query parameters."""


class _Read:
    """One read: its options, its route on the API, and the same read on a local Database.
    Both take the parsed options: `route` returns the path and the query."""

    def __init__(
        self,
        name: str,
        help_text: str,
        add_options: Callable[[argparse.ArgumentParser], None],
        route: Callable[[Params], Route],
        local: Callable[[Reporting, Params], BaseModel],
    ) -> None:
        self.name, self.help_text, self.add_options = name, help_text, add_options
        self.route, self.local = route, local


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
        help=f"default: {default}" if default else "default: every Cost Basis",
    )


def _page_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--limit", type=int, default=100, help=f"1 to {MAX_PAGE_SIZE}")
    command.add_argument("--offset", type=int, default=0)


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
        "--run-id", help="the lineage of this Workload Run. Without it, the topmost runs."
    )
    _page_options(command)


def _explain_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("ref", help="the Allocation, as <calculation_run_id>#<position>")


def _enums(params: Params) -> Params:
    """The query with its enum values typed, as the route's validation types them."""
    typed = dict(params)
    if typed.get("cost_basis") is not None:
        typed["cost_basis"] = CostBasis(typed["cost_basis"])
    if typed.get("category") is not None:
        typed["category"] = AllocationCategory(typed["category"])
    return typed


def _rollups_route(params: Params) -> Route:
    query = {key: value for key, value in params.items() if key not in {"level", "dimension"}}
    if params["dimension"] is not None:
        name = urllib.parse.quote(params["dimension"], safe="")
        return f"/v1/rollups/dimensions/{name}", query
    return "/v1/rollups", {"level": params["level"], **query}


def _rollups_local(reporting: Reporting, params: Params) -> BaseModel:
    _, query = _rollups_route(params)
    if params["dimension"] is None:
        return reporting.rollups(**query)
    return reporting.dimension_rollups(name=params["dimension"], **query)


def _lineage_local(reporting: Reporting, query: Params) -> BaseModel:
    if query["run_id"] is None:
        return reporting.root_runs(limit=query["limit"], offset=query["offset"])
    return reporting.lineage(query["run_id"])


def _same(path: str) -> Callable[[Params], Route]:
    return lambda params: (path, params)


READS = [
    _Read(
        "summary",
        "the Cost Summary of each Snapshot Scope (GET /v1/costs/summary)",
        _summary_options,
        _same("/v1/costs/summary"),
        lambda reporting, query: reporting.cost_summary(**_enums(query)),
    ),
    _Read(
        "allocations",
        "one page of one Allocation Snapshot (GET /v1/allocations)",
        _allocations_options,
        _same("/v1/allocations"),
        lambda reporting, query: reporting.allocations(**_enums(query)),
    ),
    _Read(
        "rollups",
        "what each Model Execution, Workload Run or dimension value cost (GET /v1/rollups)",
        _rollups_options,
        _rollups_route,
        _rollups_local,
    ),
    _Read(
        "reconciliation",
        "one Allocation Snapshot against the bill (GET /v1/reconciliation)",
        _reconciliation_options,
        _same("/v1/reconciliation"),
        lambda reporting, query: reporting.reconciliation(**_enums(query)),
    ),
    _Read(
        "provenance",
        "how one Allocation Snapshot was made (GET /v1/provenance)",
        _scope_options,
        _same("/v1/provenance"),
        lambda reporting, query: reporting.provenance(**query),
    ),
    _Read(
        "lineage",
        "the topmost Workload Runs, or one run's lineage (GET /v1/lineage)",
        _lineage_options,
        _same("/v1/lineage"),
        _lineage_local,
    ),
    _Read(
        "explain",
        "the Cost Explanation of one Allocation (GET /v1/allocations/{ref}/explanation)",
        _explain_options,
        lambda params: (
            f"/v1/allocations/{urllib.parse.quote(params['ref'], safe='')}/explanation",
            {},
        ),
        lambda reporting, query: reporting.explanation(query["ref"]),
    ),
]


def add_reads(commands: Any) -> None:
    """Add each read to the command line's subparsers."""
    for read in READS:
        command = commands.add_parser(read.name, help=read.help_text)
        read.add_options(command)
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
        command.set_defaults(run=lambda args, read=read: _run(read, args))


def _run(read: _Read, args: argparse.Namespace) -> int:
    params = {
        key: value
        for key, value in vars(args).items()
        if key not in {"db", "deployment", "format", "run", "start_server", "aws_clients"}
    }
    if args.db is not None:
        data = _local(read, args.db, params)
    else:
        data = _remote(read, deployment(args.deployment), params)
    print(json.dumps(data, indent=2) if args.format == "json" else table(data))
    return 0


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


def _local(read: _Read, db: Path, params: Params) -> Any:
    if not db.is_file():
        raise ReadFailed(f"no database at {db}")
    try:
        with Reporting(db) as reporting:
            return read.local(reporting, params).model_dump(mode="json")
    except DatabaseUnavailable:
        raise ReadFailed(f"{db} is not a current data-finops database") from None
    except NotFound:
        raise ReadFailed("nothing matches the request") from None
    except AmbiguousScope:
        raise ReadFailed(
            "several Snapshot Scopes match: narrow with --billing-account-id, --billing-period"
            " and --source, or give --calculation-run-id"
        ) from None
    except ConflictingFilters:
        raise ReadFailed("give --calculation-run-id or the scope filters, not both") from None


DEPLOYMENT_KEYS = {"api_url", "region", "profile", "issuer", "client_id", "audience"}
"""What an entry of deployments.toml may hold: no token and no secret (spec 10 §Targets)."""

_TIMEOUT_SECONDS = 30


class Deployment:
    """One entry of `~/.data-finops/deployments.toml`."""

    def __init__(self, name: str, api_url: str, region: str, profile: str) -> None:
        self.name, self.api_url, self.region, self.profile = name, api_url, region, profile


def deployments_file() -> Path:
    return Path.home() / ".data-finops" / "deployments.toml"


def deployment(name: str) -> Deployment:
    """The Deployment `name`. A fault names the file and the key, never a value."""
    path = deployments_file()
    try:
        entries = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReadFailed(f"no {path}: deploy writes it, or add the Deployment by hand") from None
    except tomllib.TOMLDecodeError:
        raise ReadFailed(f"{path} is not valid TOML") from None
    entry = entries.get(name)
    if not isinstance(entry, dict):
        raise ReadFailed(f"{path} has no Deployment {name}")
    unknown = sorted(set(entry) - DEPLOYMENT_KEYS)
    if unknown:
        raise ReadFailed(f"{path}: [{name}] has unknown keys: {', '.join(unknown)}")
    for key in ("api_url", "region"):
        if not isinstance(entry.get(key), str):
            raise ReadFailed(f"{path}: [{name}] needs {key}")
    if not isinstance(entry.get("profile"), str):
        raise ReadFailed(
            f"{path}: [{name}] has no profile: OIDC sign-in is not in this version of data-finops"
        )
    api_url: str = entry["api_url"]
    if not _secure(api_url):
        raise ReadFailed(f"{path}: [{name}] api_url must use https")
    return Deployment(name, api_url, entry["region"], entry["profile"])


def _secure(url: str) -> bool:
    """https, or http to a literal loopback address only (as `serve`, ADR 0024)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        return True
    try:
        return parts.scheme == "http" and ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        return False


def _remote(read: _Read, target: Deployment, params: Params) -> Any:
    path, query = read.route(params)
    encoded = urllib.parse.urlencode(
        {key: _wire(value) for key, value in query.items() if value is not None}
    )
    url = target.api_url.rstrip("/") + path + (f"?{encoded}" if encoded else "")
    try:
        headers = aws_clients.signed_headers(url, region=target.region, profile=target.profile)
    except aws_clients.ClientsUnavailable as error:
        raise ReadFailed(str(error)) from None
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        # nosec B310 below: deployment() allows only https, or http to a loopback address.
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # nosec B310
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise ReadFailed(
            f"the Deployment answered {error.code}: {_problem_detail(error)}"
        ) from None
    except urllib.error.URLError as error:
        raise ReadFailed(f"cannot reach the Deployment {target.name}: {error.reason}") from None


def _problem_detail(error: urllib.error.HTTPError) -> str:
    """The Problem Details `detail` of an error answer (spec 06), or its reason phrase."""
    try:
        detail = json.loads(error.read()).get("detail")
    except (ValueError, AttributeError):
        detail = None
    return detail if isinstance(detail, str) else str(error.reason)


def _wire(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)
