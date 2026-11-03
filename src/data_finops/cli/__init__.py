"""The `data-finops` command line (spec 05; stdlib `argparse`).

`ingest` loads one input file into a database and reports what the Python interface
returned; `calculate` starts a Calculation Run for every Snapshot Scope. Rejected items are
named by position and reason, never by their content (ADR 0012).
"""

import argparse
import sqlite3
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from pydantic import ValidationError

from data_finops.billing.focus import load_export
from data_finops.calculation import run_calculation
from data_finops.ledger import Ledger, SnapshotConflictError
from data_finops.workload.store import ArtifactOutcome, WorkloadStore

OK, REJECTED, FAILED = 0, 1, 2
"""Exit codes: everything stored; something rejected (the rest stored); nothing done."""


class _Failure(Exception):
    """Nothing was done; the message says why, without content from any input file."""


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exited:  # argparse has printed usage or help
        return exited.code if isinstance(exited.code, int) else FAILED
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data-finops", description="Load billing and workload inputs, then allocate the bill."
    )
    commands = parser.add_subparsers(required=True, metavar="command")

    ingest = commands.add_parser("ingest", help="load one input file into a database")
    kinds = ingest.add_subparsers(required=True, metavar="kind")
    ingesters: list[tuple[str, Callable[[argparse.Namespace], int], str, tuple[str, ...]]] = [
        ("focus", _ingest_focus, "a FOCUS Export Snapshot (JSON)", ()),
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
        _database_argument(command)
        command.set_defaults(run=run)

    calculate = commands.add_parser(
        "calculate", help="start a Calculation Run for every Snapshot Scope"
    )
    _database_argument(calculate)
    calculate.set_defaults(run=_calculate)
    return parser


def _database_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument("--db", type=Path, required=True, help="the database file")


def _ingest_focus(args: argparse.Namespace) -> int:
    try:
        export = load_export(args.path)
    except (ValidationError, ValueError):
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


def _workload_store(db: Path) -> WorkloadStore:
    """The workload store, or a failure when the file predates its current columns."""
    try:
        return WorkloadStore(db)
    except RuntimeError:
        raise _Failure(f"{db} was made by an older data-finops; recreate it") from None


def _fail(message: str) -> int:
    print(f"data-finops: {message}", file=sys.stderr)
    return FAILED
