"""AWS collectors (spec 08): they fetch objects and API responses and hand them to the
existing readers, never writing a store themselves. boto3 is the optional `aws` extra; these
modules only type their clients, so importing them never needs it."""

from data_finops.providers.aws.athena import (
    UNFINISHED,
    AthenaReader,
    HistoryListing,
    batch_get_query_executions,
    list_query_execution_ids,
)
from data_finops.providers.aws.calls import AwsSourceError
from data_finops.providers.aws.s3 import (
    S3Object,
    S3Reader,
    S3Writer,
    TooLarge,
    WriteConflict,
    download_object,
    fetch_file,
    list_folders,
    list_objects,
    put_file,
    read_object,
    refresh_file,
)

__all__ = [
    "UNFINISHED",
    "AthenaReader",
    "AwsSourceError",
    "HistoryListing",
    "S3Object",
    "S3Reader",
    "S3Writer",
    "WriteConflict",
    "TooLarge",
    "batch_get_query_executions",
    "download_object",
    "fetch_file",
    "list_folders",
    "list_objects",
    "list_query_execution_ids",
    "put_file",
    "read_object",
    "refresh_file",
]
