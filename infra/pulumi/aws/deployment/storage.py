"""The Deployment's one bucket (spec 08 §Topology; ADR 0026): the database object, the Inboxes
and the Lambda package, private, encrypted, versioned and kept on destroy unless the operator
opts in to deleting it."""

import json

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
        self.bucket = aws.s3.Bucket(name, force_destroy=settings.delete_data_on_destroy, opts=child)
        guards: list[pulumi.CustomResource] = [
            aws.s3.BucketPublicAccessBlock(
                name,
                bucket=self.bucket.id,
                block_public_acls=True,
                block_public_policy=True,
                ignore_public_acls=True,
                restrict_public_buckets=True,
                opts=child,
            ),
            aws.s3.BucketOwnershipControls(
                name,
                bucket=self.bucket.id,
                rule={"object_ownership": "BucketOwnerEnforced"},
                opts=child,
            ),
            aws.s3.BucketServerSideEncryptionConfiguration(
                name, bucket=self.bucket.id, rules=[_encryption(settings)], opts=child
            ),
            aws.s3.BucketVersioning(
                name,
                bucket=self.bucket.id,
                versioning_configuration={"status": "Enabled"},
                opts=child,
            ),
            aws.s3.BucketLifecycleConfiguration(
                name,
                bucket=self.bucket.id,
                rules=[
                    {
                        "id": "noncurrent-versions",
                        "status": "Enabled",
                        "filter": {},
                        "noncurrent_version_expiration": {
                            "noncurrent_days": settings.noncurrent_version_days
                        },
                    },
                    {
                        "id": "inbox",
                        "status": "Enabled",
                        "filter": {"prefix": INBOX},
                        "expiration": {"days": settings.inbox_retention_days},
                    },
                ],
                opts=child,
            ),
            aws.s3.BucketPolicy(
                name, bucket=self.bucket.id, policy=self.bucket.arn.apply(_tls_only), opts=child
            ),
        ]
        self.ready = pulumi.Output.all(self.bucket.id, *[g.id for g in guards])
        self.register_outputs({"bucket": self.bucket.bucket})


def kept_on_destroy(settings: Settings) -> pulumi.ResourceOptions:
    """The options every resource of the component is declared with: kept by `pulumi
    destroy` (removed from the stack, left in the account) unless the operator opted in to
    deleting the data (spec 08, `deleteDataOnDestroy`)."""
    return pulumi.ResourceOptions(retain_on_delete=not settings.delete_data_on_destroy)


def _encryption(
    settings: Settings,
) -> aws.s3.BucketServerSideEncryptionConfigurationRuleArgsDict:
    if settings.kms_key_arn is None:
        return {"apply_server_side_encryption_by_default": {"sse_algorithm": "AES256"}}
    return {
        "apply_server_side_encryption_by_default": {
            "sse_algorithm": "aws:kms",
            "kms_master_key_id": settings.kms_key_arn,
        },
        "bucket_key_enabled": True,
    }


def _tls_only(bucket_arn: str) -> str:
    """A bucket policy refusing every request not made over TLS."""
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
                }
            ],
        }
    )
