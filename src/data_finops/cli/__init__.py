"""The `data-finops` command line (spec 05; stdlib `argparse`).

`ingest` loads one input file into a database and reports what the Python interface
returned; `calculate` starts a Calculation Run for every Snapshot Scope; `serve` serves the
API and dashboard (the optional `api` extra) on loopback unless a token is configured
(ADR 0024); `demo` does all of it over the bundled fixtures. Rejected items are named by
position and reason, never by their content (ADR 0012).
"""

import argparse
import ipaddress
import os
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from enum import IntEnum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from data_finops.billing.aws_data_exports import DeliveryError, is_manifest, load_delivery
from data_finops.billing.focus import load_export
from data_finops.calculation import run_calculation
from data_finops.ledger import Ledger, SnapshotConflictError
from data_finops.workload.store import ArtifactOutcome, WorkloadStore


class ExitCode(IntEnum):
    """Every code the command exits with; the site's exit codes page is checked against it."""

    OK = 0
    """Everything was stored."""
    REJECTED = 1
    """Something was rejected; the rest was stored."""
    FAILED = 2
    """Nothing was done."""


OK, REJECTED, FAILED = ExitCode.OK, ExitCode.REJECTED, ExitCode.FAILED

StartServer = Callable[[Any, str, int], None]
"""Serves an ASGI app on a host and port until stopped; uvicorn unless a caller injects one."""

DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 8000
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
"""The hand-written fixtures `demo` loads: present in a source checkout, not in the wheel."""
_INSTALL_HINT = "the API needs the api extra: pip install 'data-finops[api]'"


class _Failure(Exception):
    """Nothing was done; the message says why, without content from any input file."""


def main(argv: Sequence[str] | None = None, *, start_server: StartServer | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exited:  # argparse has printed usage or help
        return exited.code if isinstance(exited.code, int) else FAILED
    args.start_server = start_server or _uvicorn
    try:
        code: int = args.run(args)
    except _Failure as failure:
        return _fail(str(failure))
    except OSError as error:
        return _fail(f"cannot read or write {error.filename or 'a file'}: {error.strerror}")
    except sqlite3.OperationalError:
        return _fail(f"cannot open {args.db}")
    except sqlite3.DatabaseError:
        return _fail(f"{args.db} is not a data-finops database")
    return code


def build_parser() -> argparse.ArgumentParser:
    """The command line's parser; the site's CLI reference is generated from it (spec 07)."""
    parser = argparse.ArgumentParser(
        prog="data-finops", description="Load billing and workload inputs, then allocate the bill."
    )
    commands = parser.add_subparsers(required=True, metavar="command")

    ingest = commands.add_parser("ingest", help="load one input file into a database")
    kinds = ingest.add_subparsers(required=True, metavar="kind")
    ingesters: list[tuple[str, Callable[[argparse.Namespace], int], str, tuple[str, ...]]] = [
        (
            "focus",
            _ingest_focus,
            "a FOCUS Export Snapshot (JSON), or an AWS Data Exports Delivery by its"
            " downloaded <export>-Manifest.json",
            (),
        ),
        ("openlineage", _ingest_openlineage, "OpenLineage events (JSON array or JSON Lines)", ()),
        ("dbt", _ingest_dbt, "a dbt manifest.json or run_results.json", ()),
        (
            "athena",
            _ingest_athena,
            "an Athena BatchGetQueryExecution response",
            ("--account-id", "--region"),
        ),
    ]
    for kind, run, help_text, required_options in ingesters:
        command = kinds.add_parser(kind, help=help_text)
        command.add_argument("path", type=Path)
        for option in required_options:
            command.add_argument(option, required=True)
        if kind == "focus":
            command.add_argument(
                "--data-dir",
                type=Path,
                help="where a Delivery's data files were downloaded (default: beside its manifest)",
            )
        _database_argument(command)
        command.set_defaults(run=run)

    calculate = commands.add_parser(
        "calculate", help="start a Calculation Run for every Snapshot Scope"
    )
    _database_argument(calculate)
    calculate.set_defaults(run=_calculate)

    serve = commands.add_parser("serve", help="serve the API and dashboard over a database")
    _database_argument(serve)
    _address_arguments(serve)
    serve.add_argument(
        "--token-file",
        type=Path,
        help="a file holding the bearer token (or set DATA_FINOPS_TOKEN); required off loopback",
    )
    serve.set_defaults(run=_serve)

    demo = commands.add_parser(
        "demo", help="load the fixtures into a new database, calculate and serve on loopback"
    )
    demo.add_argument("--port", type=int, default=DEFAULT_PORT)
    demo.add_argument("--fixtures", type=Path, default=FIXTURES, help=argparse.SUPPRESS)
    demo.set_defaults(run=_demo)
    return parser


def _address_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--host", default=DEFAULT_HOST, help="default: loopback only")
    command.add_argument("--port", type=int, default=DEFAULT_PORT)


def _database_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument("--db", type=Path, required=True, help="the database file")


def _ingest_focus(args: argparse.Namespace) -> int:
    try:
        if is_manifest(args.path):
            export = load_delivery(args.path, args.data_dir)
        else:
            export = load_export(args.path)
    except DeliveryError as error:  # names files only, never contents
        raise _Failure(f"{args.path} is not a usable AWS Data Exports Delivery: {error}") from None
    except (ValidationError, ValueError, OSError):
        raise _Failure(f"{args.path} is not a FOCUS Export Snapshot") from None
    with Ledger(args.db) as ledger:
        try:
            result = ledger.import_snapshot(export)
        except SnapshotConflictError:
            raise _Failure(
                f"Export Snapshot {export.snapshot.snapshot_id} is stored with other content"
            ) from None
    print(f"focus: {result.outcome} {result.snapshot_id}")
    return OK


def _ingest_openlineage(args: argparse.Namespace) -> int:
    with _workload_store(args.db) as store:
        result = store.ingest_events(args.path)
    print(
        f"openlineage: {result.accepted} accepted, {result.duplicates} duplicates,"
        f" {result.changed_copies} changed copies, {len(result.unsupported)} unsupported,"
        f" {len(result.rejected)} rejected"
    )
    _print_rejections((r.position, r.reason) for r in (*result.rejected, *result.unsupported))
    return REJECTED if result.rejected else OK


def _ingest_dbt(args: argparse.Namespace) -> int:
    with _workload_store(args.db) as store:
        result = store.ingest_dbt_artifact(args.path)
    if result.outcome is ArtifactOutcome.REJECTED:
        print(f"dbt: rejected: {result.reason}")
        return REJECTED
    print(f"dbt: {result.outcome} {result.invocation_id}")
    return OK


def _ingest_athena(args: argparse.Namespace) -> int:
    with _workload_store(args.db) as store:
        result = store.ingest_query_history(
            args.path, account_id=args.account_id, region=args.region
        )
    if result.file_rejection is not None:
        print(f"athena: rejected: {result.file_rejection}")
        return REJECTED
    print(
        f"athena: {result.accepted} accepted, {result.duplicates} duplicates,"
        f" {result.changed_copies} changed copies, {len(result.rejected)} rejected"
    )
    _print_rejections((r.position, r.detail) for r in result.rejected)
    return REJECTED if result.rejected else OK


def _print_rejections(rejections: Iterable[tuple[int, str]]) -> None:
    for position, reason in sorted(rejections):
        print(f"  {position}: {reason}")


def _calculate(args: argparse.Namespace) -> int:
    if not args.db.is_file():
        raise _Failure(f"no database at {args.db}")
    with Ledger(args.db) as ledger, _workload_store(args.db) as store:
        scopes = ledger.scopes()
        if not scopes:
            print("calculate: no Export Snapshot to calculate")
        for scope in scopes:
            result = run_calculation(ledger, store, scope)
            print(
                f"calculate: {scope.billing_account_id}"
                f" {scope.billing_period_start.date().isoformat()} {scope.source}:"
                f" {result.outcome} {result.calculation_run_id}"
            )
    return OK


def _serve(args: argparse.Namespace) -> int:
    if not args.db.is_file():
        raise _Failure(f"no database at {args.db}")
    return _serve_database(args.db, args.host, args.port, args.token_file, args.start_server)


def _demo(args: argparse.Namespace) -> int:
    _api()  # before writing anything: without the extra there is nothing to serve
    fixtures: Path = args.fixtures
    if not (fixtures / "focus").is_dir():
        raise _Failure("the demo needs a source checkout with its fixtures directory")
    folder = Path(tempfile.mkdtemp(prefix="data-finops-demo-"))
    try:
        return _run_demo(folder / "data-finops.sqlite", fixtures, args.port, args.start_server)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _run_demo(db: Path, fixtures: Path, port: int, start_server: StartServer) -> int:
    print(f"demo: database {db}")
    athena = {"account_id": "123456789012", "region": "us-east-1"}
    steps: list[tuple[Callable[[argparse.Namespace], int], Path, dict[str, str]]] = [
        (_ingest_focus, fixtures / "focus" / "export-2026-10.json", {}),
        *(
            (_ingest_athena, fixtures / "athena" / name, athena)
            for name in (
                "query-executions.json",
                "link-outcomes.json",
                "link-outcomes-restated.json",
            )
        ),
        (_ingest_openlineage, fixtures / "openlineage" / "events.json", {}),
        (_ingest_dbt, fixtures / "dbt" / "manifest.json", {}),
        (_ingest_dbt, fixtures / "dbt" / "run_results.json", {}),
    ]
    for ingest, path, extra in steps:
        ingest(argparse.Namespace(path=path, db=db, **extra))
    _calculate(argparse.Namespace(db=db))
    # The local demo needs no secret (ADR 0024): a configured token is not applied to it.
    return _serve_database(db, DEFAULT_HOST, port, None, start_server, environ={})


def _serve_database(
    db: Path,
    host: str,
    port: int,
    token_file: Path | None,
    start_server: StartServer,
    *,
    environ: Mapping[str, str] = os.environ,
) -> int:
    api = _api()
    try:
        token = api.read_token(environ=environ, file=token_file)
    except ValueError as error:
        raise _Failure(str(error)) from None
    if token is None and not _is_loopback(host):
        raise _Failure(
            f"refusing to serve on {host} without a token: set {api.TOKEN_VARIABLE} or"
            " --token-file (ADR 0024)"
        )
    app = api.create_app(db, token=token)
    print(f"serving {db} at http://{host}:{port}/ (Ctrl+C to stop)")
    start_server(app, host, port)
    return OK


def _api() -> Any:
    """The `api` package, imported only when needed so ingest and calculate never need it;
    uvicorn, the other half of the extra, is checked here too, before anything is written."""
    try:
        import uvicorn  # noqa: F401

        import data_finops.api as api
    except ImportError:
        raise _Failure(_INSTALL_HINT) from None
    return api


def _is_loopback(host: str) -> bool:
    """Only a literal loopback address counts: a name such as `localhost` is resolved by the
    system and could point anywhere (ADR 0024)."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _uvicorn(app: Any, host: str, port: int) -> None:  # pragma: no cover - binds a socket
    import uvicorn

    uvicorn.run(app, host=host, port=port)


def _workload_store(db: Path) -> WorkloadStore:
    """The workload store, or a failure when the file predates its current columns."""
    try:
        return WorkloadStore(db)
    except RuntimeError:
        raise _Failure(f"{db} was made by an older data-finops; recreate it") from None


def _fail(message: str) -> int:
    print(f"data-finops: {message}", file=sys.stderr)
    return FAILED
