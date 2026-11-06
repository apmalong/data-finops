"""Every offline how-to guide runs as written (spec 07, "Runnable pages")."""

from pathlib import Path

import pytest

from scripts.site.runner import run_page

REPO = Path(__file__).resolve().parents[2]
HOW_TOS = [
    "ingest-focus-export",
    "ingest-athena-history",
    "ingest-dbt-artifacts",
    "receive-openlineage",
    "calculate",
    "handle-restated-export",
    "read-cost-explanation",
    "serve-with-token",
]


@pytest.mark.parametrize("name", HOW_TOS)
def test_the_how_to_runs_as_written(name: str) -> None:
    page = REPO / "site" / "how-to" / f"{name}.html"
    assert '<code class="run">' in page.read_text(encoding="utf-8")
    assert run_page(page, REPO) == []
