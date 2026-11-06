"""Stamp the shared regions of every page from one source (spec 07, "Layout").

`site/nav.json` lists every page. Each page marks the regions the build owns with
`<!-- name:start -->` and `<!-- name:end -->`; everything else in a page is hand-written.
"""

import html
import posixpath
import re
from pathlib import Path

from scripts.site.nav import Entry, SiteError, parse_nav, walk

__all__ = ["SiteError", "build"]

REQUIRED_REGIONS = ("head", "nav", "footer")  # every page has each exactly once
OPTIONAL_REGIONS = ("pages",)  # a section's index page may list its pages
SOURCE = "https://github.com/apmalong/data-finops"

HEAD = (
    '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">'
)
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
    built: dict[str, str] = {}
    for entry in walk(nav):
        source = site / entry.path
        if not source.is_file():
            continue
        content = {
            "head": HEAD,
            "nav": _nav(nav, entry.path),
            "footer": FOOTER,
            "pages": _pages(entry),
        }
        text = source.read_text(encoding="utf-8")
        for name in (*REQUIRED_REGIONS, *OPTIONAL_REGIONS):
            text = _stamp(text, entry.path, name, content[name], name in REQUIRED_REGIONS)
        built[entry.path] = text
    return built


def _link(current: str, entry_path: str, title: str) -> str:
    href = posixpath.relpath(entry_path, posixpath.dirname(current) or ".")
    here = ' aria-current="page"' if entry_path == current else ""
    return f'<a href="{html.escape(href)}"{here}>{html.escape(title)}</a>'


def _nav(nav: tuple[Entry, ...], current: str) -> str:
    home, *sections = nav

    def items(entries: tuple[Entry, ...]) -> str:
        lines = []
        for e in entries:
            children = f"\n<ul>\n{items(e.pages)}\n</ul>\n" if e.pages else ""
            lines.append(f"<li>{_link(current, e.path, e.title)}{children}</li>")
        return "\n".join(lines)

    return (
        f'<header class="site-header">{_link(current, home.path, home.title)}</header>\n'
        f'<nav aria-label="Site">\n<ul>\n{items(tuple(sections))}\n</ul>\n</nav>'
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
