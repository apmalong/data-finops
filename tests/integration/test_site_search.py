"""The docs site's search (#274), run in Node: the same `site.js` the browser runs."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

SITE_JS = Path(__file__).resolve().parents[2] / "site" / "assets" / "site.js"
INDEX = [
    {
        "path": "how-to/calculate.html",
        "title": "Calculate Allocations",
        "section": "How-to guides",
        "headings": ["Run the calculation"],
        "text": "A Calculation Run splits every Billing Line into Allocations. See the Glossary.",
    },
    {
        "path": "reference/glossary.html",
        "title": "Glossary",
        "section": "Reference",
        "headings": ["Billing"],
        "text": "Allocation: a part of a Billing Line given to a target (or Unallocated).",
    },
    {
        "path": "explanation/public-demo.html",
        "title": "The Public Demo",
        "section": "Explanation",
        "headings": ["What is public"],
        "text": "Only the demo workgroup's lines are published.",
    },
]


def _search(query: str) -> list[dict[str, Any]]:
    node = shutil.which("node")
    assert node, "the site's search tests need Node, as the browser runs the same script"
    script = (
        "const s = require(process.argv[1]);"
        "process.stdout.write(JSON.stringify(s.search(JSON.parse(process.argv[2]),"
        " process.argv[3])));"
    )
    ran = subprocess.run(
        [node, "-e", script, str(SITE_JS), json.dumps(INDEX), query],
        capture_output=True,
        text=True,
        check=True,
    )
    found: list[dict[str, Any]] = json.loads(ran.stdout)
    return found


def test_a_query_finds_the_pages_that_hold_every_word() -> None:
    assert [r["path"] for r in _search("billing line")] == [
        "reference/glossary.html",  # "Billing" is one of its headings
        "how-to/calculate.html",
    ]
    assert _search("workgroup published")[0]["path"] == "explanation/public-demo.html"
    assert _search("kubernetes") == []
    assert _search("   ") == []


def test_a_title_match_ranks_before_a_heading_and_a_heading_before_text() -> None:
    assert [r["path"] for r in _search("allocation")] == [
        "how-to/calculate.html",  # in the title
        "reference/glossary.html",  # only in the text
    ]
    assert [r["path"] for r in _search("billing")][0] == "reference/glossary.html"  # a heading
    # A title match ranks first, even over a page that comes before it with a text match.
    assert [r["path"] for r in _search("glossary")] == [
        "reference/glossary.html",
        "how-to/calculate.html",
    ]


def test_a_result_has_its_title_section_and_a_snippet_around_the_match() -> None:
    (result,) = _search("workgroup")
    assert result == {
        "path": "explanation/public-demo.html",
        "title": "The Public Demo",
        "section": "Explanation",
        "snippet": "Only the demo workgroup's lines are published.",
    }


def test_special_characters_match_as_text() -> None:
    assert [r["path"] for r in _search("(or")] == ["reference/glossary.html"]
    assert _search(".*") == []
    assert _search("[") == []
