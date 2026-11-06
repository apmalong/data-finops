"""Check every page of the site against spec 07's rules, with no dependency beyond the stdlib."""

import posixpath
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

PLACEHOLDER_ACCOUNT = "123456789012"
_TWELVE_DIGITS = re.compile(r"(?<!\d)\d{12}(?!\d)")
_EXTERNAL_URL = re.compile(r"https?://", re.IGNORECASE)
_ASSETS = {".css", ".js"}
_TEXT = {".html", ".css", ".js", ".json", ".md", ".svg", ".txt"}

# Elements with no end tag. Every other element must be closed explicitly (spec 07).
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


@dataclass(frozen=True, order=True)
class Problem:
    path: str  # relative to the site root, with forward slashes
    line: int
    rule: str
    message: str


@dataclass(frozen=True)
class _Link:
    line: int
    target: str
    missing: str = "broken-link"  # the rule broken when the target does not exist


@dataclass
class _Page(HTMLParser):
    links: list[_Link] = field(default_factory=list)
    ids: set[str] = field(default_factory=set)
    found: list[tuple[int, str, str]] = field(default_factory=list)  # line, rule, message
    lang: str = ""
    title: str = ""
    h1_lines: list[int] = field(default_factory=list)
    _in_title: bool = False
    _open: list[tuple[str, int]] = field(default_factory=list)  # tag, line

    def __post_init__(self) -> None:
        super().__init__(convert_charrefs=True)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        line = self.getpos()[0]
        if tag not in _VOID:
            self._open.append((tag, line))
        if tag == "html":
            self.lang = (values.get("lang") or "").strip()
        elif tag == "title":
            self._in_title = True
        elif tag == "h1":
            self.h1_lines.append(line)
        if (element_id := values.get("id")) is not None:
            if element_id in self.ids:
                self.found.append((line, "duplicate-id", f"id {element_id!r} is already used"))
            self.ids.add(element_id)
        if (href := values.get("href")) is not None:
            self.links.append(_Link(line, href))
        if (src := values.get("src")) is not None:
            self.links.append(_Link(line, src, "missing-image" if tag == "img" else "broken-link"))
        if tag == "img" and not (values.get("alt") or "").strip():
            self.found.append((line, "missing-alt", "<img> needs alt text"))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self._open.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _VOID:
            return
        opened = [t for t, _ in self._open]
        if tag not in opened:
            self.found.append((self.getpos()[0], "unbalanced-tag", f"</{tag}> closes nothing"))
            return
        while (top := self._open.pop())[0] != tag:
            self._unclosed(*top)

    def _unclosed(self, tag: str, line: int) -> None:
        self.found.append((line, "unbalanced-tag", f"<{tag}> is never closed"))

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data

    def finish(self) -> None:
        while self._open:
            self._unclosed(*self._open.pop())
        if not self.lang:
            self.found.append((1, "missing-lang", "<html> needs a lang attribute"))
        if not self.title.strip():
            self.found.append((1, "missing-title", "the page needs a non-empty <title>"))
        if len(self.h1_lines) != 1:
            line = self.h1_lines[1] if self.h1_lines else 1
            self.found.append((line, "h1-count", f"{len(self.h1_lines)} <h1> elements, not one"))


def _parse(path: Path) -> _Page:
    page = _Page()
    page.feed(path.read_text(encoding="utf-8"))
    page.close()
    page.finish()
    return page


def check_site(root: Path) -> list[Problem]:
    """Every problem in the site under `root`, sorted by file and line."""
    pages = {p.relative_to(root).as_posix(): _parse(p) for p in sorted(root.rglob("*.html"))}
    problems: list[Problem] = []
    if "index.html" not in pages:
        problems.append(Problem("index.html", 1, "missing-index", "the site has no home page"))
    for name, page in pages.items():
        problems.extend(Problem(name, line, rule, message) for line, rule, message in page.found)
        for link in page.links:
            problems.extend(_check_link(root, pages, name, link))
    for path in sorted(root.rglob("*")):
        if path.suffix in _TEXT and path.is_file():
            problems.extend(_scan_text(path.relative_to(root).as_posix(), path))
    return sorted(problems)


def _scan_text(name: str, path: Path) -> list[Problem]:
    problems = []
    for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if any(m.group() != PLACEHOLDER_ACCOUNT for m in _TWELVE_DIGITS.finditer(text)):
            # Never echo the number: it may be a real account ID (ADR 0012).
            problems.append(
                Problem(
                    name,
                    number,
                    "account-id",
                    f"a 12-digit number other than {PLACEHOLDER_ACCOUNT}",
                )
            )
        if path.suffix in _ASSETS and _EXTERNAL_URL.search(text):
            problems.append(
                Problem(
                    name, number, "external-url", "CSS and JS load nothing from outside the site"
                )
            )
    return problems


def _check_link(root: Path, pages: dict[str, _Page], name: str, link: _Link) -> list[Problem]:
    if link.target.startswith("/"):
        return [
            Problem(name, link.line, "root-absolute-link", f"{link.target}: use a relative link")
        ]
    parts = urlsplit(link.target)
    if parts.scheme:
        return []
    if parts.path:
        target = posixpath.normpath((PurePosixPath(name).parent / parts.path).as_posix())
        if target.split("/")[0] == ".." or not (root / target).is_file():
            return [Problem(name, link.line, link.missing, f"{link.target} does not exist")]
    else:
        target = name
    if parts.fragment and target in pages and parts.fragment not in pages[target].ids:
        return [Problem(name, link.line, "missing-anchor", f"{link.target}: no such id")]
    return []
