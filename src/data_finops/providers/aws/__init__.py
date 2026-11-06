"""AWS collectors (spec 08): they fetch objects and API responses and hand them to the
existing readers, never writing a store themselves. boto3 is the optional `aws` extra; these
modules only type their clients, so importing them never needs it."""

from data_finops.providers.aws.s3 import (
    AwsSourceError,
    S3Object,
    S3Reader,
    TooLarge,
    list_objects,
    read_object,
)

__all__ = ["AwsSourceError", "S3Object", "S3Reader", "TooLarge", "list_objects", "read_object"]
