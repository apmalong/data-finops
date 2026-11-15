"""The Deployment's one bucket (spec 08 §Topology; ADR 0026): the database object, the Inboxes
and the Lambda package, private, encrypted, versioned and kept on destroy unless the operator
opts in to deleting it."""

import json
from typing import Any

import pulumi
import pulumi_aws as aws

from deployment.settings import Settings

INBOX = "inbox/"


class Storage(pulumi.ComponentResource):
    bucket: aws.s3.Bucket
    ready: pulumi.Output[list[str]]
    """Settles once every resource of the component is registered."""

    def __init__(
        self, name: str, settings: Settings, opts: pulumi.ResourceOptions | None = None
    ) -> None:
        super().__init__("data-finops:index:Storage", name, None, opts)
        # The bucket and every guard share one retention: a kept bucket keeps its guards.
        child = pulumi.ResourceOptions.merge(
            kept_on_destroy(settings), pulumi.ResourceOptions(parent=self)
        )
        self.bucket, guards = private_bucket(
            name,
            force_destroy=settings.delete_data_on_destroy,
            encryption=_encryption(settings),
            noncurrent_days=settings.noncurrent_version_days,
            expiring={INBOX: settings.inbox_retention_days},
            opts=child,
        )
        self.ready = pulumi.Output.all(self.bucket.id, *[g.id for g in guards])
        self.register_outputs({"bucket": self.bucket.bucket})


def private_bucket(
    name: str,
    *,
    force_destroy: bool,
    encryption: aws.s3.BucketServerSideEncryptionConfigurationRuleArgsDict,
    noncurrent_days: int,
    expiring: dict[str, int],
    opts: pulumi.ResourceOptions,
    tls_policy: bool = True,
) -> tuple[aws.s3.Bucket, list[pulumi.CustomResource]]:
    """A bucket with every guard of the policy pack (spec 08 §Policy pack, 1): all public access
    blocked, owner-enforced, encrypted, versioned and TLS only. S3 keeps an old version for
    `noncurrent_days`, and the objects under each prefix of `expiring` for its days. Without
    `tls_policy`, the caller writes the bucket policy with `bucket_policy`, to add grants."""
    bucket = aws.s3.Bucket(name, force_destroy=force_destroy, opts=opts)
    rules: list[aws.s3.BucketLifecycleConfigurationRuleArgsDict] = [
        {
            "id": "noncurrent-versions",
            "status": "Enabled",
            "filter": {},
            "noncurrent_version_expiration": {"noncurrent_days": noncurrent_days},
        }
    ]
    rules += [
        {
            "id": prefix.strip("/"),
            "status": "Enabled",
            "filter": {"prefix": prefix},
            "expiration": {"days": days},
        }
        for prefix, days in expiring.items()
    ]
    guards: list[pulumi.CustomResource] = [
        aws.s3.BucketPublicAccessBlock(
            name,
            bucket=bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=opts,
        ),
        aws.s3.BucketOwnershipControls(
            name, bucket=bucket.id, rule={"object_ownership": "BucketOwnerEnforced"}, opts=opts
        ),
        aws.s3.BucketServerSideEncryptionConfiguration(
            name, bucket=bucket.id, rules=[encryption], opts=opts
        ),
        aws.s3.BucketVersioning(
            name, bucket=bucket.id, versioning_configuration={"status": "Enabled"}, opts=opts
        ),
        aws.s3.BucketLifecycleConfiguration(name, bucket=bucket.id, rules=rules, opts=opts),
    ]
    if tls_policy:
        policy = bucket.arn.apply(bucket_policy)
        guards.append(aws.s3.BucketPolicy(name, bucket=bucket.id, policy=policy, opts=opts))
    return bucket, guards


def kept_on_destroy(settings: Settings) -> pulumi.ResourceOptions:
    """The options every resource of the component is declared with: kept by `pulumi
    destroy` (removed from the stack, left in the account) unless the operator opted in to
    deleting the data (spec 08, `deleteDataOnDestroy`)."""
    return pulumi.ResourceOptions(retain_on_delete=not settings.delete_data_on_destroy)


S3_MANAGED: aws.s3.BucketServerSideEncryptionConfigurationRuleArgsDict = {
    "apply_server_side_encryption_by_default": {"sse_algorithm": "AES256"}
}
"""Encryption with the S3-managed key."""


def _encryption(
    settings: Settings,
) -> aws.s3.BucketServerSideEncryptionConfigurationRuleArgsDict:
    if settings.kms_key_arn is None:
        return S3_MANAGED
    return {
        "apply_server_side_encryption_by_default": {
            "sse_algorithm": "aws:kms",
            "kms_master_key_id": settings.kms_key_arn,
        },
        "bucket_key_enabled": True,
    }


def bucket_policy(bucket_arn: str, *grants: dict[str, Any]) -> str:
    """A bucket policy refusing every request not made over TLS, then `grants`."""
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "DenyWithoutTls",
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": "s3:*",
                    "Resource": [bucket_arn, f"{bucket_arn}/*"],
                    "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                },
                *grants,
            ],
        }
    )
