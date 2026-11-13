"""The gate command (#241): one command runs every check, stops at the first failure, and
prints a summary block that a PR body can quote."""

import pytest

from scripts import gates


def _runner(
    failing: str | None = None, outputs: dict[str, str] | None = None
) -> tuple[gates.Runner, list[str]]:
    ran: list[str] = []

    def run(gate: gates.Gate) -> gates.Result:
        ran.append(gate.label)
        output = (outputs or {}).get(gate.label, "")
        return gates.Result(1 if gate.label == failing else 0, output)

    return run, ran


def test_a_failing_gate_stops_the_run_names_the_gate_and_exits_non_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    failing = gates.GATES[2].label
    run, ran = _runner(failing)

    code = gates.main([], run=run, tree="abc1234 (clean)")

    block = capsys.readouterr().out
    assert code == 1
    assert ran == [gate.label for gate in gates.GATES[:3]]
    assert f"- {failing}: FAILED (exit 1)" in block
    assert f"- {gates.GATES[3].label}: not run" in block
    assert f"Result: FAILED at {failing}" in block


def test_every_gate_passing_exits_zero_and_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    run, ran = _runner()

    code = gates.main([], run=run, tree="abc1234 (clean)")

    block = capsys.readouterr().out
    assert code == 0
    assert len(ran) == len(gates.GATES)
    assert f"Result: {len(gates.GATES)} of {len(gates.GATES)} gates passed" in block
    assert "abc1234 (clean)" in block


def test_the_summary_is_the_same_for_the_same_tree_and_drops_test_timings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pytest_gate = next(g.label for g in gates.GATES if g.project == "root" and g.name == "pytest")
    first = {pytest_gate: "...\n===== 992 passed, 3 skipped in 81.20s (0:01:21) =====\n"}
    second = {pytest_gate: "...\n===== 992 passed, 3 skipped in 64.02s (0:01:04) =====\n"}

    blocks = []
    for outputs in (first, second):
        run, _ = _runner(outputs=outputs)
        gates.main([], run=run, tree="abc1234 (clean)")
        blocks.append(capsys.readouterr().out.split(gates.SUMMARY_START)[1])

    assert blocks[0] == blocks[1]
    assert f"- {pytest_gate}: passed (992 passed, 3 skipped)" in blocks[0]


def test_projects_can_be_chosen_and_an_unknown_one_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    run, ran = _runner()

    assert gates.main(["policy"], run=run, tree="t") == 0
    assert ran and all(label.startswith("policy/") for label in ran)

    assert gates.main(["nope"], run=run, tree="t") == 2
    assert "nope" in capsys.readouterr().err


def test_ci_runs_every_project_through_the_command() -> None:
    workflow = (gates.ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    named = set()
    for line in workflow.splitlines():
        if "-m scripts.gates" in line:
            named.update(line.split("-m scripts.gates", 1)[1].split())

    assert named == set(gates.PROJECTS)


def test_pytest_totals_are_read_from_windows_line_endings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pytest_gate = next(g.label for g in gates.GATES if g.project == "root" and g.name == "pytest")
    run, _ = _runner(outputs={pytest_gate: "...\r\n===== 5 passed in 0.22s =====\r\n"})

    gates.main([], run=run, tree="t")

    assert f"- {pytest_gate}: passed (5 passed)" in capsys.readouterr().out
