"""Fuzz the four readers with generated and mutated input (#282).

Each reader (FOCUS, OpenLineage, dbt, Athena) is driven through `data-finops ingest`, the
seam agreed with the owner, into a new database. One OpenLineage event is also driven through
`WorkloadStore.ingest_event`, the public ingest function of an HTTP request body (spec 04).

Two input strategies:

- **mutated:** a valid fixture with one to four changes (a field removed or renamed, a value
  of another type, out of range or a planted sentinel, a list item repeated), then perhaps
  truncated, a byte flipped, an invalid UTF-8 byte put in, or a BOM added.
- **generated:** random bytes, or random JSON.

Each reader must accept or reject by its rule (spec 05): exit 0, 1 or 2, never an unhandled
exception. A rejected file or item stores nothing. When a file is rejected, the output does
not quote the sentinel that the mutations plant.

"Nothing stored" counts the rows of every table: no public read lists everything a
database holds. FOCUS uses the Ledger's own reads instead.

The oracles that can disagree with a reader use no reader code: an item identical to a
fixture item must be accepted, and counts come from the raw JSON. The checks against a
reader's own screening function only show that the CLI and the store agree with it.
"""

import codecs
import contextlib
import copy
import io
import json
import re
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from delivery_files import write_delivery
from fixture_database import ACCOUNT, FIXTURES, REGION
from hypothesis import example, given, settings
from hypothesis import strategies as st

from data_finops.cli import main
from data_finops.dbt.artifacts import ScreenedArtifact, screen_dbt_artifact
from data_finops.ledger import Ledger
from data_finops.lineage.validation import screen_events
from data_finops.providers.athena import screen_query_history
from data_finops.workload.store import WorkloadStore

SENTINEL = "zz-fuzz-sentinel"
"""A value the mutations plant. A rejection must never quote it."""

DEEP = b"[" * 100_000 + b"]" * 100_000
"""Nesting deeper than Python's recursion limit."""

FUZZ = settings(max_examples=max(50, settings().max_examples // 2))
"""Each example fills a new database: 100 examples in the `ci` profile."""

# --- strategies ------------------------------------------------------------------------

REPLACEMENTS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**70), max_value=2**70),
    st.floats(allow_nan=False, allow_infinity=False),
    st.sampled_from(
        [
            "",
            SENTINEL,
            "-1",
            "1e400",
            "not-a-date",
            "0001-01-01T00:00:00Z",
            "9999-12-31T23:59:59Z",
            "2026-13-45T25:61:61Z",
            "2026-11-01T00:00:00",
        ]
    ),
    st.just([]),
    st.just({}),
    st.just([SENTINEL]),
    st.just({SENTINEL: SENTINEL}),
)
"""Values of another type, out of range, or the sentinel."""

JSON_VALUES = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(st.text(max_size=10), children, max_size=5)
    ),
    max_leaves=30,
)


def _paths(value: Any, path: tuple[Any, ...] = ()) -> Iterator[tuple[Any, ...]]:
    yield path
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _paths(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _paths(child, (*path, index))


def _at(document: Any, path: tuple[Any, ...]) -> Any:
    for step in path:
        document = document[step]
    return document


@st.composite
def _mutation(draw: st.DrawFn, document: Any) -> Any:
    """The document with one change at a drawn place."""
    document = copy.deepcopy(document)
    path = draw(st.sampled_from(list(_paths(document))))
    if not path:
        return draw(REPLACEMENTS)
    parent, step = _at(document, path[:-1]), path[-1]
    change = draw(st.sampled_from(["remove", "replace", "repeat", "rename"]))
    if change == "remove":
        del parent[step]
    elif change == "repeat" and isinstance(parent, list):
        parent.insert(step, copy.deepcopy(parent[step]))
    elif change == "rename" and isinstance(parent, dict):
        parent[SENTINEL] = parent.pop(step)
    else:
        parent[step] = draw(REPLACEMENTS)
    return document


@st.composite
def _damaged(draw: st.DrawFn, data: bytes) -> bytes:
    """The bytes as written, or truncated, a byte flipped, an invalid byte put in, or a BOM."""
    damage = draw(st.sampled_from(["none", "none", "truncate", "flip", "invalid", "bom"]))
    if damage == "bom":
        return b"\xef\xbb\xbf" + data
    if damage == "none" or not data:
        return data
    at = draw(st.integers(min_value=0, max_value=len(data) - 1))
    if damage == "truncate":
        return data[:at]
    if damage == "flip":
        return data[:at] + bytes([data[at] ^ draw(st.integers(1, 255))]) + data[at + 1 :]
    return data[:at] + b"\xff" + data[at:]


@st.composite
def mutated(draw: st.DrawFn, fixture: Path, json_lines: bool = False) -> bytes:
    """A valid fixture with one to four changes, written as JSON (or JSON Lines), perhaps
    damaged."""
    document = json.loads(fixture.read_text(encoding="utf-8"))
    for _ in range(draw(st.integers(min_value=1, max_value=4))):
        document = draw(_mutation(document))
    if json_lines and isinstance(document, list) and draw(st.booleans()):
        text = "".join(json.dumps(item) + "\n" for item in document)
    else:
        text = json.dumps(document)
    return draw(_damaged(text.encode("utf-8")))


generated = st.one_of(
    st.binary(max_size=2000),
    JSON_VALUES.map(lambda value: json.dumps(value).encode("utf-8")),
)
"""Random bytes, or random JSON."""

# --- the seam --------------------------------------------------------------------------


def _ingest(folder: Path, kind: str, data: bytes) -> tuple[int, str, Path]:
    """`data-finops ingest <kind>` of `data` into a new database: the exit code, the output
    and the database."""
    path, database = folder / f"input-{kind}.json", folder / "data-finops.sqlite"
    path.write_bytes(data)
    extra = ["--account-id", ACCOUNT, "--region", REGION] if kind == "athena" else []
    return _run(["ingest", kind, str(path), "--db", str(database), *extra], database)


def _run(argv: list[str], database: Path) -> tuple[int, str, Path]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue() + err.getvalue(), database


def _rows(database: Path) -> int:
    """Every row of every table: zero when the database holds nothing."""
    if not database.exists():
        return 0
    with contextlib.closing(sqlite3.connect(database)) as connection:
        tables = [
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
                " AND name NOT LIKE 'sqlite_%' AND name != 'schema_version'"
            )
        ]
        return sum(
            connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]  # noqa: S608
            for name in tables
        )


def _quotes_no_value(code: int, output: str) -> None:
    if code != 0:
        assert SENTINEL not in output


def _in_a_new_folder(check: Callable[[Path, bytes], None]) -> Callable[[bytes], None]:
    def run(data: bytes) -> None:
        with tempfile.TemporaryDirectory() as folder:
            check(Path(folder), data)

    return run


# Oracles that use no reader code: the input as plain JSON, and the fixtures' own items.


def _document(data: bytes) -> Any:
    """The input as JSON, read with the standard library only. None when it is not JSON."""
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (ValueError, RecursionError):
        return None


def _fixture(*parts: str) -> Any:
    return json.loads(FIXTURES.joinpath(*parts).read_text(encoding="utf-8"))


def _canonical(item: Any) -> str:
    return json.dumps(item, sort_keys=True)


# --- dbt -------------------------------------------------------------------------------

DBT_REASONS = re.compile(
    r"too large|not a dbt artifact|unsupported \S+ v\d+|does not match \S+ v\d+"
    r"|no invocation_id|a node appears more than once|a node key differs from its unique_id"
)


DBT_FIXTURES = [_fixture("dbt", "manifest.json"), _fixture("dbt", "run_results.json")]
DBT_SUPPORTED = re.compile(r"https://schemas\.getdbt\.com/dbt/(manifest/v12|run-results/v6)\.json")


@_in_a_new_folder
def _dbt_by_its_rule(folder: Path, data: bytes) -> None:
    code, output, database = _ingest(folder, "dbt", data)
    document = _document(data)
    if document in DBT_FIXTURES:
        assert code == 0
    screened = screen_dbt_artifact(folder / "input-dbt.json")
    if isinstance(screened, ScreenedArtifact):
        assert code == 0
        assert _rows(database) > 0
        metadata = document["metadata"]
        assert DBT_SUPPORTED.fullmatch(metadata["dbt_schema_version"])
        assert isinstance(metadata["invocation_id"], str)
        assert metadata["invocation_id"]
    else:
        assert code == 1
        assert DBT_REASONS.fullmatch(screened)
        assert output == f"dbt: rejected: {screened}\n"
        assert _rows(database) == 0
    _quotes_no_value(code, output)


def _with_metadata(name: str, **metadata: str | None) -> bytes:
    """A dbt fixture with these metadata fields: the rules that random changes seldom reach."""
    document = _fixture("dbt", name)
    document["metadata"].update(metadata)
    return json.dumps(document).encode("utf-8")


@FUZZ
@given(
    st.one_of(
        mutated(FIXTURES / "dbt" / "manifest.json"),
        mutated(FIXTURES / "dbt" / "run_results.json"),
        generated,
    )
)
@example(DEEP)
@example(_with_metadata("run_results.json", invocation_id=""))
@example(_with_metadata("manifest.json", invocation_id=None))
@example(
    _with_metadata(
        "run_results.json", dbt_schema_version="https://schemas.getdbt.com/dbt/run-results/v5.json"
    )
)
def test_dbt_artifacts_are_accepted_or_rejected_by_their_rule(data: bytes) -> None:
    _dbt_by_its_rule(data)


# --- OpenLineage -----------------------------------------------------------------------


@_in_a_new_folder
def _openlineage_by_its_rule(folder: Path, data: bytes) -> None:
    code, output, database = _ingest(folder, "openlineage", data)
    screening = screen_events(folder / "input-openlineage.json")
    assert code == (1 if screening.rejected else 0)
    runs = {event.run.runId for event in screening.events}
    with WorkloadStore(database) as store:
        stored = sum(len(store.stored_events(str(run))) for run in runs)
    counts = re.match(r"openlineage: (\d+) accepted, (\d+) duplicates", output)
    assert counts is not None
    accepted, duplicates = int(counts[1]), int(counts[2])
    assert stored == accepted
    assert accepted + duplicates == len(screening.events)
    assert stored >= len({_canonical(item) for item in _items(data)} & LINEAGE_FIXTURES)
    assert (_rows(database) > 0) == bool(screening.events)
    _quotes_no_value(code, output)


LINEAGE_FIXTURES = {
    _canonical(item)
    for item in [*_fixture("openlineage", "events.json"), _fixture("openlineage", "one-event.json")]
}
"""Valid events: each one in an input must be stored."""


def _items(data: bytes) -> list[Any]:
    """The events of a JSON array, or of JSON Lines, read with the standard library only."""
    document = _document(data)
    if isinstance(document, list):
        return document
    items = []
    for line in data.removeprefix(codecs.BOM_UTF8).split(b"\n"):
        if (item := _document(line)) is not None:
            items.append(item)
    return items


@FUZZ
@given(
    st.one_of(
        mutated(FIXTURES / "openlineage" / "events.json", json_lines=True),
        mutated(FIXTURES / "openlineage" / "one-event.json"),
        generated,
    )
)
@example(DEEP)
@example(b"\n".join([DEEP, b"{}"]))
def test_openlineage_events_are_accepted_or_rejected_by_their_rule(data: bytes) -> None:
    _openlineage_by_its_rule(data)


@FUZZ
@given(st.one_of(mutated(FIXTURES / "openlineage" / "one-event.json"), generated))
@example(DEEP)
def test_one_openlineage_event_is_accepted_or_rejected_by_its_rule(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as folder:
        database = Path(folder) / "data-finops.sqlite"
        with WorkloadStore(database) as store:
            result = store.ingest_event(data)
        outcomes = result.accepted + result.duplicates, result.rejected, result.unsupported
        assert [bool(outcome) for outcome in outcomes].count(True) == 1  # one body, one outcome
        assert (_rows(database) > 0) == (result.accepted == 1)


# --- Athena ----------------------------------------------------------------------------


ATHENA_FIXTURES = {
    _canonical(entry) for entry in _fixture("athena", "query-executions.json")["QueryExecutions"]
}
"""Valid entries: each one in an input that is not rejected whole must be stored."""


@_in_a_new_folder
def _athena_by_its_rule(folder: Path, data: bytes) -> None:
    code, output, database = _ingest(folder, "athena", data)
    screening = screen_query_history(
        folder / "input-athena.json", account_id=ACCOUNT, region=REGION
    )
    if isinstance(screening, str):
        assert code == 1
        assert output == f"athena: rejected: {screening}\n"
        assert _rows(database) == 0
    else:
        entries = _document(data)["QueryExecutions"]
        assert len(screening.entries) + len(screening.rejected) == len(entries)
        assert code == (1 if screening.rejected else 0)
        ids = [entry.parsed.QueryExecutionId for entry in screening.entries]
        valid = [e["QueryExecutionId"] for e in entries if _canonical(e) in ATHENA_FIXTURES]
        with WorkloadStore(database) as store:
            assert store.stored_query_execution_ids(ACCOUNT, REGION, ids) == set(ids)
            assert set(valid) <= store.stored_query_execution_ids(ACCOUNT, REGION, valid)
        assert (_rows(database) > 0) == bool(ids)
    _quotes_no_value(code, output)


def _athena_entry(**status: str) -> bytes:
    """The Athena fixture with these Status fields in its first entry."""
    document = _fixture("athena", "query-executions.json")
    document["QueryExecutions"][0]["Status"].update(status)
    return json.dumps(document).encode("utf-8")


@FUZZ
@given(st.one_of(mutated(FIXTURES / "athena" / "query-executions.json"), generated))
@example(DEEP)
@example(_athena_entry(SubmissionDateTime="2026-11-01T00:00:00"))  # found: a TypeError
@example(_athena_entry(CompletionDateTime="2026-11-01T00:00:00"))
def test_athena_query_history_is_accepted_or_rejected_by_its_rule(data: bytes) -> None:
    _athena_by_its_rule(data)


def test_an_athena_entry_without_a_time_zone_is_rejected(tmp_path: Path) -> None:
    code, output, database = _ingest(
        tmp_path, "athena", _athena_entry(SubmissionDateTime="2026-11-01T00:00:00")
    )
    assert code == 1
    assert "  1: Status.SubmissionDateTime (timezone_aware)" in output.splitlines()
    with WorkloadStore(database) as store:
        assert store.stored_query_execution_ids(ACCOUNT, REGION, ["q-001", "q-002"]) == {"q-002"}


# --- FOCUS -----------------------------------------------------------------------------


def _focus_by_its_rule(code: int, output: str, database: Path, lines: int) -> None:
    """Exit 0 stores the whole snapshot. Exit 1 (it does not reconcile) or 2 (nothing done)
    stores nothing (spec 05, #212)."""
    assert code in (0, 1, 2)
    if code == 1:
        assert output.startswith("focus: rejected: ")
    with Ledger(database) as ledger:
        scopes = ledger.scopes()
        if code == 0:
            (scope,) = scopes
            current = ledger.current_snapshot(scope)
            assert current is not None
            assert len(current.lines) == lines
        else:
            assert scopes == []
    _quotes_no_value(code, output)


FOCUS_FIXTURE = _fixture("focus", "export-2026-10.json")


@_in_a_new_folder
def _focus_export_by_its_rule(folder: Path, data: bytes) -> None:
    code, output, database = _ingest(folder, "focus", data)
    document = _document(data)
    if document == FOCUS_FIXTURE:
        assert code == 0
    if not isinstance(document, dict):  # not a JSON object: not an Export Snapshot
        assert code == 2
    _focus_by_its_rule(code, output, database, len(document["lines"]) if code == 0 else 0)


@FUZZ
@given(st.one_of(mutated(FIXTURES / "focus" / "export-2026-10.json"), generated))
@example(DEEP)
@example((FIXTURES / "focus" / "export-2026-10.json").read_bytes())
@example(codecs.BOM_UTF8 + (FIXTURES / "focus" / "export-2026-10.json").read_bytes())  # found
def test_focus_export_snapshots_are_accepted_or_rejected_by_their_rule(data: bytes) -> None:
    _focus_export_by_its_rule(data)


@st.composite
def damaged_deliveries(draw: st.DrawFn) -> tuple[str, bytes]:
    """Which file of a Delivery to damage, and its new bytes."""
    with tempfile.TemporaryDirectory() as folder:
        write_delivery(Path(folder))
        name = draw(st.sampled_from(sorted(path.name for path in Path(folder).iterdir())))
        path = Path(folder) / name
        if name.endswith(".json"):
            return name, draw(mutated(path))
        return name, draw(_damaged(path.read_bytes()))


FOCUS_MANIFEST = "example-focus-1-2-Manifest-FOCUS.json"


@FUZZ
@given(damaged_deliveries())
@example((FOCUS_MANIFEST, b"null"))  # found: a TypeError
@example((FOCUS_MANIFEST, b'{"Schema": {}}'))
@example((FOCUS_MANIFEST, b'{"Schema": {"FocusVersion": ["zz-fuzz-sentinel"]}}'))  # found
@example((FOCUS_MANIFEST, b"[" * 100_000))
@example(("example-focus-1-2-Manifest.json", b"[" * 100_000))
def test_damaged_deliveries_are_accepted_or_rejected_by_their_rule(
    damaged: tuple[str, bytes],
) -> None:
    name, data = damaged
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        manifest = write_delivery(root / "delivery")
        (root / "delivery" / name).write_bytes(data)
        database = root / "data-finops.sqlite"
        code, output, _ = _run(["ingest", "focus", str(manifest), "--db", str(database)], database)
        if name.endswith(".json") and not isinstance(_document(data), dict):
            assert code == 2  # a manifest that is not a JSON object: not a usable Delivery
        _focus_by_its_rule(code, output, database, 1)  # the Delivery's one row
