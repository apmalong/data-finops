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
from scripts.site.reference import render_cli, render_contracts, render_glossary, render_http

__all__ = ["SiteError", "build"]

REQUIRED_REGIONS = ("head", "nav", "pager", "footer")  # every page has each exactly once
LATE_REGIONS = ("onpage",)  # required too, but stamped last, from the finished page's h2s
OPTIONAL_REGIONS = ("pages",)  # a section's index page may list its pages
SOURCE = "https://github.com/apmalong/data-finops"

STYLESHEET = "assets/site.css"
# The footer claims no more than is true: one unversioned site, no release, no license (OQ-8).
FOOTER = (
    "<footer>\n"
    "<p>This site describes the <code>main</code> branch. Nothing is released yet. "
    f'No license has been chosen yet. Source on <a href="{SOURCE}">GitHub</a>.</p>\n'
    "</footer>"
)


def build(repo: Path) -> dict[str, str]:
    """Every page in the nav as it should be committed, keyed by its path in `site/`.

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
        text = _line_spans(_stamp_reference(repo, entry.path, text))
        (late,) = LATE_REGIONS
        built[entry.path] = _stamp(text, entry.path, late, _on_page(entry.path, text), True)
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


# The generated reference pages, each with the renderer of its source (spec 07).
GENERATED: dict[str, Callable[[Path], str]] = {
    "reference/cli.html": _cli_reference,
    "reference/http-api.html": _http_reference,
    "reference/data-contracts.html": _contracts_reference,
    "reference/glossary.html": _glossary_reference,
}


def _relative(current: str, target: str) -> str:
    return posixpath.relpath(target, posixpath.dirname(current) or ".")


def _head(current: str) -> str:
    return (
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<link rel="stylesheet" href="{_relative(current, STYLESHEET)}">'
    )


def _link(current: str, entry_path: str, title: str) -> str:
    href = _relative(current, entry_path)
    here = ' aria-current="page"' if entry_path == current else ""
    return f'<a href="{html.escape(href)}"{here}>{html.escape(title)}</a>'


def _nav(nav: tuple[Entry, ...], current: str) -> str:
    """The site nav: every section, with only the current section's pages listed."""
    home, *sections = nav
    lines = []
    for section in sections:
        inside = current == section.path or any(p.path == current for p in section.pages)
        pages = "\n".join(f"<li>{_link(current, p.path, p.title)}</li>" for p in section.pages)
        children = f"\n<ul>\n{pages}\n</ul>\n" if inside and section.pages else ""
        lines.append(f"<li>{_link(current, section.path, section.title)}{children}</li>")
    items = "\n".join(lines)
    return (
        '<a class="skip-link" href="#main">Skip to content</a>\n'
        f'<header class="site-header">{_link(current, home.path, home.title)}</header>\n'
        f'<nav class="site-nav" aria-label="Site">\n<ul>\n{items}\n</ul>\n</nav>'
    )


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
_LINE_SPAN = re.compile(r'<span class="line">(.*?)</span>', re.DOTALL)
_H2 = re.compile(r"<h2\b([^>]*)>(.*?)</h2>", re.DOTALL)
_LINK_TAG = re.compile(r"</?a\b[^>]*>")


def _line_spans(text: str) -> str:
    """Each line of every plain-text code block in its own `<span class="line">`, so a long
    line wraps with a hanging indent. The block's text, which the page runner reads, is
    unchanged. A block holding any other markup is left as written."""

    def spans(block: re.Match[str]) -> str:
        plain = _LINE_SPAN.sub(r"\1", block[2])
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
