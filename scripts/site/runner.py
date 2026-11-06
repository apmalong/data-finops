"""Run a page's commands and compare the output it shows (spec 07, "Runnable pages").

A `<pre><code class="run">` block holds `data-finops …` lines, and `curl` lines once a `serve`
line has opened the API. Each runs, in order, from one temporary working directory
holding a copy of `fixtures/`, so pages use repo-relative fixture paths. `serve` goes through
the real command line, but the server is never started: the runner keeps the app and answers
`curl` through FastAPI's test client: a GET, or a POST of a file, with any headers it names.
`$CALCULATION_RUN_ID` stands for the Calculation Run `calculate` last printed, and `$(cat FILE)`
for the file's text, so a page reads as a shell reader would run it. A
`<pre class="output checked">` that is the very next element after a run block must match that
block's output line by line, `…` standing for any text in a line.

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
        self.calculation_run_id: str | None = None  # the last one `calculate` printed

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
    expanded = _expand(text, session)
    if isinstance(expanded, _Refusal):
        return expanded.message
    text = expanded
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
    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
            code = main(argv[1:], start_server=session.start_server)
    except Exception as error:  # a crash is a problem with the page's command, never fatal
        output.write(printed.getvalue())
        return f"`{text.strip()}` raised {type(error).__name__}: {error}"
    output.write(printed.getvalue())
    if code != 0:
        return f"`{text.strip()}` exited {code}: {printed.getvalue().strip()}"
    if runs := _CALCULATION_RUN.findall(printed.getvalue()):  # only calculate prints these
        session.calculation_run_id = runs[-1]
    return None


@dataclass(frozen=True)
class _Refusal:
    message: str


def _expand(text: str, session: _Session) -> str | _Refusal:
    """`text` with `$CALCULATION_RUN_ID` and `$(cat FILE)` expanded as a shell would, or why
    it cannot be: a page can name a Calculation Run or send a token without writing it down."""
    for use in (*_RUN_ID.finditer(text), *_CAT.finditer(text)):
        if _single_quoted(text, use.start()):
            return _Refusal(f"{use[0]} is in single quotes, where a shell would not expand it")
    if _RUN_ID.search(text):
        if session.calculation_run_id is None:
            return _Refusal("$CALCULATION_RUN_ID needs a Calculation Run: run calculate first")
        text = _RUN_ID.sub(session.calculation_run_id, text)
    for use in _CAT.finditer(text):
        try:
            content = Path(use["path"]).read_text(encoding="utf-8").rstrip("\n")
        except OSError:
            return _Refusal(f"{use[0]} cannot read {use['path']}")
        text = text.replace(use[0], content, 1)
    return text


def _single_quoted(text: str, index: int) -> bool:
    """Whether `text[index]` is in single quotes, as a shell reads them (backslashes aside)."""
    quote = ""
    for char in text[:index]:
        if char in "'\"" and quote in ("", char):
            quote = "" if quote else char
    return quote == "'"


_RUN_ID = re.compile(r"\$\{CALCULATION_RUN_ID\}|\$CALCULATION_RUN_ID\b")
_CAT = re.compile(r"\$\(cat (?P<path>[^\s)]+)\)")
_CALCULATION_RUN = re.compile(r"^calculate: .* (calc-\S+)$", re.M)
_HEADER = re.compile(r"(?P<name>[A-Za-z0-9-]+):(?!//)\s*(?P<value>\S.*)")  # never a URL


@dataclass
class _Request:
    """What a page's curl line asks for."""

    url: str
    method: str
    headers: dict[str, str]
    body: bytes | None


def _parse_curl(args: list[str]) -> _Request | str:
    """`curl [-s] [-X GET|POST] [-H 'Name: value']… [--data-binary @file] URL` as a request,
    or why it cannot be sent. A body makes it a POST and needs a Content-Type, as in curl."""
    urls: list[str] = []
    method: str | None = None
    headers: dict[str, str] = {}
    body: bytes | None = None
    words = iter(args)
    for arg in words:
        if arg == "-s":
            continue
        if not arg.startswith("-"):
            urls.append(arg)
            continue
        if arg not in ("-X", "-H", "--data-binary"):
            return f"curl {arg} is not supported: use -s, -X, -H or --data-binary"
        value = next(words, None)
        if value is None:
            return f"curl {arg} needs a value"
        if arg == "-X":
            if value not in ("GET", "POST"):
                return f"curl -X {value} is not supported: a page's curl sends GET or POST"
            method = value
        elif arg == "-H":
            if not (header := _HEADER.fullmatch(value)):
                return f"curl -H needs 'Name: value', not {value!r}"
            headers[header["name"]] = header["value"]
        else:
            if not value.startswith("@"):
                return "curl --data-binary needs @file: a page's curl sends only files"
            try:
                body = Path(value[1:]).read_bytes()
            except OSError:
                return f"curl cannot read {value[1:]}"
    if len(urls) != 1:
        return f"curl needs exactly one URL, not {len(urls)}"
    if body is not None and "content-type" not in {name.lower() for name in headers}:
        return "curl --data-binary needs -H 'Content-Type: …', or curl sends a form type"
    return _Request(urls[0], method or ("POST" if body is not None else "GET"), headers, body)


def _curl(args: list[str], session: _Session, output: io.StringIO) -> str | None:
    """Send a page's curl line through the test client, writing the response body."""
    if session.client is None:
        return "curl needs the API: run `data-finops serve` first"
    request = _parse_curl(args)
    if isinstance(request, str):
        return request
    parts = urlsplit(request.url)
    if f"{parts.scheme}://{parts.netloc}" != session.address:
        return f"curl must name the address serve printed, {session.address}, not {request.url}"
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    response = session.client.request(
        request.method, target, headers=request.headers, content=request.body
    )
    if not response.is_success:
        return f"curl {request.url} answered HTTP {response.status_code}: {response.text}"
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
