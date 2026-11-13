"""Run every quality gate, stop at the first failure, and print a summary block (#241).

    python -m scripts.gates                   # every project
    python -m scripts.gates policy stack      # only these projects

Each project's gates run with that project's own `.venv` (create it with `uv sync`). The
command streams each gate's output, then prints a block that starts with `## Gates`. A PR's
Evidence section quotes that block, or links the CI run, in place of hand-written results.
The block has no timings, so the same tree gives the same block. CI runs this command too, so
CI and a local run check the same things. `pip-audit` and the secret scan use the network, so
they stay as separate CI steps.

Two checks run inside a pytest gate, not as gates of their own. `root/pytest` compares the
committed site with what `python -m scripts.site build` writes. `stack/pytest` runs `bash -n` on
the wizards, and skips that test when Git Bash or bash is not available.

Stdlib only, so that it runs before any project is installed.
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404 - runs fixed, in-repo tool commands; no untrusted input
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Project:
    directory: Path
    setup: str  # the command that makes the project's environment


PROJECTS: dict[str, Project] = {
    "root": Project(ROOT, "uv sync --extra dev --extra api --extra aws --frozen"),
    "reference-workload": Project(ROOT / "examples" / "reference-workload", "uv sync --frozen"),
    "policy": Project(ROOT / "infra" / "pulumi" / "aws" / "policy", "uv sync --frozen"),
    "stack": Project(ROOT / "infra" / "pulumi" / "aws", "uv sync --frozen"),
}

SUMMARY_START = "## Gates"


@dataclass(frozen=True)
class Gate:
    project: str
    name: str
    args: tuple[str, ...]  # run as `<project python> <args>`

    @property
    def label(self) -> str:
        return f"{self.project}/{self.name}"


@dataclass(frozen=True)
class Result:
    returncode: int
    output: str


def _gate(project: str, name: str, *args: str) -> Gate:
    return Gate(project, name, args)


GATES: tuple[Gate, ...] = (
    _gate("root", "ruff check", "-m", "ruff", "check", "--no-cache", "."),
    _gate("root", "ruff format", "-m", "ruff", "format", "--no-cache", "--check", "."),
    _gate("root", "mypy", "-m", "mypy", "src", "scripts/site", "scripts/gates.py"),
    _gate("root", "pytest", "-m", "pytest", "--cov=data_finops", "--cov-report=term-missing"),
    _gate("root", "bandit", "-m", "bandit", "-q", "-r", "src", "scripts/site", "scripts/gates.py"),
    _gate("root", "site check", "-S", "-m", "scripts.site", "check"),
    _gate("reference-workload", "mypy", "-m", "mypy"),
    _gate("reference-workload", "bandit", "-m", "bandit", "-q", "-r", "infra.py", "__main__.py"),
    _gate("reference-workload", "pytest", "-m", "pytest"),
    _gate("policy", "mypy", "-m", "mypy"),
    _gate("policy", "bandit", "-m", "bandit", "-q", "-r", "policies.py", "__main__.py"),
    _gate("policy", "pytest", "-m", "pytest"),
    _gate("stack", "mypy", "-m", "mypy"),
    _gate("stack", "bandit", "-m", "bandit", "-q", "-r", "deployment", "__main__.py"),
    _gate("stack", "pytest", "-m", "pytest"),
)

Runner = Callable[[Gate], Result]

# pytest's last line, for example "==== 992 passed, 3 skipped in 81.20s (0:01:21) ====".
_PYTEST_TOTALS = re.compile(r"^=+ (?P<totals>.+?) in [\d.]+s\b.*=+\r?$", re.MULTILINE)


def _python(project: str) -> Path:
    venv = PROJECTS[project].directory / ".venv"
    return venv / "Scripts" / "python.exe" if os.name == "nt" else venv / "bin" / "python"


def run_gate(gate: Gate) -> Result:
    """Run one gate in its project with that project's environment, and stream its output."""
    project = PROJECTS[gate.project]
    python = _python(gate.project)
    if not python.exists():
        message = f"No environment at {python.parent.parent}. Run `{project.setup}` there.\n"
        sys.stdout.write(message)
        return Result(1, message)
    process = subprocess.Popen(  # nosec B603 - the fixed interpreter and GATES above
        [str(python), *gate.args],
        cwd=project.directory,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None  # nosec B101 - set by stdout=PIPE
    kept = bytearray()
    for line in process.stdout:
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
        kept.extend(line)
    return Result(process.wait(), kept.decode("utf-8", errors="replace"))


def tree_state() -> str:
    """The commit and whether the working tree differs from it, for example `1a2b3c4 (clean)`.

    Untracked files count, because a new test file changes what the gates run.
    """
    try:
        head = subprocess.run(  # nosec B603 B607 - fixed git command
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        changed = subprocess.run(  # nosec B603 B607 - fixed git command
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown (git is not available)"
    return f"{head} ({'uncommitted changes' if changed else 'clean'})"


def _pytest_totals(gate: Gate, result: Result) -> str:
    if gate.name != "pytest":
        return ""
    totals = _PYTEST_TOTALS.findall(result.output)
    return f" ({totals[-1]})" if totals else ""


def summary(chosen: Sequence[Gate], results: Sequence[Result], tree: str) -> str:
    lines = [SUMMARY_START, "", f"Tree: {tree}", ""]
    for index, gate in enumerate(chosen):
        if index >= len(results):
            lines.append(f"- {gate.label}: not run")
        elif results[index].returncode:
            lines.append(f"- {gate.label}: FAILED (exit {results[index].returncode})")
        else:
            lines.append(f"- {gate.label}: passed{_pytest_totals(gate, results[index])}")
    failed = next((g for g, r in zip(chosen, results, strict=False) if r.returncode), None)
    lines.append("")
    if failed:
        lines.append(f"Result: FAILED at {failed.label}")
    else:
        lines.append(f"Result: {len(chosen)} of {len(chosen)} gates passed")
    return "\n".join(lines) + "\n"


def main(
    argv: Sequence[str] | None = None,
    run: Runner = run_gate,
    tree: str | None = None,
) -> int:
    projects = list(sys.argv[1:] if argv is None else argv)
    unknown = [p for p in projects if p not in PROJECTS]
    if unknown:
        sys.stderr.write(
            f"Unknown project: {', '.join(unknown)}. Choose from: {', '.join(PROJECTS)}.\n"
        )
        return 2
    chosen = [g for g in GATES if not projects or g.project in projects]
    results: list[Result] = []
    for gate in chosen:
        sys.stdout.write(f"\n>>> {gate.label}\n")
        sys.stdout.flush()
        result = run(gate)
        results.append(result)
        if result.returncode:
            break
    sys.stdout.write("\n" + summary(chosen, results, tree_state() if tree is None else tree))
    return 1 if any(r.returncode for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
