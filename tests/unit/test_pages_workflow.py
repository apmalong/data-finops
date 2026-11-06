"""The Pages deploy workflow is least-privilege and reproducible (spec 07, "Build and deploy").

The workflow is checked as text: it is short, and the repository has no YAML library of its own.
"""

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "pages.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _block(text: str, key: str) -> list[str]:
    """The indented lines under a top-level `key:`."""
    lines = text.split("\n")
    start = lines.index(f"{key}:") + 1
    block = []
    for line in lines[start:]:
        if line and not line.startswith(" "):
            break
        if line.strip() and not line.strip().startswith("#"):
            block.append(line.strip())
    return block


def test_it_runs_only_on_pushes_to_main_and_by_hand() -> None:
    assert _block(_text(), "on") == ["push:", "branches: [main]", "workflow_dispatch:"]


def test_it_may_only_read_the_code_and_publish_pages() -> None:
    assert _block(_text(), "permissions") == [
        "contents: read",
        "pages: write",
        "id-token: write",
    ]
    assert "permissions:" not in _text().split("jobs:", 1)[1]  # no job widens them


def test_every_action_is_pinned_to_a_commit_with_its_version_named() -> None:
    uses = re.findall(r"uses: (\S+)(.*)", _text())
    assert {u.split("@")[0] for u, _ in uses} == {
        "actions/checkout",
        "actions/configure-pages",
        "actions/upload-pages-artifact",
        "actions/deploy-pages",
    }
    for action, comment in uses:
        assert re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}", action), action
        assert re.fullmatch(r" # v\d+\.\d+\.\d+", comment), (action, comment)


def test_it_checks_the_site_then_publishes_site_as_committed() -> None:
    text = _text()
    check = text.index("python3 -m scripts.site check")
    upload = text.index("actions/upload-pages-artifact@")
    assert check < upload
    assert re.search(r"upload-pages-artifact@\S+.*\n\s+with:\n\s+path: site\n", text)
