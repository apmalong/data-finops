"""Stamp the shared regions of every page from one source (spec 07, "Layout").

`site/nav.json` lists every page. Each page marks the regions the build owns with
`<!-- name:start -->` and `<!-- name:end -->` lines; everything else in a page is hand-written.
"""

import html
import json
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HOME = "index.html"
SHARED = ("head", "nav", "footer")  # the regions every page must have
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


class SiteError(Exception):
    """The site cannot be built as it stands."""


@dataclass(frozen=True)
class Entry:
    title: str
    path: str  # relative to the site root
    pages: tuple["Entry", ...] = ()


def build(repo: Path) -> dict[str, str]:
    """Every page in the nav as it should be committed, keyed by its path in `site/`."""
    site = repo / "site"
    nav = parse_nav((site / "nav.json").read_text(encoding="utf-8"))
    built: dict[str, str] = {}
    for entry in walk(nav):
        source = site / entry.path
        if source.is_file():
            text = source.read_text(encoding="utf-8")
            missing = [r for r in SHARED if f"<!-- {r}:start -->" not in text]
            if missing:
                raise SiteError(f"{entry.path} has no {', '.join(missing)} region")
            text = _stamp(text, "head", HEAD)
            text = _stamp(text, "nav", _nav(nav, entry.path))
            text = _stamp(text, "pages", _pages(entry))
            built[entry.path] = _stamp(text, "footer", FOOTER)
    return built


def parse_nav(text: str) -> tuple[Entry, ...]:
    """The entries of `site/nav.json`, in order."""

    def entry(raw: dict[str, Any]) -> Entry:
        return Entry(raw["title"], raw["path"], tuple(entry(p) for p in raw.get("pages", [])))

    return tuple(entry(raw) for raw in json.loads(text))


def walk(entries: tuple[Entry, ...]) -> list[Entry]:
    """Every entry, each section followed by its pages."""
    return [e for top in entries for e in (top, *walk(top.pages))]


def _link(current: str, entry_path: str, title: str) -> str:
    href = posixpath.relpath(entry_path, posixpath.dirname(current) or ".")
    here = ' aria-current="page"' if entry_path == current else ""
    return f'<a href="{html.escape(href)}"{here}>{html.escape(title)}</a>'


def _nav(nav: tuple[Entry, ...], current: str) -> str:
    def items(entries: tuple[Entry, ...]) -> str:
        lines = []
        for e in entries:
            children = f"\n<ul>\n{items(e.pages)}\n</ul>\n" if e.pages else ""
            lines.append(f"<li>{_link(current, e.path, e.title)}{children}</li>")
        return "\n".join(lines)

    return (
        f'<header class="site-header">{_link(current, HOME, "data-finops")}</header>\n'
        f'<nav aria-label="Site">\n<ul>\n{items(tuple(e for e in nav if e.path != HOME))}\n'
        "</ul>\n</nav>"
    )


def _pages(section: Entry) -> str:
    """The list of a section's pages, for its index page's optional `pages` region."""
    if not section.pages:
        return "<p>No pages yet.</p>"
    items = "\n".join(f"<li>{_link(section.path, p.path, p.title)}</li>" for p in section.pages)
    return f"<ul>\n{items}\n</ul>"


def _stamp(text: str, name: str, content: str) -> str:
    pattern = re.compile(rf"(<!-- {name}:start -->\n).*?(<!-- {name}:end -->)", re.DOTALL)
    stamped, count = pattern.subn(lambda m: f"{m.group(1)}{content}\n{m.group(2)}", text)
    return stamped
