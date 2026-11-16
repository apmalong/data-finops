"""The readable template's stamping (#171, spec 07 "Layout"), tested through `build`."""

import json
from pathlib import Path

import pytest

from scripts.site.build import SiteError, build
from tests.unit.site_pages import NAV, make_repo, page, region

NAV3 = [
    *NAV,
    {
        "title": "Reference",
        "path": "reference/index.html",
        "pages": [{"title": "Glossary", "path": "reference/glossary.html"}],
    },
]
GLOSSARY_PAGE = page("Glossary").replace(
    "</main>", "<!-- reference:start -->\n<!-- reference:end -->\n</main>"
)


def three_sections(tmp_path: Path, calculate: str = "") -> dict[str, str]:
    repo = make_repo(
        tmp_path,
        NAV3,
        {
            "index.html": page("Home"),
            "how-to/index.html": page("How-to guides"),
            "how-to/calculate.html": calculate or page("Calculate"),
            "reference/index.html": page("Reference"),
            "reference/glossary.html": GLOSSARY_PAGE,
        },
    )
    (repo / "GLOSSARY.md").write_text(
        "# T\n\n## Billing\n\n**Billing Line**:\nOne FOCUS row.\n", encoding="utf-8"
    )
    return build(repo)


def test_the_sidebar_lists_every_section_with_only_the_current_one_open(
    tmp_path: Path,
) -> None:
    built = three_sections(tmp_path)
    in_how_to = region(built["how-to/calculate.html"], "nav")
    start = in_how_to.index('<nav class="site-nav"')
    sidebar = in_how_to[start : in_how_to.index("</nav>", start)]
    assert '<a href="calculate.html" aria-current="page">Calculate</a>' in sidebar
    assert '<a href="../reference/glossary.html">Glossary</a>' in sidebar  # closed, but listed
    assert sidebar.count("<details open>") == 1
    assert sidebar.index("<details open>") < sidebar.index("calculate.html")
    assert sidebar.index("calculate.html") < sidebar.index("<details>")  # Reference: closed
    assert "<summary>How-to guides</summary>" in sidebar
    assert sidebar.count("aria-current") == 1
    home = region(built["index.html"], "nav")
    assert "<details open>" not in home and "calculate.html" in home


def test_breadcrumbs_lead_from_home_to_the_page(tmp_path: Path) -> None:
    built = three_sections(tmp_path)
    assert (
        '<nav class="breadcrumbs" aria-label="Breadcrumb">\n<ol>\n'
        '<li><a href="../index.html">Home</a></li>\n'
        '<li><a href="index.html">How-to guides</a></li>\n'
        '<li aria-current="page">Calculate</li>\n</ol>\n</nav>'
    ) in region(built["how-to/calculate.html"], "nav")
    assert (
        '<li><a href="../index.html">Home</a></li>\n<li aria-current="page">Reference</li>'
    ) in region(built["reference/index.html"], "nav")
    assert "breadcrumbs" not in region(built["index.html"], "nav")


def test_the_top_bar_holds_the_site_name_the_search_and_the_source(tmp_path: Path) -> None:
    nav = region(three_sections(tmp_path)["reference/glossary.html"], "nav")
    header = nav[nav.index('<header class="site-header">') : nav.index("</header>")]
    assert '<a class="site-name" href="../index.html">Home</a>' in header
    assert '<form class="site-search" role="search" hidden' in header  # site.js shows it
    assert 'data-index="../assets/search-index.json"' in header
    assert 'data-root="../"' in header
    assert '<a href="https://github.com/apmalong/data-finops">GitHub</a>' in header


def test_the_search_index_holds_each_pages_own_text(tmp_path: Path) -> None:
    body = (
        '<h2 id="run">Run <code>it</code></h2>\n'
        '<p>Splits a Billing &amp; <a href="#x">Line</a>.</p>\n'
        '<pre class="output checked">{"calculation_run_id": "calc-1"}</pre>\n'
    )
    built = three_sections(tmp_path, page("Calculate", body=body))
    index = json.loads(built["assets/search-index.json"])
    entry = next(e for e in index if e["path"] == "how-to/calculate.html")
    assert entry["title"] == "Calculate"
    assert entry["section"] == "How-to guides"
    assert entry["headings"] == ["Run it"]
    assert "Splits a Billing & Line." in entry["text"]  # an inline tag adds no space
    assert "calculation_run_id" not in entry["text"]  # a command's output is not searched
    for stamped in ("Skip to content", "On this page", "Previous:", "Nothing is released"):
        assert stamped not in entry["text"]  # only the page's own words
    assert [e["path"] for e in index] == [
        "index.html",
        "how-to/index.html",
        "how-to/calculate.html",
        "reference/index.html",
        "reference/glossary.html",
    ]


def test_on_this_page_lists_the_pages_h2s_by_their_ids(tmp_path: Path) -> None:
    body = (
        '<h2 id="load">1. Load <code>it</code></h2>\n<p>x</p>\n'
        '<h2 id="run">2. <a href="#x">Run</a></h2>\n'
    )
    built = three_sections(tmp_path, page("Calculate", body=body))
    assert region(built["how-to/calculate.html"], "onpage") == (
        '<nav class="on-page" aria-label="On this page">\n<p>On this page</p>\n<ul>\n'
        '<li><a href="#load">1. Load <code>it</code></a></li>\n'
        '<li><a href="#run">2. Run</a></li>\n</ul>\n</nav>\n'
    )
    assert region(built["how-to/index.html"], "onpage") == "\n"  # no h2s, no list


def test_on_this_page_includes_a_generated_pages_h2s(tmp_path: Path) -> None:
    listed = region(three_sections(tmp_path)["reference/glossary.html"], "onpage")
    assert '<li><a href="#area-billing">Billing</a></li>' in listed


def test_an_h2_without_an_id_fails_the_build(tmp_path: Path) -> None:
    with pytest.raises(SiteError, match=r"how-to/calculate\.html has an h2 without an id"):
        three_sections(tmp_path, page("Calculate", body="<h2>Run</h2>\n"))


def test_each_page_links_the_pages_before_and_after_it_in_nav_order(tmp_path: Path) -> None:
    built = three_sections(tmp_path)
    assert region(built["how-to/calculate.html"], "pager") == (
        '<nav class="pager" aria-label="Previous and next">\n'
        '<a rel="prev" href="index.html">Previous: How-to guides</a>\n'
        '<a rel="next" href="../reference/index.html">Next: Reference</a>\n</nav>\n'
    )
    assert 'rel="prev"' not in region(built["index.html"], "pager")
    assert 'rel="next"' not in region(built["reference/glossary.html"], "pager")


def test_each_line_of_a_plain_code_block_is_its_own_span(tmp_path: Path) -> None:
    body = (
        '<pre><code class="run">data-finops ingest\ndata-finops calculate</code></pre>\n'
        '<pre class="output checked">focus: new\ncalculate: new</pre>\n'
        "<pre><code>a plain block\nis spanned too</code></pre>\n"
        "<pre><code>kept <em>as</em>\nwritten</code></pre>\n"
    )
    built = three_sections(tmp_path, page("Calculate", body=body))["how-to/calculate.html"]
    assert (
        '<pre class="shell-posix"><code class="run"><span class="line">data-finops ingest\n</span>'
        '<span class="line">data-finops calculate</span></code></pre>'
    ) in built
    assert (
        '<pre class="output checked"><span class="line">focus: new\n</span>'
        '<span class="line">calculate: new</span></pre>'
    ) in built
    assert (
        '<pre class="shell-posix"><code><span class="line">a plain block\n</span>'
        '<span class="line">is spanned too</span></code></pre>'
    ) in built
    assert "<pre><code>kept <em>as</em>\nwritten</code></pre>" in built  # other markup: left


def test_a_line_added_to_a_built_block_gets_its_own_span(tmp_path: Path) -> None:
    edited = (
        '<pre class="shell-posix"><code class="run"><span class="line">one\n</span>'
        '<span class="line">two</span>\nthree</code></pre>\n'
    )
    built = three_sections(tmp_path, page("Calculate", body=edited))["how-to/calculate.html"]
    assert (
        '<pre class="shell-posix"><code class="run"><span class="line">one\n</span>'
        '<span class="line">two\n</span><span class="line">three</span></code></pre>'
    ) in built


def test_building_a_built_page_changes_nothing(tmp_path: Path) -> None:
    body = '<h2 id="a">A</h2>\n<pre><code class="run">one\ntwo</code></pre>\n'
    first = three_sections(tmp_path / "1", page("Calculate", body=body))["how-to/calculate.html"]
    again = three_sections(tmp_path / "2", first)["how-to/calculate.html"]
    assert again == first
