"""One Collection Run: fetch every source, ingest it, then calculate (spec 08)."""

import json
import re
import tempfile
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, computed_field

from data_finops.billing.aws_data_exports import (
    SOURCE,
    DeliveryError,
    billing_period_end,
    billing_period_start,
    delivery_created,
    focus_manifest_name,
    is_manifest,
    load_delivery,
)
from data_finops.calculation import run_calculation
from data_finops.contracts.billing import FocusExport, SnapshotScope
from data_finops.dbt.artifacts import MAX_ARTIFACT_BYTES
from data_finops.ledger import ImportOutcome, Ledger, SnapshotConflictError
from data_finops.providers.aws import (
    UNFINISHED,
    AthenaReader,
    AwsSourceError,
    S3Object,
    S3Reader,
    TooLarge,
    batch_get_query_executions,
    download_object,
    list_folders,
    list_objects,
    list_query_execution_ids,
    read_object,
)
from data_finops.workload.store import ArtifactOutcome, InboxOutcome, WorkloadStore

DBT_INBOX = "inbox/dbt/"
OPENLINEAGE_INBOX = "inbox/openlineage/"
DBT_ARTIFACTS = ("manifest.json", "run_results.json")
"""What one invocation folder holds, in the order it is ingested."""
MAX_INBOX_FILE_BYTES = MAX_ARTIFACT_BYTES
"""The cap on one Inbox file, the same 512 MiB as on every other input file (spec 04); each
event within an OpenLineage file is still capped at 1 MiB by the screening it goes through."""

DEFAULT_LOOKBACK_PERIODS = 2
"""Billing Periods looked at per run by default: the current and the previous (spec 08)."""
MAX_LOOKBACK_PERIODS = 24
DEFAULT_HISTORY_PAGES = 200
"""ListQueryExecutions pages per workgroup per run by default (spec 08)."""
MAX_HISTORY_PAGES = 1000
"""The most pages a run may list per workgroup: 50,000 Query Executions (AGENTS.md: bound
query limits)."""

OK, REJECTED, FAILED = 0, 1, 2
"""Exit codes, as the command line's (spec 05, spec 08 §6)."""


@dataclass(frozen=True)
class CollectionSettings:
    bucket: str
    """The Deployment's bucket, holding the Inbox."""
    region: str
    account_id: str
    """The account the Athena workgroups are in; BatchGetQueryExecution does not say."""
    workgroups: tuple[str, ...]
    """The Athena workgroups whose history is collected (spec 08: never all of them)."""
    profile: str | None = None
    """An AWS profile for a run from a laptop; None inside Lambda."""
    max_history_pages: int = DEFAULT_HISTORY_PAGES
    """ListQueryExecutions pages read per workgroup per run, 50 IDs a page."""
    export_bucket: str = ""
    """The bucket AWS Data Exports delivers the FOCUS export to."""
    export_prefix: str = ""
    """The export's S3 path prefix, as configured in Data Exports (may be empty)."""
    export_name: str = ""
    """The export's name in Data Exports, which names its folder and manifests."""
    billing_lookback_periods: int = DEFAULT_LOOKBACK_PERIODS
    """How many of the most recent Billing Periods in the export each run looks at."""

    def metadata_prefix(self, folder: str = "") -> str:
        """Where AWS writes manifests: `<root>metadata/`, then `folder` (a Billing Period or
        a Delivery under it)."""
        return f"{self.export_root}metadata/" + (f"{folder}/" if folder else "")

    def data_prefix(self, delivery: str) -> str:
        """Where AWS writes a Delivery's data files: `<root>data/<period>/<delivery>/`."""
        return f"{self.export_root}data/{delivery}/"

    @property
    def export_root(self) -> str:
        """`<prefix>/<export>/`, under which AWS writes `data/` and `metadata/`."""
        return (
            "/".join(part.strip("/") for part in (self.export_prefix, self.export_name) if part)
            + "/"
        )

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
        unknown = sorted(set(values) - {setting.name for setting in fields(cls)})
        if unknown:
            raise ValueError(f"unknown setting {', '.join(unknown)}")
        for key in ("bucket", "region", "account_id", "workgroups", "export_bucket", "export_name"):
            if key not in values:
                raise ValueError(f"missing setting {key}")
        for key in ("bucket", "region", "account_id", "profile", "export_bucket", "export_name"):
            if key in values and (not isinstance(values[key], str) or not values[key]):
                raise ValueError(f"{key} must be a non-empty string")
        if not isinstance(values.get("export_prefix", ""), str):
            raise ValueError("export_prefix must be a string")
        if not (
            isinstance(values["account_id"], str) and re.fullmatch(r"\d{12}", values["account_id"])
        ):
            raise ValueError("account_id must be 12 digits")
        workgroups = values["workgroups"]
        if (
            not isinstance(workgroups, list)
            or not workgroups
            or not all(isinstance(w, str) and w for w in workgroups)
        ):
            raise ValueError("workgroups must be a non-empty list of names")
        return cls(
            **values
            | {
                "workgroups": tuple(workgroups),
                "max_history_pages": _whole_number(
                    values, "max_history_pages", DEFAULT_HISTORY_PAGES, MAX_HISTORY_PAGES
                ),
                "billing_lookback_periods": _whole_number(
                    values,
                    "billing_lookback_periods",
                    DEFAULT_LOOKBACK_PERIODS,
                    MAX_LOOKBACK_PERIODS,
                ),
            }
        )


def _whole_number(values: dict[str, Any], key: str, default: int, most: int) -> int:
    """The setting as a whole number from 1 to `most`, or `default` when it is absent."""
    value = values.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= most:
        raise ValueError(f"{key} must be a whole number from 1 to {most}")
    return value


class _Summary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RejectedItem(_Summary):
    item: str
    """The S3 key, with `#<position>` for one event of a file; never content."""
    reason: str


class SourceSummary(_Summary):
    stored: int = 0
    duplicates: int = 0
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


@dataclass(frozen=True)
class AwsClients:
    """The clients a Collection Run reads through, made by its caller (spec 08)."""

    s3: S3Reader
    athena: AthenaReader


def run_collection(
    db: Path, settings: CollectionSettings, *, clients: AwsClients
) -> CollectionSummary:
    """Collect every source into the database at `db`, then start one Calculation Run per
    Snapshot Scope on whatever was stored."""
    with tempfile.TemporaryDirectory(prefix="data-finops-collect-") as scratch:
        with WorkloadStore(db) as store:
            # Workload metadata first (spec 08 §1); links are derived when queried, so the
            # order is for readability, never correctness.
            warnings: list[str] = []
            dbt = _collect_dbt(store, clients.s3, settings.bucket, Path(scratch), warnings)
            openlineage = _collect_openlineage(
                store, clients.s3, settings.bucket, Path(scratch), warnings
            )
            athena = _collect_athena(store, clients.athena, settings, Path(scratch), warnings)
        with Ledger(db) as ledger:
            focus = _collect_focus(ledger, clients.s3, settings, Path(scratch), warnings)
    with Ledger(db) as ledger, WorkloadStore(db) as store:
        runs = [
            run_calculation(ledger, store, scope).calculation_run_id for scope in ledger.scopes()
        ]
    return CollectionSummary(
        sources={"dbt": dbt, "openlineage": openlineage, "athena": athena, "focus": focus},
        warnings=warnings,
        calculation_run_ids=runs,
    )


@dataclass
class _Tally:
    """One source's counts as a Collection Run goes."""

    stored: int = 0
    duplicates: int = 0
    rejected: list[RejectedItem] = field(default_factory=list)

    def summary(self, failed: str | None = None) -> SourceSummary:
        return SourceSummary(
            stored=self.stored, duplicates=self.duplicates, rejected=self.rejected, failed=failed
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
        store.record_inbox_object(obj.key, obj.etag, InboxOutcome.REJECTED)
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
                outcome = InboxOutcome.COLLECTED
                if result.outcome is ArtifactOutcome.REJECTED:
                    tally.rejected.append(RejectedItem(item=obj.key, reason=str(result.reason)))
                    outcome = InboxOutcome.REJECTED
                elif result.outcome is ArtifactOutcome.DUPLICATE:
                    tally.duplicates += 1
                else:  # stored, or a changed copy kept beside the first
                    tally.stored += 1
                store.record_inbox_object(obj.key, obj.etag, outcome)
    except AwsSourceError as error:
        return tally.summary(failed=str(error))
    return tally.summary()


def _collect_openlineage(
    store: WorkloadStore, s3: S3Reader, bucket: str, scratch: Path, warnings: list[str]
) -> SourceSummary:
    """Every OpenLineage event file in the Inbox not yet collected, screened as a file;
    events of a kind data-finops does not read are listed as warnings, not rejections."""
    tally = _Tally()
    try:
        for obj in list_objects(s3, bucket, OPENLINEAGE_INBOX):
            path = _fetch_new(store, s3, bucket, obj, scratch / "events.json", tally)
            if path is None:
                continue
            result = store.ingest_events(path)
            tally.stored += result.accepted  # changed copies are stored, so counted here
            tally.duplicates += result.duplicates
            tally.rejected += [
                RejectedItem(item=f"{obj.key}#{r.position}", reason=r.reason)
                for r in result.rejected
            ]
            warnings += [f"{obj.key}#{r.position}: {r.reason}" for r in result.unsupported]
            nothing_stored = result.accepted == result.duplicates == 0 and not result.unsupported
            outcome = (
                InboxOutcome.REJECTED
                if nothing_stored and result.rejected
                else InboxOutcome.COLLECTED
            )
            store.record_inbox_object(obj.key, obj.etag, outcome)
    except AwsSourceError as error:
        return tally.summary(failed=str(error))
    return tally.summary()


def _collect_athena(
    store: WorkloadStore,
    athena: AthenaReader,
    settings: CollectionSettings,
    scratch: Path,
    warnings: list[str],
) -> SourceSummary:
    """Each configured workgroup's Query Executions that are not stored yet."""
    tally = _Tally()
    try:
        for workgroup in settings.workgroups:
            listing = list_query_execution_ids(
                athena, workgroup, max_pages=settings.max_history_pages
            )
            if not listing.complete:
                warnings.append(
                    f"workgroup {workgroup}: history page cap of {settings.max_history_pages}"
                    " reached; older queries were not listed"
                )
            listed = list(listing.ids)
            stored = store.stored_query_execution_ids(settings.account_id, settings.region, listed)
            if (
                listed
                and listing.complete
                and not stored
                and store.workgroup_has_query_executions(
                    settings.account_id, settings.region, workgroup
                )
            ):
                # Athena keeps 45 days: nothing we hold is listed, so whatever ran between
                # the last query we hold and the oldest listed may have expired unread.
                warnings.append(
                    f"workgroup {workgroup}: no overlap with stored history;"
                    " queries older than Athena's 45-day history may be missing"
                )
            unseen = [query for query in listed if query not in stored]
            if not unseen:
                continue
            response = batch_get_query_executions(athena, unseen)
            # An unfinished query is not stored: its later report would be a changed copy,
            # flagged. Left unseen, the next run fetches it again (spec 08 §3).
            response["QueryExecutions"] = [
                entry
                for entry in response["QueryExecutions"]
                if entry.get("Status", {}).get("State") not in UNFINISHED
            ]
            path = scratch / "history.json"
            path.write_text(json.dumps(response), encoding="utf-8")
            result = store.ingest_query_history(
                path, account_id=settings.account_id, region=settings.region
            )
            if result.file_rejection is not None:
                # The response is built here, so a refused file is a fault, not bad input.
                raise AwsSourceError(f"history response refused: {result.file_rejection}")
            tally.stored += result.accepted
            tally.duplicates += result.duplicates
            written = response["QueryExecutions"]
            tally.rejected += [
                RejectedItem(item=f"{workgroup}/{_query_id(written, r.position)}", reason=r.detail)
                for r in result.rejected
            ]
            warnings += [
                f"{workgroup}/{entry.get('QueryExecutionId')}:"
                f" unprocessed ({entry.get('ErrorCode')})"
                for entry in response["UnprocessedQueryExecutionIds"]
            ]
    except AwsSourceError as error:
        return tally.summary(failed=str(error))
    return tally.summary()


def _query_id(entries: list[Any], position: int) -> str:
    """The query ID of the entry at a 1-based position, or the position when it has none."""
    query = entries[position - 1].get("QueryExecutionId")
    return query if isinstance(query, str) and query else f"#{position}"


MAX_DATA_FILE_BYTES = 2 * 1024**3
"""The cap on one FOCUS data file, checked against its listed size before download (Q48)."""
MAX_DELIVERY_BYTES = 8 * 1024**3
"""The cap on one Delivery's data files together, within Lambda's 10 GiB `/tmp` (Q48)."""
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
"""The cap on a Delivery's manifests, which list files and columns only."""


class _RejectedDelivery(ValueError):
    """A Delivery refused whole; the message names the problem, never a URI or content."""


def _collect_focus(
    ledger: Ledger, s3: S3Reader, settings: CollectionSettings, scratch: Path, warnings: list[str]
) -> SourceSummary:
    """Every complete Delivery of the most recent Billing Periods that is newer than the
    current Export Snapshot of its Snapshot Scope and not yet collected, oldest first, each
    read by the existing Delivery reader (spec 08 §2)."""
    tally = _Tally()
    try:
        periods = [
            (name, start)
            for name in list_folders(s3, settings.export_bucket, settings.metadata_prefix())
            if (start := billing_period_start(name)) is not None
        ][-settings.billing_lookback_periods :]
        for period, start in periods:
            for delivery, manifests in _deliveries_to_collect(
                ledger, s3, settings, period, start, warnings
            ):
                outcome = _collect_delivery(
                    ledger, s3, settings, delivery, manifests, scratch, tally
                )
                ledger.record_collected_delivery(settings.metadata_prefix(delivery), outcome)
    except AwsSourceError as error:
        return tally.summary(failed=str(error))
    return tally.summary()


def _deliveries_to_collect(
    ledger: Ledger,
    s3: S3Reader,
    settings: CollectionSettings,
    period: str,
    start: datetime,
    warnings: list[str],
) -> list[tuple[str, list[S3Object]]]:
    """The period's Deliveries to collect, oldest first, each `<period>/<CreatedAt>-<id>` with
    its two manifests: complete (both manifests written), not yet collected, and newer than
    the current Export Snapshot of this account's Snapshot Scope (Q2: one account)."""
    scope = SnapshotScope(
        billing_account_id=settings.account_id,
        billing_period_start=start,
        billing_period_end=billing_period_end(start),
        source=SOURCE,
    )
    current = ledger.current_snapshot(scope)
    published = None if current is None else current.snapshot.published_at
    folders: dict[str, dict[str, S3Object]] = defaultdict(dict)
    for obj in list_objects(s3, settings.export_bucket, settings.metadata_prefix(period)):
        folder, _, name = obj.key.removeprefix(settings.metadata_prefix(period)).rpartition("/")
        folders[folder][name] = obj
    if "" in folders:  # files straight under the period: Data Exports' overwrite mode
        warnings.append(
            f"{period}: an overwrite-mode Delivery is refused; set the export to create new files"
        )
        return []
    chosen = []
    for folder, files in folders.items():
        delivery = f"{period}/{folder}"
        created = delivery_created(folder)
        if created is None:
            warnings.append(f"{delivery}: not a Delivery folder; ignored")
            continue
        manifest = next((name for name in files if is_manifest(Path(name))), None)
        if manifest is None or focus_manifest_name(manifest) not in files:
            continue  # still being written (spec 08 §2): left for a later run
        if published is not None and created <= published:
            continue  # superseded before it was seen: never downloaded
        if ledger.delivery_collected(settings.metadata_prefix(delivery)):
            continue
        chosen.append((created, delivery, [files[manifest], files[focus_manifest_name(manifest)]]))
    return [(delivery, manifests) for _, delivery, manifests in sorted(chosen)]


def _collect_delivery(
    ledger: Ledger,
    s3: S3Reader,
    settings: CollectionSettings,
    delivery: str,
    manifests: list[S3Object],
    scratch: Path,
    tally: _Tally,
) -> str:
    """Download one Delivery and import it; the outcome to record in the watermark."""
    try:
        snapshot = _download_delivery(s3, settings, delivery, manifests, scratch)
        result = ledger.import_snapshot(snapshot)
    except (_RejectedDelivery, DeliveryError, SnapshotConflictError, TooLarge) as error:
        tally.rejected.append(RejectedItem(item=delivery, reason=_reason(error)))
        return "rejected"
    if result.outcome is ImportOutcome.REPLAY:
        tally.duplicates += 1
    else:  # new, or superseded: stored either way
        tally.stored += 1
    return result.outcome.value


def _reason(error: Exception) -> str:
    if isinstance(error, TooLarge):
        return "a file over its cap"
    if isinstance(error, SnapshotConflictError):
        return "its Export Snapshot is stored with other content"
    return str(error)


def _download_delivery(
    s3: S3Reader,
    settings: CollectionSettings,
    delivery: str,
    manifests: list[S3Object],
    scratch: Path,
) -> FocusExport:
    """The Delivery `delivery` (`billing_period=…/<CreatedAt>-<executionId>`), downloaded to a
    scratch folder of its own and read as one Export Snapshot. Only its two manifests and the
    data files the manifest lists are fetched, and only from this Delivery's folder (Q47)."""
    local = scratch / "delivery"
    if local.exists():
        for old in local.iterdir():
            old.unlink()
    local.mkdir(exist_ok=True)
    for obj in manifests:
        download_object(
            s3, settings.export_bucket, obj, local / obj.key.rpartition("/")[2], MAX_MANIFEST_BYTES
        )
    manifest = next(path for path in local.iterdir() if is_manifest(path))
    try:
        uris = json.loads(manifest.read_text(encoding="utf-8")).get("dataFiles") or []
    except (ValueError, AttributeError):
        raise _RejectedDelivery("the manifest is not valid JSON") from None
    here = f"s3://{settings.export_bucket}/{settings.data_prefix(delivery)}"
    if not all(isinstance(uri, str) and uri.startswith(here) for uri in uris):
        raise _RejectedDelivery("the manifest names a data file outside the export")
    names = {uri.removeprefix(here) for uri in uris}
    listed = {
        obj.key.rpartition("/")[2]: obj
        for obj in list_objects(s3, settings.export_bucket, settings.data_prefix(delivery))
    }
    missing = names - set(listed)
    if missing:
        raise _RejectedDelivery(f"{len(missing)} data file(s) the manifest lists are missing")
    if sum(listed[name].size for name in names) > MAX_DELIVERY_BYTES:
        raise _RejectedDelivery("the Delivery's data files exceed its cap together")
    for name in sorted(names):
        download_object(s3, settings.export_bucket, listed[name], local / name, MAX_DATA_FILE_BYTES)
    return load_delivery(manifest)
