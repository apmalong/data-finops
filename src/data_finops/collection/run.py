"""One Collection Run: fetch every source, ingest it, then calculate (spec 08)."""

import tempfile
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, computed_field

from data_finops.calculation import run_calculation
from data_finops.ledger import Ledger
from data_finops.providers.aws import (
    AwsSourceError,
    S3Object,
    S3Reader,
    TooLarge,
    list_objects,
    read_object,
)
from data_finops.workload.store import ArtifactOutcome, WorkloadStore

DBT_INBOX = "inbox/dbt/"
OPENLINEAGE_INBOX = "inbox/openlineage/"
DBT_ARTIFACTS = ("manifest.json", "run_results.json")
"""What one invocation folder holds, in the order it is ingested."""
MAX_INBOX_FILE_BYTES = 512 * 1024 * 1024
"""The cap on one Inbox file, as on every other input file (spec 04); each event within an
OpenLineage file is still capped at 1 MiB by the screening it goes through."""

OK, REJECTED, FAILED = 0, 1, 2
"""Exit codes, as the command line's (spec 05, spec 08 §6)."""


@dataclass(frozen=True)
class CollectionSettings:
    bucket: str
    """The Deployment's bucket, holding the Inbox."""
    region: str
    profile: str | None = None
    """An AWS profile for a run from a laptop; None inside Lambda."""

    @classmethod
    def from_toml(cls, path: Path) -> "CollectionSettings":
        """The settings file `data-finops collect` reads (spec 08 §9). ValueError names the
        setting at fault, never its value; an unknown setting is refused, so a typo cannot
        silently do nothing."""
        with path.open("rb") as file:
            try:
                values = tomllib.load(file)
            except tomllib.TOMLDecodeError:
                raise ValueError("the settings file is not valid TOML") from None
        unknown = sorted(set(values) - set(_SETTINGS))
        if unknown:
            raise ValueError(f"unknown setting {', '.join(unknown)}")
        for key in ("bucket", "region"):
            if key not in values:
                raise ValueError(f"missing setting {key}")
        for key, value in values.items():
            if not isinstance(value, str) or not value:
                raise ValueError(f"{key} must be a non-empty string")
        return cls(**values)


_SETTINGS = ("bucket", "region", "profile")
"""The keys of the settings file, as the fields of CollectionSettings."""


class _Summary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RejectedItem(_Summary):
    item: str
    """The S3 key, with `#<position>` for one event of a file; never content."""
    reason: str


class SourceSummary(_Summary):
    stored: int = 0
    duplicates: int = 0
    unsupported: int = 0
    rejected: list[RejectedItem] = []
    failed: str | None = None
    """Why the source could not be read (an AWS error code), or None."""


class CollectionSummary(_Summary):
    """What one Collection Run did, as its one JSON log line (spec 08 §8)."""

    sources: dict[str, SourceSummary]
    warnings: list[str] = []
    calculation_run_ids: list[str] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def exit_code(self) -> int:
        if any(source.failed for source in self.sources.values()):
            return FAILED
        if any(source.rejected for source in self.sources.values()):
            return REJECTED
        return OK


def run_collection(db: Path, settings: CollectionSettings, *, s3: S3Reader) -> CollectionSummary:
    """Collect every source into the database at `db`, then start one Calculation Run per
    Snapshot Scope on whatever was stored."""
    with tempfile.TemporaryDirectory(prefix="data-finops-collect-") as scratch:
        with WorkloadStore(db) as store:
            # Workload metadata first (spec 08 §1); links are derived when queried, so the
            # order is for readability, never correctness.
            warnings: list[str] = []
            dbt = _collect_dbt(store, s3, settings.bucket, Path(scratch), warnings)
            openlineage = _collect_openlineage(store, s3, settings.bucket, Path(scratch))
    with Ledger(db) as ledger, WorkloadStore(db) as store:
        runs = [
            run_calculation(ledger, store, scope).calculation_run_id for scope in ledger.scopes()
        ]
    return CollectionSummary(
        sources={"dbt": dbt, "openlineage": openlineage},
        warnings=warnings,
        calculation_run_ids=runs,
    )


@dataclass
class _Tally:
    """One source's counts as a Collection Run goes."""

    stored: int = 0
    duplicates: int = 0
    unsupported: int = 0
    rejected: list[RejectedItem] = field(default_factory=list)

    def summary(self, failed: str | None = None) -> SourceSummary:
        return SourceSummary(
            stored=self.stored,
            duplicates=self.duplicates,
            unsupported=self.unsupported,
            rejected=self.rejected,
            failed=failed,
        )


def _fetch_new(
    store: WorkloadStore, s3: S3Reader, bucket: str, obj: S3Object, path: Path, tally: _Tally
) -> Path | None:
    """The Inbox object's content written to `path`, or None when this version was already
    collected or is over the cap (then rejected by key and recorded, never downloaded)."""
    if store.inbox_object_collected(obj.key, obj.etag):
        return None
    try:
        path.write_bytes(read_object(s3, bucket, obj, MAX_INBOX_FILE_BYTES))
    except TooLarge:
        tally.rejected.append(RejectedItem(item=obj.key, reason="too large"))
        store.record_inbox_object(obj.key, obj.etag, "rejected")
        return None
    return path


def _collect_dbt(
    store: WorkloadStore, s3: S3Reader, bucket: str, scratch: Path, warnings: list[str]
) -> SourceSummary:
    """Each invocation folder's manifest and run results, once both have arrived; a folder
    still missing one is reported and left for a later run."""
    tally = _Tally()
    try:
        folders: dict[str, dict[str, S3Object]] = defaultdict(dict)
        for obj in list_objects(s3, bucket, DBT_INBOX):
            folder, _, name = obj.key.rpartition("/")
            folders[folder][name] = obj
        for folder, files in folders.items():
            missing = [name for name in DBT_ARTIFACTS if name not in files]
            if missing:
                warnings.append(f"{folder}/: waiting for {', '.join(missing)}")
                continue
            for name in DBT_ARTIFACTS:
                obj = files[name]
                path = _fetch_new(store, s3, bucket, obj, scratch / name, tally)
                if path is None:
                    continue
                result = store.ingest_dbt_artifact(path)
                if result.outcome is ArtifactOutcome.REJECTED:
                    tally.rejected.append(RejectedItem(item=obj.key, reason=str(result.reason)))
                elif result.outcome is ArtifactOutcome.DUPLICATE:
                    tally.duplicates += 1
                else:
                    tally.stored += 1
                store.record_inbox_object(obj.key, obj.etag, result.outcome.value)
    except AwsSourceError as error:
        return tally.summary(failed=str(error))
    return tally.summary()


def _collect_openlineage(
    store: WorkloadStore, s3: S3Reader, bucket: str, scratch: Path
) -> SourceSummary:
    """Every OpenLineage event file in the Inbox not yet collected, screened as a file."""
    tally = _Tally()
    try:
        for obj in list_objects(s3, bucket, OPENLINEAGE_INBOX):
            path = _fetch_new(store, s3, bucket, obj, scratch / "events.json", tally)
            if path is None:
                continue
            result = store.ingest_events(path)
            tally.stored += result.accepted + result.changed_copies
            tally.duplicates += result.duplicates
            tally.unsupported += len(result.unsupported)
            tally.rejected += [
                RejectedItem(item=f"{obj.key}#{r.position}", reason=r.reason)
                for r in result.rejected
            ]
            store.record_inbox_object(obj.key, obj.etag, "collected")
    except AwsSourceError as error:
        return tally.summary(failed=str(error))
    return tally.summary()
