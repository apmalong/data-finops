"""Which CI checks a change needs (#321), from the paths that it changes.

    python3 scripts/ci_changes.py BASE      # prints docs_only=… and infra=… for $GITHUB_OUTPUT

BASE is the commit that the change goes into: a PR's base. The paths are `git diff
--name-only BASE...HEAD`. Without BASE (a push to main), every check runs. A path that this
module does not know also runs every check, so a new kind of file is never left unchecked.

Stdlib only, so that it runs before any project is installed.
"""

from __future__ import annotations

import subprocess  # nosec B404 - runs one fixed git command; no untrusted input
import sys
from collections.abc import Sequence
from dataclasses import dataclass

DOCS = ("docs/", ".claude/")
"""Documents only: the STE check and the site drift check read them (ADRs and GLOSSARY.md feed
the site), and nothing else does. Markdown at the root counts too."""
ROOT_ONLY = ("site/", "tests/", "scripts/", "fixtures/", "schemas/", "examples/")
"""Checked by the root and reference-workload gates, never by the infra job."""
INFRA = ("infra/", "src/", "pyproject.toml", "uv.lock", "scripts/gates.py", ".github/")
"""Read by the infra job: the stack, the policy pack, and the Lambda package built from src."""


@dataclass(frozen=True)
class Checks:
    docs_only: bool
    """Only the docs gates (`scripts.gates --docs`) and the secret scan run in `quality`."""
    infra: bool
    """The infra job runs."""


EVERYTHING = Checks(docs_only=False, infra=True)


def _matches(path: str, prefixes: Sequence[str]) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in prefixes)


def _is_doc(path: str) -> bool:
    at_root = "/" not in path
    return path.endswith(".md") and (at_root or _matches(path, DOCS))


def classify(paths: Sequence[str] | None) -> Checks:
    """The checks that a change of `paths` needs. None or no paths: every check."""
    if not paths:
        return EVERYTHING
    if all(_is_doc(p) for p in paths):
        return Checks(docs_only=True, infra=False)
    if any(_matches(p, INFRA) for p in paths):
        return EVERYTHING
    if all(_is_doc(p) or _matches(p, ROOT_ONLY) for p in paths):
        return Checks(docs_only=False, infra=False)
    return EVERYTHING  # an unknown path


def changed_paths(base: str) -> list[str]:
    diff = subprocess.run(  # nosec B603 B607 - a fixed git command on the checkout
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return diff.stdout.split()


def main(argv: Sequence[str]) -> int:
    base = argv[0] if argv else ""
    checks = classify(changed_paths(base) if base else None)
    print(f"docs_only={str(checks.docs_only).lower()}")
    print(f"infra={str(checks.infra).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
