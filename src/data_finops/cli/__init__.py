"""The `data-finops` command line (spec 05; stdlib `argparse`).

`ingest` loads one input file into a database and reports what the Python interface
returned; `calculate` starts a Calculation Run for every Snapshot Scope. Messages name
positions and reasons, never content from an input file (ADR 0012).
"""

import argparse
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import ValidationError

from data_finops.billing.focus import load_export
from data_finops.calculation import run_calculation
from data_finops.ledger import Ledger, SnapshotConflictError
from data_finops.workload.store import ArtifactOutcome, WorkloadStore

OK, REJECTED, FAILED = 0, 1, 2
"""Exit codes: everything stored; something rejected (the rest stored); nothing done."""


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        code: int = args.run(args)
    except OSError as error:
        return _fail(f"cannot read or write {error.filename or 'a file'}: {error.strerror}")
    return code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data-finops", description="Load billing and workload inputs, then allocate the bill."
    )
    commands = parser.add_subparsers(required=True, metavar="command")

    ingest = commands.add_parser("ingest", help="load one input file into a database")
    kinds = ingest.add_subparsers(required=True, metavar="kind")
    for kind, run, help_text in (
        ("focus", _ingest_focus, "a FOCUS Export Snapshot (JSON)"),
        ("openlineage", _ingest_openlineage, "OpenLineage events (JSON array or JSON Lines)"),
        ("dbt", _ingest_dbt, "a dbt manifest.json or run_results.json"),
        ("athena", _ingest_athena, "an Athena BatchGetQueryExecution response"),
    ):
        command = kinds.add_parser(kind, help=help_text)
        command.add_argument("path", type=Path)
        _database_argument(command)
        command.set_defaults(run=run)
        if kind == "athena":
            command.add_argument("--account-id", required=True)
            command.add_argument("--region", required=True)

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
        return _fail(f"{args.path} is not a FOCUS Export Snapshot")
    with Ledger(args.db) as ledger:
        try:
            result = ledger.import_snapshot(export)
        except SnapshotConflictError:
            return _fail(f"snapshot {export.snapshot.snapshot_id} is stored with other content")
    print(f"focus: {result.outcome} {result.snapshot_id}")
    return OK


def _ingest_openlineage(args: argparse.Namespace) -> int:
    with WorkloadStore(args.db) as store:
        result = store.ingest_events(args.path)
    print(
        f"openlineage: {result.accepted} accepted, {result.duplicates} duplicates,"
        f" {result.changed_copies} changed copies, {len(result.unsupported)} unsupported,"
        f" {len(result.rejected)} rejected"
    )
    _report((r.position, r.reason) for r in result.rejected)
    return REJECTED if result.rejected else OK


def _ingest_dbt(args: argparse.Namespace) -> int:
    with WorkloadStore(args.db) as store:
        result = store.ingest_dbt_artifact(args.path)
    if result.outcome is ArtifactOutcome.REJECTED:
        print(f"dbt: rejected: {result.reason}")
        return REJECTED
    print(f"dbt: {result.outcome} {result.invocation_id}")
    return OK


def _ingest_athena(args: argparse.Namespace) -> int:
    with WorkloadStore(args.db) as store:
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
    _report((r.position, r.detail) for r in result.rejected)
    return REJECTED if result.rejected else OK


def _report(rejections: Iterable[tuple[int, str]]) -> None:
    """Each rejected item by position and reason; never its content."""
    for position, reason in rejections:
        print(f"  {position}: {reason}")


def _calculate(args: argparse.Namespace) -> int:
    with Ledger(args.db) as ledger, WorkloadStore(args.db) as store:
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


def _fail(message: str) -> int:
    print(f"data-finops: {message}", file=sys.stderr)
    return FAILED
