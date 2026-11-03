"""The `data-finops` command line through `main(argv)` on a temporary database (#115; spec 05).

Results are read back through the Ledger's public interface, never from tables.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from data_finops.billing.focus import load_export
from data_finops.cli import main
from data_finops.contracts.billing import CostBasis
from data_finops.ledger import Ledger

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
ACCOUNT, REGION = "123456789012", "us-east-1"
SCOPE = load_export(FIXTURES / "focus" / "export-2026-10.json").snapshot.scope


def _ingest_all(db: Path) -> list[int]:
    athena = ["--account-id", ACCOUNT, "--region", REGION]
    commands = [
        ["focus", str(FIXTURES / "focus" / "export-2026-10.json")],
        *(
            ["athena", str(FIXTURES / "athena" / name), *athena]
            for name in (
                "query-executions.json",
                "link-outcomes.json",
                "link-outcomes-restated.json",
            )
        ),
        ["openlineage", str(FIXTURES / "openlineage" / "events.json")],
        ["dbt", str(FIXTURES / "dbt" / "manifest.json")],
        ["dbt", str(FIXTURES / "dbt" / "run_results.json")],
    ]
    return [main(["ingest", *command, "--db", str(db)]) for command in commands]


def test_ingesting_every_fixture_and_calculating_allocates_the_bill(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "data-finops.sqlite"
    assert _ingest_all(db) == [0] * 7
    assert main(["calculate", "--db", str(db)]) == 0
    assert "new" in capsys.readouterr().out.splitlines()[-1]
    with Ledger(db) as ledger:
        stored = ledger.current_allocations(SCOPE)
    assert stored is not None
    billed = [a.amount for a in stored.allocations if a.cost_basis == CostBasis.BILLED_COST]
    assert sorted(billed) == [Decimal("4.00"), Decimal("6.00")]


def test_running_everything_twice_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "data-finops.sqlite"
    _ingest_all(db)
    main(["calculate", "--db", str(db)])
    capsys.readouterr()
    assert _ingest_all(db) == [0] * 7
    assert main(["calculate", "--db", str(db)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "focus: replay aws-focus-123456789012-2026-10-v1"
    assert "openlineage: 0 accepted, 5 duplicates" in out[4]
    assert out[-1].startswith("calculate: 123456789012 2026-10-01 aws-data-exports:FOCUS_1_2_AWS")
    assert " replay calc-" in out[-1]


def test_rejected_events_exit_one_and_are_named_by_position_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    invalid = FIXTURES / "openlineage" / "invalid" / "mixed.jsonl"
    code = main(["ingest", "openlineage", str(invalid), "--db", str(tmp_path / "db.sqlite")])
    out = capsys.readouterr().out
    assert code == 1
    assert out.splitlines()[0] == (
        "openlineage: 3 accepted, 0 duplicates, 0 changed copies, 2 unsupported, 4 rejected"
    )
    assert "  2: not valid JSON" in out.splitlines()
    assert "not-a-uuid-secret-value" not in out


def test_a_rejected_dbt_or_athena_file_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = str(tmp_path / "db.sqlite")
    not_dbt = FIXTURES / "openlineage" / "events.json"
    assert main(["ingest", "dbt", str(not_dbt), "--db", db]) == 1
    athena = ["--account-id", ACCOUNT, "--region", REGION]
    assert main(["ingest", "athena", str(not_dbt), *athena, "--db", db]) == 1
    dbt_line, athena_line = capsys.readouterr().out.splitlines()
    assert dbt_line.startswith("dbt: rejected: ") and athena_line.startswith("athena: rejected: ")
    assert "00000000-0000-4000-8000" not in dbt_line + athena_line


def test_a_missing_file_exits_two_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = str(tmp_path / "missing.json")
    assert main(["ingest", "openlineage", missing, "--db", str(tmp_path / "db.sqlite")]) == 2
    assert capsys.readouterr().err.startswith("data-finops: cannot read or write ")


def test_a_file_that_is_not_a_focus_export_exits_two_without_echoing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    events = str(FIXTURES / "openlineage" / "events.json")
    assert main(["ingest", "focus", events, "--db", str(tmp_path / "db.sqlite")]) == 2
    err = capsys.readouterr().err
    assert err == f"data-finops: {events} is not a FOCUS Export Snapshot\n"


def test_calculating_an_empty_database_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["calculate", "--db", str(tmp_path / "db.sqlite")]) == 0
    assert capsys.readouterr().out == "calculate: no Export Snapshot to calculate\n"


@pytest.mark.parametrize(
    "argv",
    [[], ["ingest"], ["ingest", "csv", "x"], ["calculate"], ["ingest", "athena", "x", "--db", "d"]],
)
def test_bad_arguments_exit_two_with_usage(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        main(argv)
    assert exited.value.code == 2
