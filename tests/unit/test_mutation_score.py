"""The mutation score against its baseline (#243): a run's results in, a report and an exit out.

The results are what `mutmut results --all true` prints. The mutation tool itself is not tested
(owner, Q98): only this comparison is.
"""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from scripts.mutation_score import main


def _results(**statuses: str) -> str:
    return "".join(
        f"    data_finops.ledger.ledger.{name}: {status}\n" for name, status in statuses.items()
    )


def _run(tmp_path: Path, results: str, baseline: Mapping[str, object]) -> tuple[int, str]:
    (tmp_path / "results.txt").write_text(results, encoding="utf-8")
    (tmp_path / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    report = tmp_path / "report.md"
    code = main(
        [str(tmp_path / "results.txt"), str(tmp_path / "baseline.json"), "--report", str(report)]
    )
    return code, report.read_text(encoding="utf-8")


def test_a_score_below_the_baseline_fails(tmp_path: Path) -> None:
    results = _results(x_a__mutmut_1="killed", x_a__mutmut_2="survived", x_a__mutmut_3="survived")
    baseline = {"score": {"detected": 2, "tested": 3}, "survivors": {}}

    code, report = _run(tmp_path, results, baseline)

    assert code == 1
    assert "1 of 3" in report
    assert "lower than the baseline (2 of 3)" in report


def test_a_score_at_the_baseline_passes_and_counts_as_mutmut_does(tmp_path: Path) -> None:
    # mutmut's score: killed and timed out, over every mutant that was not skipped.
    results = _results(
        x_a__mutmut_1="killed",
        x_a__mutmut_2="timeout",
        x_a__mutmut_3="survived",
        x_a__mutmut_4="skipped",
        x_a__mutmut_5="no tests",
    )
    baseline = {"score": {"detected": 1, "tested": 2}, "survivors": {}}

    code, report = _run(tmp_path, results, baseline)

    assert code == 0
    assert "2 of 4" in report
    assert "lower" not in report


def test_the_report_lists_every_survivor_with_its_recorded_reason(tmp_path: Path) -> None:
    results = _results(
        x_a__mutmut_1="killed",
        x_a__mutmut_2="survived",
        x_a__mutmut_3="no tests",
        x_a__mutmut_4="suspicious",
        x_a__mutmut_5="killed",
    )
    baseline = {
        "score": {"detected": 1, "tested": 5},
        "survivors": {
            "data_finops.ledger.ledger.x_a__mutmut_2": "an equivalent mutant",
            "data_finops.ledger.ledger.x_a__mutmut_5": "killed since",
        },
    }

    code, report = _run(tmp_path, results, baseline)

    assert code == 0
    assert "| data_finops.ledger.ledger.x_a__mutmut_2 | survived | an equivalent mutant |" in report
    assert "| data_finops.ledger.ledger.x_a__mutmut_3 | no tests | not recorded |" in report
    assert "| data_finops.ledger.ledger.x_a__mutmut_4 | suspicious | not recorded |" in report
    assert "x_a__mutmut_1 " not in report
    assert "Recorded, but no longer surviving: data_finops.ledger.ledger.x_a__mutmut_5" in report


@pytest.mark.parametrize(
    ("results", "reason"),
    [
        (_results(x_a__mutmut_1="killed", x_a__mutmut_2="not checked"), "1 mutant was not checked"),
        (
            _results(x_a__mutmut_1="killed", x_a__mutmut_2="check was interrupted by user"),
            "1 mutant was not checked",
        ),
        ("", "no mutants"),
        (_results(x_a__mutmut_1="skipped"), "no mutants"),
        ("    no status here\n", "line 1 is not a mutant and its status"),
    ],
    ids=["not checked", "interrupted", "empty", "all skipped", "not a result"],
)
def test_an_incomplete_run_is_no_score(tmp_path: Path, results: str, reason: str) -> None:
    baseline = {"score": {"detected": 0, "tested": 1}, "survivors": {}}

    code, report = _run(tmp_path, results, baseline)

    assert code == 2
    assert f"No score: {reason}." in report


def test_the_stored_baseline_gives_a_reason_for_every_survivor() -> None:
    baseline = json.loads(
        (
            Path(__file__).resolve().parents[2] / "docs/implementation/mutation-baseline.json"
        ).read_text(encoding="utf-8")
    )

    score = baseline["score"]
    assert 0 < score["detected"] <= score["tested"]
    assert len(baseline["survivors"]) == score["tested"] - score["detected"]
    assert all(reason.strip() for reason in baseline["survivors"].values())
