"""The decision records page lists every ADR as its file states it (spec 07)."""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from scripts.site.build import SOURCE

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "site" / "explanation" / "decision-records.html"
ADRS = REPO / "docs" / "adr"
ADR_URL = f"{SOURCE}/blob/main/docs/adr/"
ADR_FILE = re.compile(r"\d{4}-[a-z0-9-]+\.md")


@dataclass
class _Row:
    href: str = ""
    cells: list[str] = field(default_factory=list)


class _Rows(HTMLParser):
    """Each body row of `<table id="decision-records">`: its first link and its cells' text."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[_Row] = []
        self._in_table = False
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table" and attributes.get("id") == "decision-records":
            self._in_table = True
        elif self._in_table and tag == "tr":
            self.rows.append(_Row())
        elif self._in_table and tag == "td" and self.rows:
            self._cell = []
        elif self._in_table and tag == "a" and self.rows and not self.rows[-1].href:
            self.rows[-1].href = attributes.get("href") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._in_table = False
        elif tag == "td" and self._cell is not None:
            self.rows[-1].cells.append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _listed() -> list[tuple[str, list[str]]]:
    parser = _Rows()
    parser.feed(PAGE.read_text(encoding="utf-8"))
    return [(row.href, row.cells) for row in parser.rows if row.cells]


def _status(text: str) -> str:
    """The ADR's status line, up to its first sentence's full stop."""
    line = re.search(r"^Status: (.+)$", text, flags=re.M)
    assert line, "an ADR has no Status line"
    return re.split(r"\.(?:\s|$)", line[1], maxsplit=1)[0].rstrip(".")


def _expected() -> list[tuple[str, list[str]]]:
    rows = []
    for adr in sorted(path for path in ADRS.glob("*.md") if path.name != "README.md"):
        assert ADR_FILE.fullmatch(adr.name), f"{adr.name} is not named NNNN-title.md"
        text = adr.read_text(encoding="utf-8")
        title = re.search(r"^# ADR (\d{4})\s*[:—]\s*(.+)$", text, flags=re.M)  # 0014 uses a dash
        assert title, f"{adr.name} has no '# ADR NNNN: title' heading"
        rows.append((ADR_URL + adr.name, [title[1], title[2], _status(text)]))
    return rows


def test_the_page_lists_every_adr_in_order_with_its_title_and_status() -> None:
    expected = _expected()
    assert expected, "no ADRs found"
    assert _listed() == expected


def test_a_status_stops_at_its_first_sentence() -> None:
    assert _status("Status: Accepted (design, 2026-11-01). Refines ADR 0003.") == (
        "Accepted (design, 2026-11-01)"
    )
    assert _status("Status: Accepted (see v1.2 notes); amended.") == (
        "Accepted (see v1.2 notes); amended"
    )
    assert _status("Status: Accepted") == "Accepted"
