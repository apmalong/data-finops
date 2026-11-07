"""AWS collectors (spec 08): they fetch objects and API responses and hand them to the
existing readers, never writing a store themselves. boto3 is the optional `aws` extra; these
modules only type their clients, so importing them never needs it."""

from data_finops.providers.aws.athena import (
    AthenaReader,
    HistoryListing,
    batch_get_query_executions,
    list_query_execution_ids,
)
from data_finops.providers.aws.s3 import (
    AwsSourceError,
    S3Object,
    S3Reader,
    TooLarge,
    list_objects,
    read_object,
)

__all__ = [
    "AthenaReader",
    "AwsSourceError",
    "HistoryListing",
    "S3Object",
    "S3Reader",
    "TooLarge",
    "batch_get_query_executions",
    "list_objects",
    "list_query_execution_ids",
    "read_object",
]
