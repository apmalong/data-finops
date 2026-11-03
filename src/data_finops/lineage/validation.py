"""Screen OpenLineage event files event by event (spec 04, spec 05 payload controls; #42).

Each event is size-checked, sorted by kind and validated against the OpenLineage 2-0-2
schema for its kind (RunEvent, JobEvent or DatasetEvent), and a RunEvent's ParentRunFacet
against 1-1-0, all bundled in this package and matching the sha256 pins in
`scripts/verify_upstream.py`. Any `eventType` string is tolerated (spec 04). One bad event
never stops the file: every problem becomes a Rejection naming the failing field and rule,
never the value.
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing import Registry, Resource

from data_finops.lineage.openlineage import RunEvent

MAX_EVENT_BYTES = 1024 * 1024
"""Per event: the raw line in JSON Lines, the element's compact UTF-8 encoding in an array."""

_BOM = b"\xef\xbb\xbf"


class RejectionKind(StrEnum):
    NOT_UTF8 = "not valid UTF-8"
    NOT_JSON = "not valid JSON"
    TOO_LARGE = "too large"
    TOO_DEEP = "too deeply nested"
    INVALID_RUN_EVENT = "invalid RunEvent"
    INVALID_PARENT_FACET = "invalid ParentRunFacet"
    INVALID_JOB_EVENT = "invalid JobEvent"
    INVALID_DATASET_EVENT = "invalid DatasetEvent"
    UNSUPPORTED_JOB_EVENT = "unsupported JobEvent"
    UNSUPPORTED_DATASET_EVENT = "unsupported DatasetEvent"


@dataclass(frozen=True)
class Rejection:
    position: int
    """1-based: the line in a JSON Lines file, the element in a JSON array."""
    kind: RejectionKind
    detail: str = ""
    """The failing field and rule, e.g. `run/runId (format)`; never a value from the event."""

    @property
    def reason(self) -> str:
        return f"{self.kind}: {self.detail}" if self.detail else str(self.kind)


@dataclass(frozen=True)
class Screening:
    events: list[RunEvent]
    rejected: list[Rejection]
    """Invalid events, in file order."""
    unsupported: list[Rejection]
    """Valid JobEvents and DatasetEvents, in file order; not stored in M2."""


def screen_events(path: Path) -> Screening:
    """Split an event file (JSON array or JSON Lines) into valid RunEvents and the rest."""
    rejected: list[Rejection] = []
    return _screen(_items(path.read_bytes(), rejected), rejected)


def screen_event(data: bytes) -> Screening:
    """Screen one event, such as an HTTP request body, exactly as one file item (spec 04).

    The whole body is the event, at position 1: newlines inside it never split it.
    """
    rejected: list[Rejection] = []
    item = _parse(1, data.removeprefix(_BOM), rejected)
    return _screen([] if item is None else [item], rejected)


def _screen(items: list[tuple[int, Any]], rejected: list[Rejection]) -> Screening:
    events: list[RunEvent] = []
    unsupported: list[Rejection] = []
    for position, item in items:
        outcome = _check(position, item)
        if isinstance(outcome, RunEvent):
            events.append(outcome)
        elif outcome.kind in (
            RejectionKind.UNSUPPORTED_JOB_EVENT,
            RejectionKind.UNSUPPORTED_DATASET_EVENT,
        ):
            unsupported.append(outcome)
        else:
            rejected.append(outcome)
    rejected.sort(key=lambda r: r.position)
    return Screening(events=events, rejected=rejected, unsupported=unsupported)


def _items(data: bytes, rejected: list[Rejection]) -> list[tuple[int, Any]]:
    data = data.removeprefix(_BOM)
    if data.lstrip().startswith(b"["):
        try:
            elements = _loads(data.decode("utf-8"))
        except UnicodeDecodeError:
            rejected.append(Rejection(1, RejectionKind.NOT_UTF8))
            return []
        except RecursionError:
            rejected.append(Rejection(1, RejectionKind.TOO_DEEP))
            return []
        except ValueError:
            rejected.append(Rejection(1, RejectionKind.NOT_JSON))
            return []
        items = []
        for position, element in enumerate(elements, start=1):
            encoded = json.dumps(element, separators=(",", ":"), ensure_ascii=False)
            if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
                rejected.append(Rejection(position, RejectionKind.TOO_LARGE))
            else:
                items.append((position, element))
        return items
    items = []
    # Split on newlines only: U+2028 and similar may appear unescaped inside JSON strings.
    for position, line in enumerate(data.split(b"\n"), start=1):
        if line.strip() and (item := _parse(position, line, rejected)) is not None:
            items.append(item)
    return items


def _parse(position: int, raw: bytes, rejected: list[Rejection]) -> tuple[int, Any] | None:
    """One event's bytes as JSON, or None with its Rejection recorded."""
    if len(raw) > MAX_EVENT_BYTES:
        rejected.append(Rejection(position, RejectionKind.TOO_LARGE))
        return None
    try:
        return position, _loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        rejected.append(Rejection(position, RejectionKind.NOT_UTF8))
    except RecursionError:
        rejected.append(Rejection(position, RejectionKind.TOO_DEEP))
    except ValueError:
        rejected.append(Rejection(position, RejectionKind.NOT_JSON))
    return None


def _loads(text: str) -> Any:
    """Strict JSON: NaN and Infinity are not JSON and are refused."""
    return json.loads(text, parse_constant=_refuse_constant)


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def _check(position: int, item: Any) -> RunEvent | Rejection:
    validators = _validators()
    try:
        if isinstance(item, dict) and "run" not in item:
            if "dataset" in item:
                return _unsupported_or_invalid(position, item, "DatasetEvent")
            if str(item.get("schemaURL", "")).endswith("#/$defs/JobEvent"):
                return _unsupported_or_invalid(position, item, "JobEvent")
        if detail := _first_error(validators["RunEvent"], _tolerating_event_type(item)):
            return Rejection(position, RejectionKind.INVALID_RUN_EVENT, detail)
        parent = item["run"].get("facets", {}).get("parent")
        if parent is not None and (detail := _first_error(validators["ParentRunFacet"], parent)):
            return Rejection(position, RejectionKind.INVALID_PARENT_FACET, detail)
        if parent is not None and parent["run"]["runId"] == item["run"]["runId"]:
            return Rejection(
                position, RejectionKind.INVALID_PARENT_FACET, "run/runId (names the run itself)"
            )
        return RunEvent.model_validate(item)
    except RecursionError:
        return Rejection(position, RejectionKind.TOO_DEEP)
    except ValidationError:
        return Rejection(
            position, RejectionKind.INVALID_RUN_EVENT, "does not match the product's model"
        )


def _unsupported_or_invalid(position: int, item: dict[str, Any], kind: str) -> Rejection:
    if detail := _first_error(_validators()[kind], item):
        return Rejection(position, RejectionKind(f"invalid {kind}"), detail)
    return Rejection(position, RejectionKind(f"unsupported {kind}"))


def _tolerating_event_type(item: Any) -> Any:
    """The event as the schema should see it: an unknown or null `eventType` is tolerated."""
    if not isinstance(item, dict) or "eventType" not in item:
        return item
    event_type = item["eventType"]
    if event_type is None or (isinstance(event_type, str) and event_type not in _event_types()):
        return {k: v for k, v in item.items() if k != "eventType"}
    return item


def _first_error(validator: Draft202012Validator, instance: Any) -> str:
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(map(str, e.absolute_path)))
    if not errors:
        return ""
    error = errors[0]
    return f"{'/'.join(map(str, error.absolute_path)) or '(event)'} ({error.validator})"


@cache
def _schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    folder = files("data_finops.lineage") / "schemas"
    core = json.loads((folder / "OpenLineage-2-0-2.json").read_text(encoding="utf-8"))
    parent = json.loads((folder / "ParentRunFacet-1-1-0.json").read_text(encoding="utf-8"))
    return core, parent


@cache
def _event_types() -> frozenset[str]:
    core, _ = _schemas()
    return frozenset(core["$defs"]["RunEvent"]["allOf"][1]["properties"]["eventType"]["enum"])


@cache
def _validators() -> dict[str, Draft202012Validator]:
    core, parent = _schemas()
    registry: Registry = Registry().with_resources(
        [(s["$id"], Resource.from_contents(s)) for s in (core, parent)]
    )
    refs = {
        "RunEvent": f"{core['$id']}#/$defs/RunEvent",
        "JobEvent": f"{core['$id']}#/$defs/JobEvent",
        "DatasetEvent": f"{core['$id']}#/$defs/DatasetEvent",
        "ParentRunFacet": f"{parent['$id']}#/$defs/ParentRunFacet",
    }
    return {
        name: Draft202012Validator(
            {"$ref": ref}, registry=registry, format_checker=Draft202012Validator.FORMAT_CHECKER
        )
        for name, ref in refs.items()
    }
