"""A Collection Run against the Deployment's database object (ADR 0026): download it, collect,
then write it back only over the version read, so no write is ever lost."""

from pathlib import Path

from data_finops.collection.run import (
    AwsClients,
    CollectionSettings,
    CollectionSummary,
    run_collection,
)
from data_finops.providers.aws import S3Writer, fetch_file, put_file

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
