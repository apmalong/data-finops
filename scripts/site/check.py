"""Check every file of the site against spec 07's rules, with no dependency beyond the stdlib.

The parser only collects facts about a page; the rule functions below turn facts into Problems.
Links resolve against the exact list of files in the site, so a link whose case differs from the
file's breaks here as it would on GitHub Pages, even on a case-insensitive disk.
"""

import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import unquote, urlsplit

from scripts.site.nav import SiteError, parse_nav, walk

Rule = Literal[
    "account-id",
    "broken-link",
    "duplicate-id",
    "external-url",
    "h1-count",
    "missing-alt",
    "missing-anchor",
    "missing-image",
    "missing-index",
    "missing-lang",
    "missing-title",
    "missing-nav",
    "bad-nav",
    "nav-entry-missing",
    "not-in-nav",
    "not-utf8",
    "root-absolute-link",
    "unbalanced-tag",
]


@dataclass(frozen=True, order=True)
class Problem:
    path: str  # relative to the site root, with forward slashes
    line: int
    rule: Rule
    message: str


PLACEHOLDER_ACCOUNT = "123456789012"
_TWELVE_DIGITS = re.compile(r"(?<!\d)\d{12}(?!\d)")
_EXTERNAL_URL = re.compile(r"https?://", re.IGNORECASE)
_CSS_URL = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)|@import\s+(['"])(.*?)\3""", re.IGNORECASE)
_TEXT = {".html", ".css", ".js", ".json", ".md", ".svg", ".txt"}

# Elements with no end tag. The site closes every other element explicitly, which is stricter
# than HTML's optional end tags and keeps "tags are balanced" (spec 07) checkable.
_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "track",
    "wbr",
}
# Attributes that load something into the page, as opposed to a link the reader follows.
_ASSET_ATTRS = {
    ("img", "src"),
    ("script", "src"),
    ("source", "src"),
    ("link", "href"),
    ("iframe", "src"),
    ("video", "src"),
    ("audio", "src"),
    ("video", "poster"),
}


@dataclass(frozen=True)
class _Ref:
    line: int
    target: str
    asset: bool  # loaded into the page (must stay inside the site), not a link to follow
    image: bool


@dataclass(frozen=True)
class _Code:
    line: int  # where the text starts
    text: str
    language: Literal["css", "js"]


class _Page(HTMLParser):
    """The facts about one page that the rules need."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[_Ref] = []
        self.ids: list[tuple[int, str]] = []
        self.images: list[tuple[int, str]] = []  # line, alt text
        self.h1_lines: list[int] = []
        self.code: list[_Code] = []  # inline style attributes, <style> and <script> text
        self.unbalanced: list[tuple[int, str]] = []
        self.lang = ""
        self.title = ""
        self._open: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v for k, v in attrs if v is not None}
        line = self.getpos()[0]
        if tag not in _VOID:
            self._open.append((tag, line))
        if tag == "html":
            self.lang = values.get("lang", "").strip()
        elif tag == "h1":
            self.h1_lines.append(line)
        elif tag == "img":
            self.images.append((line, values.get("alt", "")))
        if "id" in values:
            self.ids.append((line, values["id"]))
        if "style" in values:
            self.code.append(_Code(line, values["style"], "css"))
        for attr in ("href", "src", "poster"):
            if attr in values:
                asset = (tag, attr) in _ASSET_ATTRS
                self.refs.append(_Ref(line, values[attr], asset, tag == "img"))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self._open.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        if tag not in (t for t, _ in self._open):
            self.unbalanced.append((self.getpos()[0], f"</{tag}> closes nothing"))
            return
        while (top := self._open.pop())[0] != tag:
            self._unclosed(*top)

    def handle_data(self, data: str) -> None:
        current = self._open[-1][0] if self._open else ""
        if current == "title":
            self.title += data
        elif current in ("style", "script"):
            self.code.append(_Code(self.getpos()[0], data, "css" if current == "style" else "js"))

    def close(self) -> None:
        super().close()
        while self._open:
            self._unclosed(*self._open.pop())

    def _unclosed(self, tag: str, line: int) -> None:
        self.unbalanced.append((line, f"<{tag}> is never closed"))


@dataclass(frozen=True)
class _Site:
    files: frozenset[str]  # every file, relative to the root, exactly as cased on disk
    pages: dict[str, _Page]

    def resolve(self, base: str, path: str) -> str | None:
        """The file a relative path from `base` names, or None if there is none."""
        # A target outside the site ("../…") is never in `files`, so it resolves to nothing.
        target = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(path)))
        if target in self.files:
            return target
        index = posixpath.join(target, "index.html")
        return index if index in self.files else None


def check_site(root: Path) -> list[Problem]:
    """Every problem in the site under `root`, sorted by file and line."""
    texts: dict[str, str] = {}
    problems: list[Problem] = []
    paths = sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []
    for path in paths:
        name = path.relative_to(root).as_posix()
        if path.suffix.lower() in _TEXT:
            try:
                texts[name] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                problems.append(Problem(name, 1, "not-utf8", "the file is not UTF-8"))
    site = _Site(frozenset(p.relative_to(root).as_posix() for p in paths), {})
    for name, text in texts.items():
        if name.lower().endswith(".html"):
            site.pages[name] = page = _Page()
            page.feed(text)
            page.close()
    if "index.html" not in site.pages:
        problems.append(Problem("index.html", 1, "missing-index", "the site has no home page"))
    for name, page in site.pages.items():
        problems.extend(_page_rules(site, name, page))
    for name, text in texts.items():
        problems.extend(_text_rules(site, name, text))
    if "nav.json" not in site.files:
        problems.append(Problem("nav.json", 1, "missing-nav", "the site has no nav.json"))
    elif "nav.json" in texts:
        problems.extend(_nav_rules(site, texts["nav.json"]))
    return sorted(problems)


def _nav_rules(site: _Site, text: str) -> Iterator[Problem]:
    try:
        nav = parse_nav(text)
    except SiteError as error:
        yield Problem("nav.json", 1, "bad-nav", str(error))
        return
    listed = {entry.path for entry in walk(nav)}
    lines = text.splitlines()
    for path in sorted(listed - site.files):
        line = next((n for n, t in enumerate(lines, 1) if f'"{path}"' in t), 1)
        yield Problem("nav.json", line, "nav-entry-missing", f"{path} does not exist")
    for name in sorted(set(site.pages) - listed):
        yield Problem(name, 1, "not-in-nav", "the page is not in nav.json")


def _page_rules(site: _Site, name: str, page: _Page) -> Iterator[Problem]:
    if not page.lang:
        yield Problem(name, 1, "missing-lang", "<html> needs a lang attribute")
    if not page.title.strip():
        yield Problem(name, 1, "missing-title", "the page needs a non-empty <title>")
    if len(page.h1_lines) != 1:
        line = page.h1_lines[1] if page.h1_lines else 1
        yield Problem(name, line, "h1-count", f"{len(page.h1_lines)} <h1> elements, not one")
    seen: set[str] = set()
    for line, element_id in page.ids:
        if element_id in seen:
            yield Problem(name, line, "duplicate-id", f"id {element_id!r} is already used")
        seen.add(element_id)
    for line, alt in page.images:
        if not alt.strip():
            yield Problem(name, line, "missing-alt", "<img> needs alt text")
    for line, message in page.unbalanced:
        yield Problem(name, line, "unbalanced-tag", message)
    for ref in page.refs:
        yield from _ref_rules(site, name, ref)
    for code in page.code:
        yield from _code_rules(site, name, code.line, code.text, code.language)


def _ref_rules(site: _Site, name: str, ref: _Ref) -> Iterator[Problem]:
    parts = urlsplit(ref.target)
    if parts.scheme or ref.target.startswith("//"):
        if ref.asset and (ref.target.startswith("//") or parts.scheme in ("http", "https")):
            yield Problem(
                name, ref.line, "external-url", f"{ref.target}: load nothing from outside"
            )
        return
    if ref.target.startswith("/"):
        yield Problem(name, ref.line, "root-absolute-link", f"{ref.target}: use a relative link")
        return
    target: str | None = name
    if parts.path:
        target = site.resolve(name, parts.path)
        if target is None:
            rule: Rule = "missing-image" if ref.image else "broken-link"
            yield Problem(name, ref.line, rule, f"{ref.target} does not exist")
            return
    page = site.pages.get(target or "")
    fragment = unquote(parts.fragment)
    if fragment and page is not None and fragment not in {i for _, i in page.ids}:
        yield Problem(name, ref.line, "missing-anchor", f"{ref.target}: no such id")


def _code_rules(
    site: _Site, name: str, start: int, text: str, language: Literal["css", "js"]
) -> Iterator[Problem]:
    """CSS or JS, in a file or inline in a page: nothing loads from outside the site."""
    for line, content in enumerate(text.split("\n"), start=start):
        if _EXTERNAL_URL.search(content):
            yield Problem(name, line, "external-url", "CSS and JS load nothing from outside")
        if language != "css":
            continue
        for match in _CSS_URL.finditer(content):
            target = (match.group(2) or match.group(4) or "").strip()
            if target.startswith("//"):
                yield Problem(name, line, "external-url", f"{target}: load nothing from outside")
            elif target.startswith("/"):
                yield Problem(name, line, "root-absolute-link", f"{target}: use a relative link")
            elif target and not target.startswith("#") and not urlsplit(target).scheme:
                if site.resolve(name, urlsplit(target).path) is None:
                    yield Problem(name, line, "broken-link", f"{target} does not exist")


def _text_rules(site: _Site, name: str, text: str) -> Iterator[Problem]:
    for line, content in enumerate(text.splitlines(), start=1):
        if any(m.group() != PLACEHOLDER_ACCOUNT for m in _TWELVE_DIGITS.finditer(content)):
            # Never echo the number: it may be a real account ID (ADR 0012).
            message = f"a 12-digit number other than {PLACEHOLDER_ACCOUNT}"
            yield Problem(name, line, "account-id", message)
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in (".css", ".js"):
        yield from _code_rules(site, name, 1, text, "css" if suffix == ".css" else "js")
