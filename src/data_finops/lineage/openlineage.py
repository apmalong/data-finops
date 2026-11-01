"""OpenLineage RunEvents (spec 2-0-2). Facets are kept verbatim, including unknown ones."""

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")


class Run(_Event):
    runId: str
    facets: dict[str, Any] = {}


class Job(_Event):
    namespace: str
    name: str


class Dataset(_Event):
    namespace: str
    name: str


class RunEvent(_Event):
    eventType: str | None = None
    eventTime: datetime
    producer: str
    schemaURL: str
    run: Run
    job: Job
    inputs: list[Dataset] = []
    outputs: list[Dataset] = []


_EVENTS = TypeAdapter(list[RunEvent])


def load_events(path: Path) -> list[RunEvent]:
    """Events as a JSON array, or JSON Lines (one per line, as dbt-ol's file transport writes)."""
    text = path.read_text(encoding="utf-8-sig")
    if text.lstrip().startswith("["):
        return _EVENTS.validate_json(text)
    events = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            events.append(RunEvent.model_validate_json(line))
        except ValidationError as error:
            raise ValueError(f"{path}: line {number}: {error}") from error
    return events
