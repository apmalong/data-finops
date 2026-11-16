"""Stamp the shared regions of every page from one source (spec 07, "Layout").

`site/nav.json` lists every page. Each page marks the regions the build owns with
`<!-- name:start -->` and `<!-- name:end -->`; everything else in a page is hand-written.
"""

import html
import json
import posixpath
import re
from collections.abc import Callable
from pathlib import Path

from scripts.site.nav import Entry, SiteError, parse_nav, walk
from scripts.site.reference import (
    render_cli,
    render_contracts,
    render_deployment_settings,
    render_glossary,
    render_http,
)
from scripts.site.shells import LINE_SPAN, shell_tabs

__all__ = ["SiteError", "build"]

REQUIRED_REGIONS = ("head", "nav", "pager", "footer")  # every page has each exactly once
LATE_REGIONS = ("onpage",)  # required too, but stamped last, from the finished page's h2s
OPTIONAL_REGIONS = ("pages",)  # a section's index page may list its pages
SOURCE = "https://github.com/apmalong/data-finops"

STYLESHEET = "assets/site.css"
SCRIPT = "assets/site.js"  # the shell tabs (#173) and the search (#274); pages work without it
SEARCH_INDEX = "assets/search-index.json"  # written by build from the finished pages (#274)
PUBLIC_DEMO = "explanation/public-demo.html"  # linked from the top bar when the nav lists it
# The footer claims no more than is true: one unversioned site, no release, no license (OQ-8).
FOOTER = (
    "<footer>\n"
    "<p>This site describes the <code>main</code> branch. Nothing is released yet. "
    f'No license has been chosen yet. Source on <a href="{SOURCE}">GitHub</a>.</p>\n'
    "</footer>"
)


def build(repo: Path) -> dict[str, str]:
    """Every page in the nav as it should be committed, keyed by its path in `site/`, and the
    search index (`SEARCH_INDEX`) of those pages.

    A page the nav lists but the site lacks is left to the checker to report.
    """
    site = repo / "site"
    nav = parse_nav((site / "nav.json").read_text(encoding="utf-8"))
    order = list(walk(nav))
    built: dict[str, str] = {}
    for index, entry in enumerate(order):
        source = site / entry.path
        if not source.is_file():
            continue
        content = {
            "head": _head(entry.path),
            "nav": _nav(nav, entry.path),
            "pager": _pager(
                entry.path,
                order[index - 1] if index else None,
                order[index + 1] if index + 1 < len(order) else None,
            ),
            "footer": FOOTER,
            "pages": _pages(entry),
        }
        text = source.read_text(encoding="utf-8")
        for name in (*REQUIRED_REGIONS, *OPTIONAL_REGIONS):
            text = _stamp(text, entry.path, name, content[name], name in REQUIRED_REGIONS)
        text = _line_spans(shell_tabs(entry.path, _stamp_reference(repo, entry.path, text)))
        (late,) = LATE_REGIONS
        built[entry.path] = _stamp(text, entry.path, late, _on_page(entry.path, text), True)
    built[SEARCH_INDEX] = _search_index(nav, built)
    return built


def _stamp_reference(repo: Path, page: str, text: str) -> str:
    """A generated reference page's `reference` region, rendered from its source."""
    has_region = "<!-- reference:start -->" in text
    if page not in GENERATED:
        if has_region:
            raise SiteError(f"{page} has a reference region but no source to render it from")
        return text
    if not has_region:
        raise SiteError(f"{page} has no reference region")
    return _stamp(text, page, "reference", GENERATED[page](repo), True)


def _cli_reference(repo: Path) -> str:
    from data_finops.cli import build_parser  # data_finops only here: `check` stays stdlib-only

    return render_cli(build_parser())


def _http_reference(repo: Path) -> str:
    return render_http(json.loads((repo / "schemas" / "openapi.json").read_text(encoding="utf-8")))


def _contracts_reference(repo: Path) -> str:
    schemas = sorted((repo / "schemas").glob("*.schema.json"))
    return render_contracts({p.name: json.loads(p.read_text(encoding="utf-8")) for p in schemas})


def _glossary_reference(repo: Path) -> str:
    return render_glossary((repo / "GLOSSARY.md").read_text(encoding="utf-8"))


def _deployment_settings_reference(repo: Path) -> str:
    deployment = repo / "infra" / "pulumi" / "aws" / "deployment"
    return render_deployment_settings(
        (deployment / "settings.py").read_text(encoding="utf-8"),
        (deployment / "program.py").read_text(encoding="utf-8"),
    )


# The generated reference pages, each with the renderer of its source (spec 07).
GENERATED: dict[str, Callable[[Path], str]] = {
    "reference/cli.html": _cli_reference,
    "reference/http-api.html": _http_reference,
    "reference/data-contracts.html": _contracts_reference,
    "reference/glossary.html": _glossary_reference,
    "reference/deployment-settings.html": _deployment_settings_reference,
}


def _relative(current: str, target: str) -> str:
    return posixpath.relpath(target, posixpath.dirname(current) or ".")


def _head(current: str) -> str:
    return (
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<link rel="stylesheet" href="{_relative(current, STYLESHEET)}">\n'
        f'<script src="{_relative(current, SCRIPT)}" defer></script>'
    )


def _link(current: str, entry_path: str, title: str) -> str:
    href = _relative(current, entry_path)
    here = ' aria-current="page"' if entry_path == current else ""
    return f'<a href="{html.escape(href)}"{here}>{html.escape(title)}</a>'


def _section_of(nav: tuple[Entry, ...], path: str) -> Entry | None:
    """The top-level entry that is `path` or lists it; None for the home page."""
    _, *sections = nav
    return next(
        (top for top in sections if top.path == path or any(p.path == path for p in top.pages)),
        None,
    )


def _root_prefix(current: str, home: str) -> str:
    """The way from `current` back to the site root, such as "../" or "./"."""
    return _relative(current, home)[: -len(home)] or "./"


def _nav(nav: tuple[Entry, ...], current: str) -> str:
    """The top bar, the sidebar and the breadcrumbs (#274): every section in the sidebar, each
    in a `<details>` that is open only for the current section. No script is needed."""
    home, *sections = nav
    here = _section_of(nav, current)
    items = []
    for section in sections:
        if not section.pages:
            items.append(f"<li>{_link(current, section.path, section.title)}</li>")
            continue
        pages = [_link(current, section.path, "Overview")]
        pages += [_link(current, p.path, p.title) for p in section.pages]
        listed = "\n".join(f"<li>{link}</li>" for link in pages)
        opened = "<details open>" if section is here else "<details>"
        items.append(
            f"<li>{opened}<summary>{html.escape(section.title)}</summary>\n"
            f"<ul>\n{listed}\n</ul>\n</details></li>"
        )
    sidebar = "\n".join(items)
    return (
        '<a class="skip-link" href="#main">Skip to content</a>\n'
        + _top_bar(nav, current)
        + '\n<nav class="site-nav" id="site-nav" aria-label="Site">\n'
        + f"<ul>\n{sidebar}\n</ul>\n</nav>"
        + _breadcrumbs(home, here, next(e for e in walk(nav) if e.path == current), current)
    )


def _top_bar(nav: tuple[Entry, ...], current: str) -> str:
    """The site name, the search box (hidden until site.js shows it) and the outside links."""
    home = nav[0]
    root = _root_prefix(current, home.path)
    links = [f'<a href="{SOURCE}">GitHub</a>']
    if any(e.path == PUBLIC_DEMO for e in walk(nav)):
        links.insert(0, _link(current, PUBLIC_DEMO, "Public Demo"))
    search = (
        f'<form class="site-search" role="search" hidden '
        f'data-index="{html.escape(_relative(current, SEARCH_INDEX))}" '
        f'data-root="{html.escape(root)}">\n'
        '<label for="site-search-query">Search the docs</label>\n'
        '<input id="site-search-query" type="search" autocomplete="off" '
        'placeholder="Search the docs">\n'
        '<p class="search-status" role="status"></p>\n'
        '<ul class="search-results"></ul>\n</form>'
    )
    name = html.escape(_relative(current, home.path))
    return (
        '<header class="site-header">\n'
        f'<a class="site-name" href="{name}">{html.escape(home.title)}</a>\n'
        f"{search}\n"
        '<a class="menu-link" href="#site-nav">Menu</a>\n'
        f'<nav class="site-links" aria-label="Elsewhere">{" ".join(links)}</nav>\n'
        "</header>"
    )


def _breadcrumbs(home: Entry, section: Entry | None, page: Entry, current: str) -> str:
    """Home, then the section, then the page; nothing on the home page."""
    if section is None:
        return ""
    trail = [home] + ([section] if section is not page else [])
    crumbs = [f"<li>{_link(current, e.path, e.title)}</li>" for e in trail]
    crumbs.append(f'<li aria-current="page">{html.escape(page.title)}</li>')
    items = "\n".join(crumbs)
    return f'\n<nav class="breadcrumbs" aria-label="Breadcrumb">\n<ol>\n{items}\n</ol>\n</nav>'


def _pager(current: str, before: Entry | None, after: Entry | None) -> str:
    """Links to the pages before and after `current`, in nav order."""
    links = [
        f'<a rel="{rel}" href="{html.escape(_relative(current, entry.path))}">'
        f"{label}: {html.escape(entry.title)}</a>"
        for rel, label, entry in (("prev", "Previous", before), ("next", "Next", after))
        if entry is not None
    ]
    return '<nav class="pager" aria-label="Previous and next">\n' + "\n".join(links) + "\n</nav>"


_PRE = re.compile(r"(<pre\b[^>]*>(?:<code\b[^>]*>)?)(.*?)((?:</code>)?</pre>)", re.DOTALL)
_H2 = re.compile(r"<h2\b([^>]*)>(.*?)</h2>", re.DOTALL)
_LINK_TAG = re.compile(r"</?a\b[^>]*>")


def _line_spans(text: str) -> str:
    """Each line of every plain-text code block in its own `<span class="line">`, so a long
    line wraps with a hanging indent. The block's text, which the page runner reads, is
    unchanged. A block holding any other markup is left as written."""

    def spans(block: re.Match[str]) -> str:
        plain = LINE_SPAN.sub(r"\1", block[2])
        if "<" in plain:
            return block[0]
        lines = plain.split("\n")
        inner = "".join(f'<span class="line">{line}\n</span>' for line in lines[:-1])
        return f'{block[1]}{inner}<span class="line">{lines[-1]}</span>{block[3]}'

    return _PRE.sub(spans, text)


def _on_page(page: str, text: str) -> str:
    """The "On this page" list of the page's h2s, or nothing when it has none."""
    items = []
    for heading in _H2.finditer(text):
        anchor = re.search(r'\bid="([^"]+)"', heading[1])
        if anchor is None:
            raise SiteError(f"{page} has an h2 without an id: {heading[0][:60]}")
        label = _LINK_TAG.sub("", heading[2]).strip()  # a link inside a link is not allowed
        items.append(f'<li><a href="#{anchor[1]}">{label}</a></li>')
    if not items:
        return ""
    return (
        '<nav class="on-page" aria-label="On this page">\n<p>On this page</p>\n<ul>\n'
        + "\n".join(items)
        + "\n</ul>\n</nav>"
    )


_MAIN = re.compile(r"<main\b[^>]*>(.*)</main>", re.DOTALL)
_H1 = re.compile(r"<h1\b[^>]*>.*?</h1>", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_INLINE_TAG = re.compile(r"</?(?:a|code|em|strong|span|abbr|kbd|b|i)\b[^>]*>")
# Not searched: a command's output, and the Windows version that build adds beside a command.
_OUTPUT = re.compile(r'<pre\b[^>]*class="(?:output|shell-windows)[^"]*"[^>]*>.*?</pre>', re.DOTALL)
_STAMPED_IN_MAIN = ("onpage", "pager", "pages")


def _search_index(nav: tuple[Entry, ...], built: dict[str, str]) -> str:
    """For each built page, in nav order: its path, title, section, h2s and own text (#274).
    The text leaves out what build stamps, so a search finds a page by its own words."""
    entries = []
    for entry in walk(nav):
        page = built.get(entry.path)
        if page is None:
            continue
        found = _MAIN.search(page)
        main = found[1] if found else ""
        for name in _STAMPED_IN_MAIN:
            main = re.sub(
                f"<!-- {name}:start -->.*?<!-- {name}:end -->", " ", main, flags=re.DOTALL
            )
        section = _section_of(nav, entry.path)
        entries.append(
            {
                "path": entry.path,
                "title": entry.title,
                "section": section.title if section is not None else "",
                "headings": [_plain(h[2]) for h in _H2.finditer(main)],
                "text": _plain(_OUTPUT.sub(" ", _H1.sub(" ", main))),
            }
        )
    return json.dumps(entries, ensure_ascii=False, indent=0) + "\n"


def _plain(fragment: str) -> str:
    """The text of an HTML fragment: no tags, entities decoded, white space collapsed. An
    inline tag joins its neighbours, so "<a>Line</a>," stays "Line,"."""
    return " ".join(html.unescape(_TAG.sub(" ", _INLINE_TAG.sub("", fragment))).split())


def _pages(section: Entry) -> str:
    """The list of a section's pages, for its index page's `pages` region."""
    if not section.pages:
        return "<p>No pages yet.</p>"
    items = "\n".join(f"<li>{_link(section.path, p.path, p.title)}</li>" for p in section.pages)
    return f"<ul>\n{items}\n</ul>"


def _stamp(text: str, page: str, name: str, content: str, required: bool) -> str:
    """`text` with its one `name` region holding `content`. Raises SiteError if malformed."""
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    starts, ends = text.count(start), text.count(end)
    if starts == ends == 0 and not required:
        return text
    if starts == ends == 0:
        raise SiteError(f"{page} has no {name} region")
    if not starts == ends == 1 or text.index(start) > text.index(end):
        raise SiteError(f"{page} has a malformed {name} region: one {start} then one {end}")
    pattern = re.compile(re.escape(start) + ".*?" + re.escape(end), re.DOTALL)
    return pattern.sub(lambda _: f"{start}\n{content}\n{end}", text)
