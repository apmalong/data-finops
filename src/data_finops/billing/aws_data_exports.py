"""One AWS Data Exports Delivery of a FOCUS 1.2 export, read from its downloaded files (#12).

AWS writes each Delivery (an export execution) to S3 as

    <prefix>/<export>/data/billing_period=YYYY-MM/<CreatedAt>-<executionId>/<data files>
    <prefix>/<export>/metadata/billing_period=YYYY-MM/<CreatedAt>-<executionId>/
        <export>-Manifest.json         executionId, exportArn, columns, dataFiles (S3 URIs)
        <export>-Manifest-FOCUS.json   DataGenerator, Schema.FocusVersion

Neither manifest carries a publication time, so `published_at` (ADR 0020 amendment) is
the execution's creation time, which AWS writes into the Delivery's S3 folder name; it
equals the `CreatedAt` that Data Exports' ListExecutions reports for the execution
(checked against the real 2026-11 Delivery). It is read from the manifest's own
`dataFiles` URIs, not from local paths: the folder name holds colons, which Windows
cannot store, so a download's local layout is the operator's. Only the files the
manifest lists are read, as AWS recommends.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from data_finops.billing.focus import billing_accounts, load_parquet_export
from data_finops.contracts.billing import ExportSnapshot, FocusExport

SOURCE = "aws-data-exports:FOCUS_1_2_AWS"
FOCUS_VERSION = "1.2"
_MANIFEST_SUFFIX = "-Manifest.json"
_FOCUS_MANIFEST_SUFFIX = "-Manifest-FOCUS.json"
_PERIOD = re.compile(r"billing_period=(?P<year>\d{4})-(?P<month>\d{2})")
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_EXECUTION_FOLDER = re.compile(
    rf"(?P<created>\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}:\d{{2}}:\d{{2}}(?:\.\d+)?Z)-(?P<execution>{_UUID})"
)


class DeliveryError(ValueError):
    """A Delivery not laid out as AWS writes it. Messages name files, never contents or
    bucket URIs, so a caller may show them."""


@dataclass(frozen=True)
class _DeliveryFolder:
    """What a Delivery's S3 folders name: its Billing Period, creation and execution."""

    period_start: datetime
    created: datetime
    execution: str

    @property
    def period_end(self) -> datetime:
        start = self.period_start
        return datetime(start.year + start.month // 12, start.month % 12 + 1, 1, tzinfo=UTC)


def is_manifest(path: Path) -> bool:
    """Whether `path` names a Delivery's `<export>-Manifest.json`."""
    return path.name.endswith(_MANIFEST_SUFFIX)


def load_delivery(manifest: Path, data_dir: Path | None = None) -> FocusExport:
    """Read the Delivery described by `manifest` (`<export>-Manifest.json`) as one Export
    Snapshot. Its `<export>-Manifest-FOCUS.json` lies beside it; the data files it lists
    are found by name in `data_dir`, by default the manifest's own folder.

    Raises DeliveryError when the Delivery is not as AWS writes it: a data file outside
    `data/billing_period=YYYY-MM/<CreatedAt>-<executionId>/`, files from more than one
    execution or one other than the manifest's, no data files, a listed file that was
    not downloaded, a FOCUS release other than the pinned one, or data files naming
    more than one billing account. ValueError when lines differ from the snapshot.
    """
    content = json.loads(manifest.read_text(encoding="utf-8"))
    focus_version = _focus_version(manifest)
    if focus_version != FOCUS_VERSION:
        raise DeliveryError(f"FOCUS {focus_version}, but {FOCUS_VERSION} is pinned (ADR 0015)")
    uris = content.get("dataFiles") or []
    if not uris:
        raise DeliveryError("the manifest lists no data files")
    folders = {_delivery_folder(uri) for uri in uris}
    if len(folders) != 1:
        raise DeliveryError(f"the manifest lists files from {len(folders)} deliveries")
    (delivery,) = folders
    if delivery.execution != content.get("executionId"):
        raise DeliveryError(f"files from execution {delivery.execution}, not the manifest's")

    local_folder = data_dir if data_dir is not None else manifest.parent
    files = [_downloaded(local_folder, uri) for uri in uris]
    account = _billing_account(files)
    snapshot = ExportSnapshot(
        snapshot_id=f"aws-focus-{account}-{delivery.period_start:%Y-%m}-{delivery.execution}",
        source=SOURCE,
        focus_version=focus_version,
        billing_account_id=account,
        billing_period_start=delivery.period_start,
        billing_period_end=delivery.period_end,
        published_at=delivery.created,
        files=[],
    )
    return load_parquet_export(files, snapshot)


def _focus_version(manifest: Path) -> str:
    name = manifest.name.removesuffix(_MANIFEST_SUFFIX) + _FOCUS_MANIFEST_SUFFIX
    path = manifest.parent / name
    if not path.is_file():
        raise DeliveryError(f"{name} is not beside the manifest")
    return str(json.loads(path.read_text(encoding="utf-8"))["Schema"]["FocusVersion"])


def _delivery_folder(uri: str) -> _DeliveryFolder:
    parts = PurePosixPath(uri.removeprefix("s3://")).parts
    period = _PERIOD.fullmatch(parts[-3]) if len(parts) >= 4 and parts[-4] == "data" else None
    folder = _EXECUTION_FOLDER.fullmatch(parts[-2]) if len(parts) >= 2 else None
    if period is None or folder is None:
        raise DeliveryError(
            f"{PurePosixPath(uri).name} is not in a Delivery folder "
            "(data/billing_period=YYYY-MM/<CreatedAt>-<executionId>/)"
        )
    return _DeliveryFolder(
        period_start=datetime(int(period["year"]), int(period["month"]), 1, tzinfo=UTC),
        created=datetime.fromisoformat(folder["created"]),
        execution=folder["execution"],
    )


def _downloaded(folder: Path, uri: str) -> Path:
    local = folder / PurePosixPath(uri).name
    if not local.is_file():
        raise DeliveryError(f"{local.name} is listed in the manifest but not downloaded")
    return local


def _billing_account(files: list[Path]) -> str:
    accounts = billing_accounts(files)
    if len(accounts) != 1:
        raise DeliveryError(f"a Delivery bills one account, found {len(accounts)}")
    return accounts.pop()
