"""Run a page's commands and compare the output it shows (spec 07, "Runnable pages").

A `<pre><code class="run">` block holds `data-finops …` lines, and `curl` lines once a `serve`
or `demo` line has opened the API. Each runs, in order, from one temporary working directory
holding a copy of `fixtures/`, so pages use repo-relative fixture paths. `serve` and `demo` go
through the real command line, but the server is never started: the runner keeps the app and
answers `curl` through FastAPI's test client. A `<pre class="output checked">` straight after a
run block must match that block's output line by line, `…` standing for any text in a line.

This module imports data_finops and FastAPI, so `check` (stdlib only) never imports it.
"""

import contextlib
import io
import re
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True, order=True)
class RunProblem:
    page: str
    block: int  # the run block's number on the page, from 1
    line: int  # the line in the page's HTML
    message: str


@dataclass
class _Block:
    kind: str  # "run", "checked" or "other"
    line: int  # where its text starts
    text: str = ""


class _Blocks(HTMLParser):
    """The `<pre>` blocks of a page, in order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self._current: _Block | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if tag == "pre":
            kind = "checked" if {"output", "checked"} <= set(classes) else "other"
            self._current = _Block(kind, self.getpos()[0])
            self.blocks.append(self._current)
        elif tag == "code" and "run" in classes and self._current is not None:
            self._current.kind = "run"
            self._current.line = self.getpos()[0]

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre":
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.text += data


class _Session:
    """What the page's commands have built up: the API, once `serve` or `demo` has opened it."""

    def __init__(self) -> None:
        self.client: Any = None

    def keep(self, app: Any, host: str, port: int) -> None:
        """Stands in for starting the server: keep the app and answer through a test client."""
        from fastapi.testclient import TestClient

        self.close()
        self.client = TestClient(app)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


def run_page(page: Path, repo: Path) -> list[RunProblem]:
    """Every problem running `page`'s commands, or none when the page is true."""
    parser = _Blocks()
    parser.feed(page.read_text(encoding="utf-8"))
    parser.close()
    problems: list[RunProblem] = []
    with tempfile.TemporaryDirectory() as work, contextlib.chdir(work):
        shutil.copytree(repo / "fixtures", Path(work) / "fixtures")
        session = _Session()
        try:
            number = 0
            for index, block in enumerate(parser.blocks):
                if block.kind != "run":
                    continue
                number += 1
                output = _run_block(page.name, number, block, session, problems)
                following = parser.blocks[index + 1] if index + 1 < len(parser.blocks) else None
                if output is not None and following is not None and following.kind == "checked":
                    problems.extend(_compare(page.name, number, following, output))
        finally:
            session.close()
    return problems


def _run_block(
    page: str, number: int, block: _Block, session: _Session, problems: list[RunProblem]
) -> str | None:
    """The block's combined output, or None once a line fails."""
    from data_finops.cli import main

    output = io.StringIO()
    for offset, text in enumerate(block.text.split("\n")):
        if not text.strip():
            continue
        line = block.line + offset
        try:
            argv = shlex.split(text)
        except ValueError as error:  # e.g. "No closing quotation"
            problems.append(RunProblem(page, number, line, f"cannot split the line: {error}"))
            return None
        if argv[0] == "curl":
            refusal = _curl(argv[1:], session, output)
            if refusal is not None:
                problems.append(RunProblem(page, number, line, refusal))
                return None
            continue
        if argv[0] != "data-finops":
            message = f"only data-finops and curl lines may run, not {argv[0]!r}"
            problems.append(RunProblem(page, number, line, message))
            return None
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(argv[1:], start_server=session.keep)
        if code != 0:
            message = f"`{text.strip()}` exited {code}: {output.getvalue().strip()}"
            problems.append(RunProblem(page, number, line, message))
            return None
    return output.getvalue()


def _curl(args: list[str], session: _Session, output: io.StringIO) -> str | None:
    """Answer `curl [-s] URL` as a GET through the test client.

    Returns why the line cannot run, or None after writing the response body to `output`.
    """
    if session.client is None:
        return "curl needs the API: run `data-finops serve` (or `demo`) first"
    urls: list[str] = []
    for arg in args:
        if arg in ("-s", "--silent"):
            continue
        if arg.startswith("-"):
            return f"curl {arg} is not supported: a page's curl only reads, with -s"
        urls.append(arg)
    if len(urls) != 1:
        return f"curl needs exactly one URL, not {len(urls)}"
    parts = urlsplit(urls[0])
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    output.write(session.client.get(target).text + "\n")
    return None


def _compare(page: str, number: int, shown: _Block, output: str) -> list[RunProblem]:
    expected = shown.text.strip("\n").split("\n")
    actual = output.strip("\n").split("\n")
    for offset, want in enumerate(expected):
        got = actual[offset] if offset < len(actual) else None
        pattern = ".*".join(re.escape(part) for part in want.rstrip().split("…"))
        if got is None or not re.fullmatch(pattern, got.rstrip()):
            message = f"shows {want!r} but the commands printed {got!r}"
            return [RunProblem(page, number, shown.line + offset, message)]
    if len(actual) > len(expected):
        message = f"the commands printed more: {actual[len(expected)]!r}"
        return [RunProblem(page, number, shown.line + len(expected) - 1, message)]
    return []
