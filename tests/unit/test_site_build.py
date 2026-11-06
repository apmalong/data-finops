"""The site generator (spec 07, "The site tool"), tested through `build` and the drift test.

The reference renderers are tested on small inputs in test_site_reference.py."""

import json
from pathlib import Path

import pytest

from scripts.site.build import SiteError, build

REPO = Path(__file__).resolve().parents[2]

NAV = [
    {"title": "Home", "path": "index.html"},
    {
        "title": "How-to guides",
        "path": "how-to/index.html",
        "pages": [{"title": "Calculate", "path": "how-to/calculate.html"}],
    },
]


ALL = ("head", "nav", "onpage", "pager", "footer")
NO_NAV = tuple(r for r in ALL if r != "nav")


def page(name: str, *, regions: tuple[str, ...] = ALL, body: str = "") -> str:
    def region(r: str) -> str:
        return f"<!-- {r}:start -->\nstale\n<!-- {r}:end -->\n" if r in regions else ""

    return (
        f'<!doctype html>\n<html lang="en">\n<head>\n{region("head")}<title>{name}</title>\n'
        f"</head>\n<body>\n{region('nav')}<main>\n<h1>{name}</h1>\n{region('onpage')}{body}"
        f"{region('pager')}</main>\n{region('footer')}</body>\n</html>\n"
    )


def make_repo(root: Path, nav: list[dict[str, object]], pages: dict[str, str]) -> Path:
    site = root / "site"
    site.mkdir(parents=True)
    (site / "nav.json").write_text(json.dumps(nav), encoding="utf-8")
    for name, text in pages.items():
        (site / name).parent.mkdir(parents=True, exist_ok=True)
        (site / name).write_text(text, encoding="utf-8")
    return root


def region(html: str, name: str) -> str:
    start, end = f"<!-- {name}:start -->\n", f"<!-- {name}:end -->"
    return html[html.index(start) + len(start) : html.index(end)]


def test_the_nav_links_relative_to_each_page_and_marks_the_current_one(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        NAV,
        {
            "index.html": page("Home"),
            "how-to/index.html": page("How-to guides"),
            "how-to/calculate.html": page("Calculate"),
        },
    )
    nav = region(build(repo)["how-to/calculate.html"], "nav")
    assert '<a href="../index.html">Home</a>' in nav  # the home entry's title
    assert '<a href="index.html">How-to guides</a>' in nav
    assert '<a href="calculate.html" aria-current="page">Calculate</a>' in nav
    assert nav.count("aria-current") == 1


def test_a_page_missing_a_shared_region_fails_the_build_naming_it(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        NAV,
        {
            "index.html": page("Home"),
            "how-to/index.html": page("How-to guides", regions=("head", "nav", "onpage", "pager")),
            "how-to/calculate.html": page("Calculate"),
        },
    )
    with pytest.raises(SiteError, match=r"how-to/index\.html has no footer region"):
        build(repo)


def test_every_page_links_the_stylesheet_and_gets_a_footer_that_claims_no_more_than_is_true(
    tmp_path: Path,
) -> None:
    repo = make_repo(
        tmp_path,
        NAV,
        {
            "index.html": page("Home"),
            "how-to/index.html": page("How-to guides"),
            "how-to/calculate.html": page("Calculate"),
        },
    )
    built = build(repo)
    footers = {region(text, "footer") for text in built.values()}
    assert len(footers) == 1
    (footer,) = footers
    for path, stylesheet in [
        ("index.html", "assets/site.css"),
        ("how-to/calculate.html", "../assets/site.css"),
    ]:
        head = region(built[path], "head")
        assert '<meta charset="utf-8">' in head and 'name="viewport"' in head
        assert f'<link rel="stylesheet" href="{stylesheet}">' in head
    assert "<code>main</code>" in footer
    assert "Nothing is released yet" in footer
    assert "No license has been chosen yet" in footer


def test_a_section_index_lists_its_pages_from_the_nav(tmp_path: Path) -> None:
    nav = [*NAV, {"title": "Reference", "path": "reference/index.html", "pages": []}]
    section = page("How-to guides").replace(
        "</main>", "<!-- pages:start -->\n<!-- pages:end -->\n</main>"
    )
    empty = page("Reference").replace(
        "</main>", "<!-- pages:start -->\n<!-- pages:end -->\n</main>"
    )
    repo = make_repo(
        tmp_path,
        nav,
        {
            "index.html": page("Home"),
            "how-to/index.html": section,
            "how-to/calculate.html": page("Calculate"),
            "reference/index.html": empty,
        },
    )
    built = build(repo)
    listed = region(built["how-to/index.html"], "pages")
    assert '<li><a href="calculate.html">Calculate</a></li>' in listed
    assert "No pages yet." in region(built["reference/index.html"], "pages")


def test_the_committed_site_is_what_the_build_writes() -> None:
    stale = [
        path
        for path, text in build(REPO).items()
        if (REPO / "site" / path).read_text(encoding="utf-8") != text
    ]
    assert not stale, f"stale pages {stale}: run `python -m scripts.site build`"


@pytest.mark.parametrize(
    ("broken", "error"),
    [
        ("<!-- nav:start -->\nstale\n", r"index\.html.*nav"),
        ("<!-- nav:start -->\n<!-- nav:end -->\n<!-- nav:start -->\n<!-- nav:end -->\n", "nav"),
    ],
    ids=["no end marker", "region twice"],
)
def test_a_malformed_region_fails_the_build(tmp_path: Path, broken: str, error: str) -> None:
    home = page("Home", regions=NO_NAV).replace("<main>", broken + "<main>")
    repo = make_repo(
        tmp_path,
        NAV,
        {
            "index.html": home,
            "how-to/index.html": page("How-to guides"),
            "how-to/calculate.html": page("Calculate"),
        },
    )
    with pytest.raises(SiteError, match=error):
        build(repo)


def test_markers_on_one_line_are_stamped(tmp_path: Path) -> None:
    home = page("Home", regions=NO_NAV).replace(
        "<main>", "<!-- nav:start --><!-- nav:end -->\n<main>"
    )
    repo = make_repo(
        tmp_path,
        NAV,
        {
            "index.html": home,
            "how-to/index.html": page("How-to guides"),
            "how-to/calculate.html": page("Calculate"),
        },
    )
    nav = region(build(repo)["index.html"], "nav")
    assert '<a href="how-to/index.html">How-to guides</a>' in nav


@pytest.mark.parametrize(
    "nav",
    [
        "[{",
        '[{"title": "Home"}]',
        '[{"title": "Home", "path": "index.html"}, {"title": "Again", "path": "index.html"}]',
        '[{"title": "Home", "path": "index.html"}, {"title": "CSS", "path": "assets/site.css"}]',
        '[{"title": "Home", "path": "index.html"}, {"title": "A", "path": "a.html#part"}]',
        '[{"title": "A", "path": "a.html"}, {"title": "Home", "path": "index.html"}]',
        '[{"title": "Home", "path": "index.html", "pages": [{"title": "A", "path": "a.html"}]}]',
    ],
    ids=[
        "not JSON",
        "no path",
        "duplicate",
        "not a page",
        "fragment",
        "home not first",
        "home with pages",
    ],
)
def test_a_malformed_nav_fails_the_build_saying_why(tmp_path: Path, nav: str) -> None:
    repo = make_repo(tmp_path, [], {"index.html": page("Home"), "a.html": page("A")})
    (repo / "site" / "nav.json").write_text(nav, encoding="utf-8")
    with pytest.raises(SiteError, match="nav.json"):
        build(repo)


def test_the_nav_starts_with_a_link_that_skips_to_the_content(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        NAV,
        {
            "index.html": page("Home"),
            "how-to/index.html": page("How-to guides"),
            "how-to/calculate.html": page("Calculate"),
        },
    )
    nav = region(build(repo)["how-to/calculate.html"], "nav")
    assert nav.startswith('<a class="skip-link" href="#main">Skip to content</a>')


def test_a_reference_page_is_stamped_from_its_source_in_the_repository(tmp_path: Path) -> None:
    nav = [*NAV, {"title": "Glossary", "path": "reference/glossary.html"}]
    glossary = page("Glossary").replace(
        "</main>", "<!-- reference:start -->\n<!-- reference:end -->\n</main>"
    )
    repo = make_repo(
        tmp_path,
        nav,
        {
            "index.html": page("Home"),
            "how-to/index.html": page("How-to guides"),
            "how-to/calculate.html": page("Calculate"),
            "reference/glossary.html": glossary,
        },
    )
    (repo / "GLOSSARY.md").write_text(
        "# T\n\n## Billing\n\n**Billing Line**:\nOne FOCUS row.\n", encoding="utf-8"
    )
    stamped = region(build(repo)["reference/glossary.html"], "reference")
    assert '<dt id="billing-line">Billing Line</dt>' in stamped


def test_a_reference_region_on_a_page_with_no_source_fails_the_build(tmp_path: Path) -> None:
    stray = page("Calculate").replace(
        "</main>", "<!-- reference:start -->\n<!-- reference:end -->\n</main>"
    )
    repo = make_repo(
        tmp_path,
        NAV,
        {
            "index.html": page("Home"),
            "how-to/index.html": page("How-to guides"),
            "how-to/calculate.html": stray,
        },
    )
    with pytest.raises(SiteError, match="how-to/calculate.html.*reference"):
        build(repo)


def test_a_reference_page_without_its_region_fails_the_build(tmp_path: Path) -> None:
    nav = [*NAV, {"title": "Glossary", "path": "reference/glossary.html"}]
    repo = make_repo(
        tmp_path,
        nav,
        {
            "index.html": page("Home"),
            "how-to/index.html": page("How-to guides"),
            "how-to/calculate.html": page("Calculate"),
            "reference/glossary.html": page("Glossary"),
        },
    )
    with pytest.raises(SiteError, match="reference/glossary.html has no reference region"):
        build(repo)
