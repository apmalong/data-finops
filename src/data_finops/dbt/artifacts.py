"""dbt `manifest.json` (v12) and `run_results.json` (v6), reading only what M2 needs.

Full validation against the pinned dbt schemas runs in `scripts/verify_upstream.py`.
`screen_run_results` is the ingest path: it never raises on bad content, and its reasons
come from a fixed set, so nothing from the file is echoed.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_DBT_SCHEMA = re.compile(r"https://schemas\.getdbt\.com/dbt/(manifest|run-results)/v(\d+)\.json")


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
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None


class RunResult(_Artifact):
    unique_id: str
    status: str
    execution_time: float
    timing: list[Timing] = []


class RunResults(_Artifact):
    metadata: ArtifactMetadata
    results: list[RunResult]


class ArtifactKind(StrEnum):
    RUN_RESULTS = "run_results"
    MANIFEST = "manifest"


@dataclass(frozen=True)
class ScreenedRunResults:
    results: RunResults
    invocation_id: str
    digest: str
    """SHA-256 of the file's bytes: with the invocation, the artifact's identity."""


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate_json(path.read_text(encoding="utf-8"))


def load_run_results(path: Path) -> RunResults:
    return RunResults.model_validate_json(path.read_text(encoding="utf-8"))


def screen_run_results(path: Path) -> ScreenedRunResults | str:
    """The run-results file if it is acceptable, otherwise the reason it is not.

    Accepts run-results v6 only, at most MAX_ARTIFACT_BYTES, with an invocation ID, aware
    timestamps and each node at most once. Extra fields are allowed (OQ-23).
    """
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        return "too large"
    data = path.read_bytes()
    try:
        document = json.loads(data.decode("utf-8-sig"))
        version = document["metadata"]["dbt_schema_version"]
    except (ValueError, KeyError, TypeError, RecursionError):
        return "not a dbt artifact"
    known = _DBT_SCHEMA.fullmatch(version) if isinstance(version, str) else None
    if known is None:
        return "not a dbt artifact"
    if known.groups() != ("run-results", "6"):
        return f"unsupported {known[1]} v{known[2]}"
    try:
        results = RunResults.model_validate(document)
    except (ValidationError, RecursionError):
        return "does not match run-results v6"
    if not results.metadata.invocation_id:
        return "no invocation_id"
    nodes = [result.unique_id for result in results.results]
    if len(nodes) != len(set(nodes)):
        return "a node appears more than once"
    return ScreenedRunResults(
        results=results,
        invocation_id=results.metadata.invocation_id,
        digest=hashlib.sha256(data).hexdigest(),
    )
