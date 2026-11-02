"""dbt `manifest.json` (v12) and `run_results.json` (v6), reading only what M2 needs.

Full validation against the pinned dbt schemas runs in `scripts/verify_upstream.py`.
"""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class _Artifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")


class ArtifactMetadata(_Artifact):
    dbt_schema_version: str
    invocation_id: str | None = None


class ManifestNode(_Artifact):
    unique_id: str
    resource_type: str
    name: str


class Manifest(_Artifact):
    metadata: ArtifactMetadata
    nodes: dict[str, ManifestNode]


class Timing(_Artifact):
    name: str
    started_at: datetime | None = None
    completed_at: datetime | None = None


class RunResult(_Artifact):
    unique_id: str
    status: str
    execution_time: float
    timing: list[Timing] = []


class RunResults(_Artifact):
    metadata: ArtifactMetadata
    results: list[RunResult]


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate_json(path.read_text(encoding="utf-8"))


def load_run_results(path: Path) -> RunResults:
    return RunResults.model_validate_json(path.read_text(encoding="utf-8"))
