"""Listing and reading S3 objects for a Collection Run (spec 08, ADR 0027)."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from data_finops.providers.aws.calls import call

MAX_KEYS = 1000
"""S3's own page limit for ListObjectsV2."""


class S3Reader(Protocol):
    """The two S3 calls a collector makes, as boto3's client offers them (keyword arguments
    and response dictionaries named as in the S3 API)."""

    def list_objects_v2(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_object(self, **kwargs: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class S3Object:
    key: str
    etag: str
    """As S3 returns it, quotes included: the version a Collection Watermark records."""
    size: int


class TooLarge(ValueError):
    """The object is larger than its reader accepts; nothing was downloaded."""


def list_objects(s3: S3Reader, bucket: str, prefix: str) -> Iterator[S3Object]:
    """Every object under `prefix`, in S3's key order, across pages."""
    params: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
    while True:
        page = call(s3.list_objects_v2, **params)
        for entry in page.get("Contents", ()):
            yield S3Object(key=entry["Key"], etag=entry["ETag"], size=entry["Size"])
        if not page.get("IsTruncated"):
            return
        params["ContinuationToken"] = page["NextContinuationToken"]


def list_folders(s3: S3Reader, bucket: str, prefix: str) -> list[str]:
    """The names of the 'folders' directly under `prefix` (S3 common prefixes), sorted."""
    params: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix, "Delimiter": "/"}
    names: list[str] = []
    while True:
        page = call(s3.list_objects_v2, **params)
        names += [
            entry["Prefix"][len(prefix) :].rstrip("/") for entry in page.get("CommonPrefixes", ())
        ]
        if not page.get("IsTruncated"):
            return sorted(names)
        params["ContinuationToken"] = page["NextContinuationToken"]


def download_object(s3: S3Reader, bucket: str, obj: S3Object, path: Path, max_bytes: int) -> None:
    """Write the object at its listed version to `path` in chunks, refused before any download
    when the listing says it exceeds `max_bytes`, and stopped if the body does."""
    if obj.size > max_bytes:
        raise TooLarge(obj.key)
    body = call(s3.get_object, Bucket=bucket, Key=obj.key, IfMatch=obj.etag)["Body"]
    written = 0
    with path.open("wb") as file:
        while chunk := body.read(_CHUNK):
            written += len(chunk)
            if written > max_bytes:
                raise TooLarge(obj.key)
            file.write(chunk)


_CHUNK = 8 * 1024 * 1024


def read_object(s3: S3Reader, bucket: str, obj: S3Object, max_bytes: int) -> bytes:
    """The object's content at the listed version (`If-Match` its ETag), refused before any
    download when the listing says it exceeds `max_bytes`."""
    if obj.size > max_bytes:
        raise TooLarge(obj.key)
    response = call(s3.get_object, Bucket=bucket, Key=obj.key, IfMatch=obj.etag)
    data: bytes = response["Body"].read(max_bytes + 1)
    if len(data) > max_bytes:
        raise TooLarge(obj.key)
    return data
