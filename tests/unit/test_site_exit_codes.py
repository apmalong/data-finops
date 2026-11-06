"""The hand-written exit codes and environment page stays true to the code (spec 07)."""

import ast
import re
from html.parser import HTMLParser
from pathlib import Path

from data_finops import cli

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "site" / "reference" / "exit-codes-and-environment.html"
NAME = re.compile(r"\bDATA_FINOPS_[A-Z0-9_]*[A-Z0-9]\b")


class _Terms(HTMLParser):
    """The `<code>` text of each `<dt>`, by the id of the `<dl>` it is in."""

    def __init__(self) -> None:
        super().__init__()
        self.terms: dict[str, list[str]] = {}
        self._list: str | None = None
        self._in_term = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "dl":
            self._list = dict(attrs).get("id") or ""
            self.terms.setdefault(self._list, [])
        elif tag == "dt":
            self._in_term = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "dl":
            self._list = None
        elif tag == "dt":
            self._in_term = False

    def handle_data(self, data: str) -> None:
        if self._in_term and self._list is not None and data.strip():
            self.terms[self._list].append(data.strip())


def _terms(list_id: str) -> list[str]:
    parser = _Terms()
    parser.feed(PAGE.read_text(encoding="utf-8"))
    return parser.terms.get(list_id, [])


def _names_in_code() -> set[str]:
    return {
        name
        for folder in ("src", "scripts")
        for path in (REPO / folder).rglob("*.py")
        for name in NAME.findall(path.read_text(encoding="utf-8"))
    }


def test_the_page_lists_exactly_the_cli_exit_codes() -> None:
    assert _terms("exit-codes") == [str(int(code)) for code in cli.ExitCode]


def test_the_cli_returns_no_exit_code_outside_its_exit_code_list() -> None:
    """A bare `return 3` in the CLI would be a code the page cannot be checked against."""
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    bare = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and type(node.value.value) is int
    ]
    assert bare == []


def test_the_page_lists_exactly_the_environment_variables_the_code_reads() -> None:
    found = _names_in_code()
    assert found, "no DATA_FINOPS_* names found in src/ or scripts/"
    assert sorted(_terms("environment")) == sorted(found)
