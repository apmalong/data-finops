"""One AWS Data Exports delivery of a FOCUS 1.2 export, read from its downloaded files (#12).

AWS writes each delivery (an export execution) to S3 as

    <prefix>/<export>/data/billing_period=YYYY-MM/<CreatedAt>-<executionId>/<data files>
    <prefix>/<export>/metadata/billing_period=YYYY-MM/<CreatedAt>-<executionId>/
        <export>-Manifest.json         executionId, exportArn, columns, dataFiles (S3 URIs)
        <export>-Manifest-FOCUS.json   DataGenerator, Schema.FocusVersion

Neither manifest carries a publication time, so `published_at` (ADR 0020) is the
execution's creation time, which AWS writes into the delivery's S3 folder name; it
equals the `CreatedAt` that Data Exports' ListExecutions reports for the execution
(checked against the real 2026-11 delivery). It is read from the manifest's own
`dataFiles` URIs, not from local paths: the folder name holds colons, which Windows
cannot store, so a download's local layout is the operator's. Only the files the
manifest lists are read, as AWS recommends.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from data_finops.billing.focus import billing_accounts, load_parquet_export
from data_finops.contracts.billing import ExportSnapshot, FocusExport

SOURCE = "aws-data-exports:FOCUS_1_2_AWS"
FOCUS_VERSION = "1.2"
MANIFEST_SUFFIX = "-Manifest.json"
_FOCUS_MANIFEST_SUFFIX = "-Manifest-FOCUS.json"
_PERIOD = re.compile(r"billing_period=(\d{4})-(\d{2})")
_EXECUTION_FOLDER = re.compile(
    r"(?P<created>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)-(?P<execution>[0-9a-f-]{36})"
)


def load_delivery(manifest: Path, data_dir: Path | None = None) -> FocusExport:
    """Read the delivery described by `manifest` (`<export>-Manifest.json`) as one Export
    Snapshot. Its `<export>-Manifest-FOCUS.json` lies beside it; the data files it lists
    are found by name in `data_dir`, by default the manifest's own folder.

    Raises ValueError when the delivery is not as AWS writes it: a data file outside
    `data/billing_period=YYYY-MM/<CreatedAt>-<executionId>/`, files from more than one
    execution or one other than the manifest's, no data files, a listed file that was
    not downloaded, a FOCUS release other than the pinned one, or lines whose billing
    account or Billing Period differ.
    """
    content = json.loads(manifest.read_text(encoding="utf-8"))
    focus_version = _focus_version(manifest)
    if focus_version != FOCUS_VERSION:
        raise ValueError(
            f"{manifest}: FOCUS {focus_version}, but {FOCUS_VERSION} is pinned (ADR 0015)"
        )
    uris = content.get("dataFiles") or []
    if not uris:
        raise ValueError(f"{manifest}: lists no data files")
    folders = {_delivery_folder(uri) for uri in uris}
    if len(folders) != 1:
        raise ValueError(f"{manifest}: lists files from {len(folders)} deliveries")
    ((year, month, created, execution),) = folders
    if execution != content.get("executionId"):
        raise ValueError(f"{manifest}: files from execution {execution}, not the manifest's")

    folder = data_dir if data_dir is not None else manifest.parent
    files = [_downloaded(folder, uri) for uri in uris]
    account = _billing_account(files)
    snapshot = ExportSnapshot(
        snapshot_id=f"aws-focus-{account}-{year:04d}-{month:02d}-{execution}",
        source=SOURCE,
        focus_version=focus_version,
        billing_account_id=account,
        billing_period_start=datetime(year, month, 1, tzinfo=UTC),
        billing_period_end=datetime(year + month // 12, month % 12 + 1, 1, tzinfo=UTC),
        published_at=created,
        files=[],
    )
    return load_parquet_export(files, snapshot)


def _focus_version(manifest: Path) -> str:
    name = manifest.name.removesuffix(MANIFEST_SUFFIX) + _FOCUS_MANIFEST_SUFFIX
    focus = json.loads((manifest.parent / name).read_text(encoding="utf-8"))
    return str(focus["Schema"]["FocusVersion"])


def _delivery_folder(uri: str) -> tuple[int, int, datetime, str]:
    """Billing period, creation time and execution ID named by a data file's S3 folders."""
    parts = PurePosixPath(uri.removeprefix("s3://")).parts
    period = _PERIOD.fullmatch(parts[-3]) if len(parts) >= 4 and parts[-4] == "data" else None
    folder = _EXECUTION_FOLDER.fullmatch(parts[-2]) if len(parts) >= 2 else None
    if period is None or folder is None:
        raise ValueError(
            f"{uri}: not in an AWS Data Exports delivery folder "
            "(data/billing_period=YYYY-MM/<CreatedAt>-<executionId>/)"
        )
    created = datetime.fromisoformat(folder["created"])
    return int(period[1]), int(period[2]), created, folder["execution"]


def _downloaded(folder: Path, uri: str) -> Path:
    local = folder / PurePosixPath(uri).name
    if not local.is_file():
        raise ValueError(f"{local.name}: listed in the manifest but not in {folder}")
    return local


def _billing_account(files: list[Path]) -> str:
    accounts = billing_accounts(files)
    if len(accounts) != 1:
        raise ValueError(f"a delivery bills one account, found {len(accounts)}")
    return accounts.pop()
