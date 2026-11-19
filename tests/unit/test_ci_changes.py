"""Which CI checks a change needs (#321): the changed paths in, the checks to run out."""

import pytest

from scripts.ci_changes import Checks, classify

EVERYTHING = Checks(docs_only=False, infra=True)
DOCS_ONLY = Checks(docs_only=True, infra=False)
ROOT_ONLY = Checks(docs_only=False, infra=False)


@pytest.mark.parametrize(
    ("paths", "checks"),
    [
        (["docs/adr/0003-authoritative-billing-ledger.md"], DOCS_ONLY),
        (["GLOSSARY.md", "README.md", "docs/specs/08-aws-deployment.md"], DOCS_ONLY),
        ([".claude/skills/simplified-technical-english/SKILL.md"], DOCS_ONLY),
        (["site/how-to/send-to-inbox.html"], ROOT_ONLY),  # the page runner runs its commands
        (["docs/specs/05-security-operations.md", "tests/unit/test_gates.py"], ROOT_ONLY),
        (["scripts/ste_check.py", "fixtures/focus/export-2026-10.json"], ROOT_ONLY),
        (["schemas/openapi.json", "examples/demo/README.md"], ROOT_ONLY),
        (["src/data_finops/ledger/ledger.py"], EVERYTHING),  # the Lambda package
        (["infra/pulumi/aws/deployment/compute.py"], EVERYTHING),
        (["uv.lock"], EVERYTHING),
        (["pyproject.toml"], EVERYTHING),
        (["scripts/gates.py"], EVERYTHING),
        ([".github/workflows/ci.yml"], EVERYTHING),
        (["docs/guide.md", "something/new.txt"], EVERYTHING),  # an unknown path
        (["docs/notes.txt"], EVERYTHING),  # not a Markdown document
        (["infra/README.md"], EVERYTHING),  # Markdown, but not at the root
    ],
)
def test_a_change_runs_the_checks_its_paths_need(paths: list[str], checks: Checks) -> None:
    assert classify(paths) == checks


@pytest.mark.parametrize("paths", [None, []], ids=["no base: a push to main", "no paths"])
def test_without_a_diff_every_check_runs(paths: list[str] | None) -> None:
    assert classify(paths) == EVERYTHING
