"""Run a page's commands and compare the output it shows (spec 07, "Runnable pages").

A `<pre><code class="run">` block holds `data-finops …` lines, and `curl -s URL` lines once a
`serve` line has opened the API. Each runs, in order, from one temporary working directory
holding a copy of `fixtures/`, so pages use repo-relative fixture paths. `serve` goes through
the real command line, but the server is never started: the runner keeps the app and answers
`curl` through FastAPI's test client. A `<pre class="output checked">` that is the very next
element after a run block must match that block's output line by line, `…` standing for any
text in a line.

Unlike the rest of scripts/site, this module imports data_finops and FastAPI; only tests import
it, so `check` stays stdlib-only for the Pages workflow.
"""

import contextlib
import io
import os
import re
import shlex
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from data_finops.cli import main


@dataclass(frozen=True, order=True)
class RunProblem:
    page: str
    block: int  # the run block's number on the page, from 1; 0 before the first
    line: int  # the line in the page's HTML
    message: str


@dataclass
class _Block:
    kind: Literal["run", "checked", "other"]
    line: int  # where its text starts
    follows_gap: bool  # another element sits between it and the block before
    text: str = ""


class _Blocks(HTMLParser):
    """The `<pre>` blocks of a page, in order, and whether anything sits between them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self._current: _Block | None = None
        self._gap = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set((dict(attrs).get("class") or "").split())
        if tag == "pre":
            kind: Literal["checked", "other"] = (
                "checked" if {"output", "checked"} <= classes else "other"
            )
            self._current = _Block(kind, self.getpos()[0], self._gap)
            self.blocks.append(self._current)
            self._gap = False
        elif tag == "code" and "run" in classes and self._current is not None:
            self._current.kind = "run"
            self._current.line = self.getpos()[0]
        elif self._current is None:
            self._gap = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre":
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.text += data
        elif data.strip():
            self._gap = True


class _Session:
    """The API a page's `serve` line opened, answered in-process instead of by a server."""

    def __init__(self) -> None:
        self.client: TestClient | None = None
        self.address = ""

    def start_server(self, app: Any, host: str, port: int) -> None:
        """Stands in for uvicorn (the command line's `start_server`): keep the app."""
        self.close()
        self.client = TestClient(app, raise_server_exceptions=False)
        self.address = f"http://{host}:{port}"

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


def run_page(page: Path, repo: Path) -> list[RunProblem]:
    """Every problem running `page`'s commands, or none when the page is true."""
    fixtures = (repo / "fixtures").resolve()  # before the working directory changes
    parser = _Blocks()
    parser.feed(page.read_text(encoding="utf-8"))
    parser.close()
    problems: list[RunProblem] = []
    with (
        tempfile.TemporaryDirectory() as work,
        contextlib.chdir(work),
        _without_data_finops_settings(),
    ):
        shutil.copytree(fixtures, Path(work) / "fixtures")
        session = _Session()
        try:
            block_number = 0
            output: str | None = None  # the last run block's output, while nothing follows it
            for block in parser.blocks:
                if block.kind == "run":
                    block_number += 1
                    output, problem = _run_block(page.name, block_number, block, session)
                    problems.extend([problem] if problem else [])
                    continue
                if block.kind == "checked":
                    if output is None or block.follows_gap:
                        message = "this checked output follows no run block directly"
                        problems.append(RunProblem(page.name, block_number, block.line, message))
                    elif problem := _compare(page.name, block_number, block, output):
                        problems.append(problem)
                output = None
        finally:
            session.close()
    return problems


@contextlib.contextmanager
def _without_data_finops_settings() -> Iterator[None]:
    """A page runs the same wherever it runs: the caller's DATA_FINOPS_* settings are hidden."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("DATA_FINOPS_")}
    for name in saved:
        del os.environ[name]
    try:
        yield
    finally:
        os.environ.update(saved)


def _run_block(
    page: str, block_number: int, block: _Block, session: _Session
) -> tuple[str | None, RunProblem | None]:
    """The block's combined output, or the problem that stopped it."""
    output = io.StringIO()
    for offset, text in enumerate(block.text.split("\n")):
        if not text.strip():
            continue
        refusal = _run_line(text, session, output)
        if refusal is not None:
            return None, RunProblem(page, block_number, block.line + offset, refusal)
    return output.getvalue(), None


def _run_line(text: str, session: _Session, output: io.StringIO) -> str | None:
    """Run one line, writing what it prints to `output`; or say why it cannot run."""
    try:
        argv = shlex.split(text)
    except ValueError as error:  # e.g. "No closing quotation"
        return f"cannot split the line: {error}"
    if argv[0] == "curl":
        return _curl(argv[1:], session, output)
    if argv[0] != "data-finops":
        return f"only data-finops and curl lines may run, not {argv[0]!r}"
    if argv[1:2] == ["demo"]:
        return "demo cannot run in a page: run ingest, calculate and serve instead"
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = main(argv[1:], start_server=session.start_server)
    except Exception as error:  # a crash is a problem with the page's command, never fatal
        return f"`{text.strip()}` raised {type(error).__name__}: {error}"
    if code != 0:
        return f"`{text.strip()}` exited {code}: {output.getvalue().strip()}"
    return None


def _curl(args: list[str], session: _Session, output: io.StringIO) -> str | None:
    """Answer `curl [-s] URL` as a GET through the test client, writing the response body."""
    if session.client is None:
        return "curl needs the API: run `data-finops serve` first"
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
    if f"{parts.scheme}://{parts.netloc}" != session.address:
        return f"curl must name the address serve printed, {session.address}, not {urls[0]}"
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    response = session.client.get(target)
    if not response.is_success:
        return f"curl {urls[0]} answered HTTP {response.status_code}: {response.text}"
    output.write(response.text + "\n")
    return None


def _compare(page: str, block_number: int, shown: _Block, output: str) -> RunProblem | None:
    lead = len(shown.text) - len(shown.text.lstrip("\n"))  # lines before the text starts
    expected = shown.text.strip("\n").split("\n")
    actual = output.strip("\n").split("\n")
    for offset, want in enumerate(expected):
        got = actual[offset] if offset < len(actual) else None
        pattern = ".*".join(re.escape(part) for part in want.rstrip().split("…"))
        if got is None or not re.fullmatch(pattern, got.rstrip()):
            message = f"shows {want!r} but the commands printed {got!r}"
            return RunProblem(page, block_number, shown.line + lead + offset, message)
    if len(actual) > len(expected):
        message = f"the commands printed more: {actual[len(expected)]!r}"
        return RunProblem(page, block_number, shown.line + lead + len(expected) - 1, message)
    return None
