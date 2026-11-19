"""OpenLineage RunEvents (spec 2-0-2). Facets are kept verbatim, including unknown ones."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow", hide_input_in_errors=True)


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
