"""Screen OpenLineage event files event by event (spec 04, spec 05 payload controls; #42).

Each event is size-checked, sorted by kind and validated against the OpenLineage 2-0-2
RunEvent schema, and its ParentRunFacet against 1-1-0, both bundled in this package and
matching the sha256 pins in `scripts/verify_upstream.py`. Rejected events are reported by
position and reason only: a reason names the failing field and rule, never the value.
"""

import json
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import ValidationError
from referencing import Registry, Resource

from data_finops.lineage.openlineage import RunEvent

MAX_EVENT_BYTES = 1024 * 1024
_UNSUPPORTED_KINDS = ("#/$defs/JobEvent", "#/$defs/DatasetEvent")


@dataclass(frozen=True)
class Rejection:
    position: int
    """1-based: the line in a JSON Lines file, the element in a JSON array."""
    reason: str


@dataclass(frozen=True)
class Screening:
    events: list[tuple[int, RunEvent]]
    rejected: list[Rejection]
    unsupported: int
    """JobEvents and DatasetEvents: valid OpenLineage, not stored in M2."""


def screen_events(path: Path) -> Screening:
    """Split an event file (JSON array or JSON Lines) into valid RunEvents and the rest."""
    events: list[tuple[int, RunEvent]] = []
    rejected: list[Rejection] = []
    unsupported = 0
    for position, item, size in _items(path, rejected):
        if size > MAX_EVENT_BYTES:
            rejected.append(Rejection(position, "too large"))
        elif _is_unsupported(item):
            unsupported += 1
        elif (reason := _invalid(item)) is not None:
            rejected.append(Rejection(position, reason))
        else:
            events.append((position, RunEvent.model_validate(item)))
    return Screening(events=events, rejected=rejected, unsupported=unsupported)


def _items(path: Path, rejected: list[Rejection]) -> list[tuple[int, Any, int]]:
    text = path.read_text(encoding="utf-8-sig")
    if text.lstrip().startswith("["):
        try:
            elements = json.loads(text)
        except json.JSONDecodeError:
            rejected.append(Rejection(1, "not valid JSON"))
            return []
        return [
            (position, item, len(json.dumps(item, separators=(",", ":")).encode("utf-8")))
            for position, item in enumerate(elements, start=1)
        ]
    items = []
    # Split on newlines only: U+2028 and similar may appear unescaped inside JSON strings.
    for position, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        size = len(line.encode("utf-8"))
        if size > MAX_EVENT_BYTES:
            items.append((position, None, size))
            continue
        try:
            items.append((position, json.loads(line), size))
        except json.JSONDecodeError:
            rejected.append(Rejection(position, "not valid JSON"))
    return items


def _is_unsupported(item: Any) -> bool:
    if not isinstance(item, dict) or "run" in item:
        return False
    return "dataset" in item or str(item.get("schemaURL", "")).endswith(_UNSUPPORTED_KINDS)


def _invalid(item: Any) -> str | None:
    run_event, parent_facet = _validators()
    if reason := _first_error("RunEvent", run_event, item):
        return reason
    parent = item["run"].get("facets", {}).get("parent")
    if parent is not None and (reason := _first_error("ParentRunFacet", parent_facet, parent)):
        return reason
    try:
        RunEvent.model_validate(item)
    except ValidationError:
        return "invalid RunEvent: does not match the product's RunEvent model"
    return None


def _first_error(kind: str, validator: Draft202012Validator, instance: Any) -> str | None:
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(map(str, e.absolute_path)))
    if not errors:
        return None
    error = errors[0]
    where = "/".join(map(str, error.absolute_path)) or "(event)"
    return f"invalid {kind}: {where} ({error.validator})"


@cache
def _validators() -> tuple[Draft202012Validator, Draft202012Validator]:
    schemas = files("data_finops.lineage") / "schemas"
    core = json.loads((schemas / "OpenLineage-2-0-2.json").read_text(encoding="utf-8"))
    parent = json.loads((schemas / "ParentRunFacet-1-1-0.json").read_text(encoding="utf-8"))
    registry: Registry = Registry().with_resources(
        [(s["$id"], Resource.from_contents(s)) for s in (core, parent)]
    )
    checker = Draft202012Validator.FORMAT_CHECKER
    return (
        Draft202012Validator(
            {"$ref": f"{core['$id']}#/$defs/RunEvent"}, registry=registry, format_checker=checker
        ),
        Draft202012Validator(
            {"$ref": f"{parent['$id']}#/$defs/ParentRunFacet"},
            registry=registry,
            format_checker=checker,
        ),
    )
