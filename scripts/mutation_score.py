"""The money path's mutation score against its stored baseline (#243).

    mutmut results --all true > results.txt
    python -m scripts.mutation_score results.txt docs/implementation/mutation-baseline.json \\
        --report report.md

The score is mutmut's own (its `badge` command): killed and timed-out mutants over every
mutant that was not skipped. The report gives the score and every mutant that survived, with
the reason that the baseline records for it.

Exit codes: 0 the score is at or above the baseline, 1 it is lower, 2 the run gives no score
(a mutant not checked, no mutants, or a line that is not a result).

Stdlib only. The comparison is exact (fractions), never floating point.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

DETECTED = frozenset({"killed", "timeout"})
SURVIVED = frozenset({"survived", "no tests", "suspicious", "segfault"})
"""The mutants that no test stopped. mutmut counts a segfault as not detected too."""
NOT_CHECKED = frozenset({"not checked", "check was interrupted by user"})
SKIPPED = frozenset({"skipped", "caught by type check"})
STATUSES = DETECTED | SURVIVED | NOT_CHECKED | SKIPPED
"""Every status that `mutmut results` prints (mutmut 3.8.1, `mutmut.stats`)."""

OK, LOWER, NO_SCORE = 0, 1, 2


class NoScore(Exception):
    """The run gives no score. The message says why."""


@dataclass(frozen=True)
class Score:
    detected: int
    tested: int

    @property
    def fraction(self) -> Fraction:
        return Fraction(self.detected, self.tested)

    def __str__(self) -> str:
        return f"{self.detected} of {self.tested}"


def read_results(text: str) -> dict[str, str]:
    """Each mutant's status, from the lines `mutant: status` that `mutmut results` prints."""
    statuses = {}
    for number, line in enumerate(text.splitlines(), start=1):
        name, _, status = line.strip().rpartition(": ")
        if not name or status not in STATUSES:
            raise NoScore(f"line {number} is not a mutant and its status")
        statuses[name] = status
    return statuses


def score_of(statuses: dict[str, str]) -> Score:
    if unchecked := sum(1 for s in statuses.values() if s in NOT_CHECKED):
        many = unchecked != 1
        raise NoScore(
            f"{unchecked} mutant{'s' if many else ''} {'were' if many else 'was'} not checked"
        )
    tested = sum(1 for s in statuses.values() if s not in SKIPPED)
    if not tested:
        raise NoScore("no mutants")
    return Score(sum(1 for s in statuses.values() if s in DETECTED), tested)


def report(statuses: dict[str, str], floor: Score, recorded: dict[str, str]) -> tuple[int, str]:
    """The exit code and the Markdown report of one run against the baseline. NoScore when
    the run gives no score."""
    score = score_of(statuses)
    lower = score.fraction < floor.fraction
    lines = [f"Score: {score} mutants detected. Baseline: {floor}."]
    if lower:
        lines.append(f"The score is lower than the baseline ({floor}).")
    survivors = {n: s for n, s in statuses.items() if s in SURVIVED}
    unrecorded = sum(1 for n in survivors if n not in recorded)
    lines += ["", f"Surviving mutants: {len(survivors)}, not recorded: {unrecorded}", ""]
    lines += ["| Mutant | Status | Reason |", "|---|---|---|"]
    lines += [f"| {n} | {s} | {recorded.get(n, 'not recorded')} |" for n, s in survivors.items()]
    if gone := [n for n in recorded if n not in survivors]:
        lines += ["", f"Recorded, but no longer surviving: {', '.join(gone)}"]
    return (LOWER if lower else OK), "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", type=Path, help="what `mutmut results --all true` printed")
    parser.add_argument("baseline", type=Path, help="the stored baseline (JSON)")
    parser.add_argument("--report", type=Path, help="also write the report to this file")
    args = parser.parse_args(argv)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    try:
        floor = Score(**baseline["score"])
        if floor.tested <= 0:
            raise NoScore("the baseline has no mutants")
        statuses = read_results(args.results.read_text(encoding="utf-8"))
        code, text = report(statuses, floor, baseline["survivors"])
    except NoScore as error:
        code, text = NO_SCORE, f"No score: {error}.\n"
    sys.stdout.buffer.write(text.encode("utf-8"))  # mutant names hold "ǁ", which cp1252 lacks
    sys.stdout.flush()
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
