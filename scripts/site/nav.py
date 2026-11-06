"""The site's nav, read from `site/nav.json`: one list of entries, a section holding its pages."""

import json
from dataclasses import dataclass
from typing import Any

HOME = "index.html"


class SiteError(Exception):
    """The site cannot be built as it stands."""


@dataclass(frozen=True)
class Entry:
    title: str
    path: str  # a page, relative to the site root
    pages: tuple["Entry", ...] = ()


def parse_nav(text: str) -> tuple[Entry, ...]:
    """The entries of `site/nav.json`, in order. Raises SiteError saying what is wrong."""
    try:
        raw = json.loads(text)
        if not isinstance(raw, list):
            raise ValueError("it must be a list of entries")
        entries = tuple(_entry(item) for item in raw)
    except (ValueError, TypeError, KeyError) as error:
        raise SiteError(f"nav.json: {error}") from error
    paths = [e.path for e in walk(entries)]
    if duplicates := sorted({p for p in paths if paths.count(p) > 1}):
        raise SiteError(f"nav.json: listed more than once: {', '.join(duplicates)}")
    if not entries or entries[0].path != HOME or entries[0].pages:
        raise SiteError(f"nav.json: the first entry must be {HOME}, with no pages")
    return entries


def walk(entries: tuple[Entry, ...]) -> list[Entry]:
    """Every entry, each section followed by its pages."""
    return [e for top in entries for e in (top, *walk(top.pages))]


def _entry(raw: Any) -> Entry:
    if not isinstance(raw, dict):
        raise ValueError(f"an entry must be an object, not {raw!r}")
    title, path = raw.get("title"), raw.get("path")
    if not isinstance(title, str) or not isinstance(path, str):
        raise ValueError(f"an entry needs a title and a path: {raw!r}")
    if not path.endswith(".html"):  # so never a fragment, query or directory
        raise ValueError(f"{path!r} is not a page")
    return Entry(title, path, tuple(_entry(p) for p in raw.get("pages", [])))
