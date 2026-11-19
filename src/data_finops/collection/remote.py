"""A Collection Run against the Deployment's database object (ADR 0026): download it, collect,
then write it back only over the version read, so no write is ever lost."""

from pathlib import Path

from data_finops.collection.run import (
    AwsClients,
    CollectionSettings,
    CollectionSummary,
    run_collection,
)
from data_finops.providers.aws import (
    S3Reader,
    S3Writer,
    WriteConflict,
    fetch_file,
    put_file,
    refresh_file,
)

__all__ = [
    "DATABASE_KEY",
    "CachedDatabase",
    "S3Reader",
    "S3Writer",
    "WriteConflict",
    "run_remote_collection",
]

DATABASE_KEY = "database/data-finops.sqlite"
"""Where the Deployment's database object lives in its bucket (spec 08)."""


def run_remote_collection(
    settings: CollectionSettings, *, clients: AwsClients, writer: S3Writer, scratch: Path
) -> CollectionSummary:
    """One Collection Run over the database object in `settings.bucket`. The stores are
    closed before the upload, so SQLite has checkpointed its WAL into the file. WriteConflict
    when another writer changed the object meanwhile: nothing is overwritten."""
    scratch.mkdir(parents=True, exist_ok=True)
    database = scratch / "data-finops.sqlite"
    etag = fetch_file(writer, settings.bucket, DATABASE_KEY, database)
    summary = run_collection(database, settings, clients=clients)
    put_file(writer, settings.bucket, DATABASE_KEY, database, etag=etag)
    return summary


class CachedDatabase:
    """A local copy of the database object, downloaded again only when its ETag changes: the
    API's copy, and the publisher's (#214)."""

    def __init__(self, s3: S3Reader, bucket: str, path: Path) -> None:
        self._s3, self._bucket, self.path = s3, bucket, path
        self._etag: str | None = None

    def refresh(self) -> bool:
        """Bring the copy up to date. False when there is no object yet, and so no copy."""
        self._etag = refresh_file(self._s3, self._bucket, DATABASE_KEY, self.path, self._etag)
        return self._etag is not None
