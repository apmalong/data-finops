"""The public demo (spec 09; ADR 0029, ADR 0030), made only when `publicDemo` is on: the demo
bucket, the `data-finops-demo` Athena workgroup, the Glue tables of the demo dataset and the
function that generates it (#225), the simulated orchestrator with its schedule (#226), and the
site: CloudFront over the bucket, the dashboard's files, and the publisher, which the database
object starts (#227, #228, #229)."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pulumi
import pulumi_aws as aws

from deployment.compute import DATABASE_KEY, allow, key_grant, log_writing, trust_policy
from deployment.settings import DEMO_WORKGROUP, Settings
from deployment.storage import INBOX, S3_MANAGED, bucket_policy, private_bucket

WORKGROUP = DEMO_WORKGROUP
"""The Athena workgroup of the simulated workload. Only it runs the demo's queries."""
DATABASE = "data_finops_demo"
"""The Glue database of the demo dataset. Athena names take letters, digits and `_` only."""
DATASET_PREFIX = "dataset/"
"""Where the dataset is in the demo bucket (`data_finops.public_demo.dataset.PREFIX`)."""
SNAPSHOTS_PREFIX = "snapshots/"
"""Where the publisher writes each Demo Snapshot, under a new prefix (#227)."""
SNAPSHOT_DAYS = 30
"""How long S3 keeps a Demo Snapshot. The publisher writes a new one after each Collection Run;
only 30 days without one would leave `current.json` naming an expired prefix."""
RESULTS_PREFIX = "athena-results/"
"""Where Athena writes the results of the demo's queries. S3 deletes them after a day."""
HANDLER = "data_finops.lambda_handlers.generator"
SIMULATOR_HANDLER = "data_finops.lambda_handlers.simulator"
SIMULATOR_TIMEOUT_SECONDS = 900
SIMULATOR_MEMORY_MB = 512
SCHEDULED_TIME = json.dumps({"scheduled_time": "<aws.scheduler.scheduled-time>"})
"""The simulator's event: EventBridge Scheduler puts the scheduled time in, so a start that is
invoked again plans the same runs."""
TABLES: dict[str, tuple[tuple[str, str], ...]] = {
    "customers": (
        ("customer_id", "bigint"),
        ("tenant_id", "string"),
        ("signup_date", "date"),
        ("country", "string"),
        ("segment", "string"),
    ),
    "orders": (
        ("order_id", "bigint"),
        ("customer_id", "bigint"),
        ("tenant_id", "string"),
        ("order_ts", "timestamp"),
        ("status", "string"),
        ("amount", "decimal(12,2)"),
    ),
    "events": (
        ("event_id", "bigint"),
        ("customer_id", "bigint"),
        ("tenant_id", "string"),
        ("event_ts", "timestamp"),
        ("event_type", "string"),
        ("page", "string"),
        ("session_id", "string"),
    ),
}
"""Each Glue table and its columns. They match `data_finops.public_demo.dataset.SCHEMAS`; a test
in the root project compares the two."""
HIVE_PARQUET = "org.apache.hadoop.hive.ql.io.parquet"
"""The package of the Hive classes that read Parquet, as Athena expects them."""
DASHBOARD = Path(__file__).resolve().parents[4] / "src" / "data_finops" / "api" / "dashboard"
"""The packaged dashboard; the site serves its files (spec 09 §The site)."""
SITE_FILES = {
    "index.html": "text/html; charset=utf-8",
    "dashboard.css": "text/css; charset=utf-8",
    "dashboard.js": "text/javascript; charset=utf-8",
    "static.js": "text/javascript; charset=utf-8",
}
"""Each file of the dashboard, at the root of the bucket, and its media type."""
CURRENT = "current.json"
"""The publisher's pointer to the current Demo Snapshot (`public_demo.publisher.CURRENT`)."""
CACHING_OPTIMIZED = "658327ea-f89d-4fab-a63d-7e88639e58f6"
"""CloudFront's managed cache policy `CachingOptimized`: a day by default."""
CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
"""CloudFront's managed cache policy `CachingDisabled`: for `current.json`, which moves."""
PUBLISHER_HANDLER = "data_finops.lambda_handlers.publisher"
PUBLISHER_TIMEOUT_SECONDS = 900
PUBLISHER_MEMORY_MB = 1024
GENERATOR_TIMEOUT_SECONDS = 900
GENERATOR_MEMORY_MB = 3008
"""The generator builds one Parquet part (a million rows) in memory at a time."""


class PublicDemo(pulumi.ComponentResource):
    bucket: aws.s3.Bucket
    generator: aws.lambda_.Function
    ready: pulumi.Output[list[Any]]
    """Settles once every resource of the component is registered."""

    def __init__(
        self,
        name: str,
        settings: Settings,
        *,
        code: dict[str, Any],
        inbox: aws.s3.Bucket,
        account_id: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("data-finops:index:PublicDemo", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)
        stack = pulumi.get_stack()
        # Everything in the demo bucket can be made again: the dataset from its seed, the
        # results by the next query. So it goes with the demo (the `publicDemo` kill switch).
        # S3-managed encryption, so that CloudFront can serve the site from it later (#229).
        self.bucket, guards = private_bucket(
            f"{name}-demo",
            force_destroy=True,
            encryption=S3_MANAGED,
            noncurrent_days=1,
            expiring={RESULTS_PREFIX: 1, SNAPSHOTS_PREFIX: SNAPSHOT_DAYS},
            opts=child,
            caller_writes_policy=True,  # with the site, to add CloudFront's grant
        )
        workgroup = aws.athena.Workgroup(
            f"{name}-demo",
            name=WORKGROUP,
            configuration={
                "bytes_scanned_cutoff_per_query": settings.demo_scan_cutoff_bytes,
                "enforce_workgroup_configuration": True,
                "publish_cloudwatch_metrics_enabled": True,
                "result_configuration": {
                    "output_location": self.bucket.bucket.apply(_s3_url(RESULTS_PREFIX)),
                    "encryption_configuration": {"encryption_option": "SSE_S3"},
                },
            },
            opts=child,
        )
        database = aws.glue.CatalogDatabase(f"{name}-demo", name=DATABASE, opts=child)
        tables = [
            aws.glue.CatalogTable(
                f"{name}-demo-{table}",
                name=table,
                database_name=database.name,
                table_type="EXTERNAL_TABLE",
                parameters={"classification": "parquet", "EXTERNAL": "TRUE"},
                storage_descriptor={
                    "location": self.bucket.bucket.apply(_s3_url(f"{DATASET_PREFIX}{table}/")),
                    "columns": [{"name": column, "type": kind} for column, kind in columns],
                    "input_format": f"{HIVE_PARQUET}.MapredParquetInputFormat",
                    "output_format": f"{HIVE_PARQUET}.MapredParquetOutputFormat",
                    "ser_de_info": {
                        "serialization_library": f"{HIVE_PARQUET}.serde.ParquetHiveSerDe"
                    },
                },
                opts=child,
            )
            for table, columns in TABLES.items()
        ]
        group = aws.cloudwatch.LogGroup(
            f"{name}-generator",
            name=f"/aws/lambda/{name}-{stack}-generator",
            retention_in_days=settings.log_retention_days,
            opts=child,
        )
        role = aws.iam.Role(
            f"{name}-generator", assume_role_policy=trust_policy("lambda.amazonaws.com"), opts=child
        )
        policy = aws.iam.RolePolicy(
            f"{name}-generator",
            role=role.id,
            policy=pulumi.Output.all(self.bucket.arn, group.arn).apply(
                lambda values: json.dumps(_generator_policy(*values))
            ),
            opts=child,
        )
        self.generator = aws.lambda_.Function(
            f"{name}-generator",
            name=f"{name}-{stack}-generator",
            role=role.arn,
            handler=HANDLER,
            timeout=GENERATOR_TIMEOUT_SECONDS,
            memory_size=GENERATOR_MEMORY_MB,
            environment={"variables": {"DATA_FINOPS_DEMO_BUCKET": self.bucket.bucket}},
            opts=pulumi.ResourceOptions(parent=self, depends_on=[group]),
            **code,
        )
        resources: list[pulumi.Resource] = [self.bucket, *guards, workgroup, database, *tables]
        resources += [group, role, policy, self.generator]
        self.simulator, made = self._simulator(name, stack, settings, code, inbox, account_id)
        resources += made
        self.distribution, made = self._site(name, stack)
        resources += made
        resources += self._publisher(name, stack, settings, code, inbox, account_id)
        self.ready = pulumi.Output.all(*[r.urn for r in resources])
        self.register_outputs(self.outputs())

    def _simulator(
        self,
        name: str,
        stack: str,
        settings: Settings,
        code: dict[str, Any],
        inbox: aws.s3.Bucket,
        account_id: pulumi.Input[str],
    ) -> tuple[aws.lambda_.Function, list[pulumi.Resource]]:
        """The simulated orchestrator (#226): its function, role and schedule."""
        child = pulumi.ResourceOptions(parent=self)
        group = aws.cloudwatch.LogGroup(
            f"{name}-simulator",
            name=f"/aws/lambda/{name}-{stack}-simulator",
            retention_in_days=settings.log_retention_days,
            opts=child,
        )
        role = aws.iam.Role(
            f"{name}-simulator", assume_role_policy=trust_policy("lambda.amazonaws.com"), opts=child
        )
        policy = aws.iam.RolePolicy(
            f"{name}-simulator",
            role=role.id,
            policy=pulumi.Output.all(self.bucket.arn, inbox.arn, account_id, group.arn).apply(
                lambda values: json.dumps(_simulator_policy(settings, *values))
            ),
            opts=child,
        )
        function = aws.lambda_.Function(
            f"{name}-simulator",
            name=f"{name}-{stack}-simulator",
            role=role.arn,
            handler=SIMULATOR_HANDLER,
            timeout=SIMULATOR_TIMEOUT_SECONDS,
            memory_size=SIMULATOR_MEMORY_MB,
            environment={"variables": {"DATA_FINOPS_BUCKET": inbox.bucket}},
            opts=pulumi.ResourceOptions(parent=self, depends_on=[group]),
            **code,
        )
        # The schedule does not retry, and neither does Lambda: a retry runs the queries again.
        retries = aws.lambda_.FunctionEventInvokeConfig(
            f"{name}-simulator",
            function_name=function.name,
            maximum_retry_attempts=0,
            opts=child,
        )
        scheduler = aws.iam.Role(
            f"{name}-demo-scheduler",
            assume_role_policy=trust_policy("scheduler.amazonaws.com"),
            opts=child,
        )
        invoke = aws.iam.RolePolicy(
            f"{name}-demo-scheduler",
            role=scheduler.id,
            policy=function.arn.apply(
                lambda arn: json.dumps(allow([("lambda:InvokeFunction", [arn])]))
            ),
            opts=child,
        )
        schedule = aws.scheduler.Schedule(
            f"{name}-demo",
            schedule_expression=settings.demo_schedule,
            schedule_expression_timezone="UTC",
            flexible_time_window={"mode": "OFF"},
            target={
                "arn": function.arn,
                "role_arn": scheduler.arn,
                "input": SCHEDULED_TIME,
                "retry_policy": {"maximum_retry_attempts": 0},
            },
            opts=child,
        )
        return function, [group, role, policy, function, retries, scheduler, invoke, schedule]

    def _site(
        self, name: str, stack: str
    ) -> tuple[aws.cloudfront.Distribution, list[pulumi.Resource]]:
        """CloudFront over the demo bucket, through origin access control and HTTPS only, the
        dashboard's files, and the bucket policy that lets CloudFront read the site and nothing
        else (spec 09 §The site; #228, #229)."""
        child = pulumi.ResourceOptions(parent=self)
        access = aws.cloudfront.OriginAccessControl(
            f"{name}-demo",
            name=f"{name}-{stack}-demo",
            origin_access_control_origin_type="s3",
            signing_behavior="always",
            signing_protocol="sigv4",
            opts=child,
        )
        origin = "demo-bucket"
        https = "redirect-to-https"
        reads = ["GET", "HEAD"]

        def ordered(
            path_pattern: str, cache_policy_id: str
        ) -> aws.cloudfront.DistributionOrderedCacheBehaviorArgsDict:
            return {
                "path_pattern": path_pattern,
                "target_origin_id": origin,
                "viewer_protocol_policy": https,
                "allowed_methods": reads,
                "cached_methods": reads,
                "compress": True,
                "cache_policy_id": cache_policy_id,
            }

        origins: list[aws.cloudfront.DistributionOriginArgsDict] = [
            {
                "origin_id": origin,
                "domain_name": self.bucket.bucket_regional_domain_name,
                "origin_access_control_id": access.id,
            }
        ]
        default: aws.cloudfront.DistributionDefaultCacheBehaviorArgsDict = {
            "target_origin_id": origin,
            "viewer_protocol_policy": https,
            "allowed_methods": reads,
            "cached_methods": reads,
            "compress": True,
            "cache_policy_id": CACHING_OPTIMIZED,
        }
        distribution = aws.cloudfront.Distribution(
            f"{name}-demo",
            enabled=True,
            comment=f"{name} {stack}: the public demo",
            default_root_object="index.html",
            price_class="PriceClass_100",
            origins=origins,
            default_cache_behavior=default,
            ordered_cache_behaviors=[
                ordered(CURRENT, CACHING_DISABLED),  # it moves: never cached
                ordered(f"{SNAPSHOTS_PREFIX}*", CACHING_OPTIMIZED),
            ],
            restrictions={"geo_restriction": {"restriction_type": "none"}},
            viewer_certificate={"cloudfront_default_certificate": True},
            opts=child,
        )
        policy = aws.s3.BucketPolicy(
            f"{name}-demo",
            bucket=self.bucket.id,
            policy=pulumi.Output.all(self.bucket.arn, distribution.arn).apply(
                lambda values: bucket_policy(values[0], _site_grant(*values))
            ),
            opts=child,
        )
        files: list[pulumi.Resource] = [
            aws.s3.BucketObjectv2(
                f"{name}-site-{key.replace('.', '-')}",
                bucket=self.bucket.id,
                key=key,
                content_type=media_type,
                **_site_file(key),
                opts=child,
            )
            for key, media_type in SITE_FILES.items()
        ]
        return distribution, [access, distribution, policy, *files]

    def _publisher(
        self,
        name: str,
        stack: str,
        settings: Settings,
        code: dict[str, Any],
        inbox: aws.s3.Bucket,
        account_id: pulumi.Input[str],
    ) -> list[pulumi.Resource]:
        """The publisher (#227): its function and role, and the S3 event of the database object
        that starts it (spec 09 §The publisher)."""
        child = pulumi.ResourceOptions(parent=self)
        group = aws.cloudwatch.LogGroup(
            f"{name}-publisher",
            name=f"/aws/lambda/{name}-{stack}-publisher",
            retention_in_days=settings.log_retention_days,
            opts=child,
        )
        role = aws.iam.Role(
            f"{name}-publisher", assume_role_policy=trust_policy("lambda.amazonaws.com"), opts=child
        )
        policy = aws.iam.RolePolicy(
            f"{name}-publisher",
            role=role.id,
            policy=pulumi.Output.all(inbox.arn, self.bucket.arn, group.arn).apply(
                lambda values: json.dumps(_publisher_policy(settings, *values))
            ),
            opts=child,
        )
        function = aws.lambda_.Function(
            f"{name}-publisher",
            name=f"{name}-{stack}-publisher",
            role=role.arn,
            handler=PUBLISHER_HANDLER,
            timeout=PUBLISHER_TIMEOUT_SECONDS,
            memory_size=PUBLISHER_MEMORY_MB,
            environment={
                "variables": {
                    "DATA_FINOPS_BUCKET": inbox.bucket,
                    "DATA_FINOPS_DEMO_BUCKET": self.bucket.bucket,
                }
            },
            opts=pulumi.ResourceOptions(parent=self, depends_on=[group]),
            **code,
        )
        permission = aws.lambda_.Permission(
            f"{name}-publisher",
            action="lambda:InvokeFunction",
            function=function.name,
            principal="s3.amazonaws.com",
            source_arn=inbox.arn,
            source_account=account_id,
            opts=child,
        )
        # A BucketNotification is the whole notification configuration of its bucket: a later
        # trigger on the Deployment's bucket must be added to this resource, not beside it.
        trigger = aws.s3.BucketNotification(
            f"{name}-publisher",
            bucket=inbox.id,
            lambda_functions=[
                {
                    "lambda_function_arn": function.arn,
                    "events": ["s3:ObjectCreated:*"],
                    "filter_prefix": DATABASE_KEY,
                    "filter_suffix": ".sqlite",
                }
            ],
            opts=pulumi.ResourceOptions(parent=self, depends_on=[permission]),
        )
        return [group, role, policy, function, permission, trigger]

    def outputs(self) -> dict[str, pulumi.Output[str]]:
        """The component's stack outputs (spec 09 §Stack)."""
        return {
            "demoUrl": self.distribution.domain_name.apply(lambda domain: f"https://{domain}/"),
            "demoBucket": self.bucket.bucket,
            "demoGeneratorFunction": self.generator.name,
            "demoSimulatorFunction": self.simulator.name,
        }


def _s3_url(prefix: str) -> Callable[[str], str]:
    """The S3 URL of a prefix in a bucket, from the bucket's name."""
    return lambda bucket: f"s3://{bucket}/{prefix}"


def _simulator_policy(
    settings: Settings, bucket_arn: str, inbox_arn: str, account_id: str, group_arn: str
) -> dict[str, Any]:
    """Run queries in the demo workgroup only, read the dataset's tables and files, write the
    results, and write to the Inbox as a producer (spec 09 §IAM, Q80)."""
    workgroup = f"arn:aws:athena:{settings.region}:{account_id}:workgroup/{WORKGROUP}"
    glue = f"arn:aws:glue:{settings.region}:{account_id}"
    tables = [f"{glue}:catalog", f"{glue}:database/{DATABASE}", f"{glue}:table/{DATABASE}/*"]
    results = f"{bucket_arn}/{RESULTS_PREFIX}*"
    return allow(
        [
            (
                [
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:StopQueryExecution",
                ],
                [workgroup],
            ),
            (["glue:GetDatabase", "glue:GetTable", "glue:GetPartitions"], tables),
            (["s3:ListBucket", "s3:GetBucketLocation"], [bucket_arn]),
            ("s3:GetObject", [f"{bucket_arn}/{DATASET_PREFIX}*"]),
            ("s3:PutObject", [results, f"{inbox_arn}/{INBOX}*"]),
            ("s3:AbortMultipartUpload", [results]),
            log_writing(group_arn),
            *key_grant(settings, ["kms:GenerateDataKey"]),  # the Inbox's bucket's key, if any
        ]
    )


def _site_grant(bucket_arn: str, distribution_arn: str) -> dict[str, Any]:
    """CloudFront, for this distribution only, may read the site: the dashboard's files, the
    pointer and the snapshots. Never `dataset/` or `athena-results/` (Q77)."""
    keys = [*SITE_FILES, CURRENT]
    return {
        "Sid": "CloudFrontReadsTheSite",
        "Effect": "Allow",
        "Principal": {"Service": "cloudfront.amazonaws.com"},
        "Action": "s3:GetObject",
        "Resource": [f"{bucket_arn}/{key}" for key in keys] + [f"{bucket_arn}/{SNAPSHOTS_PREFIX}*"],
        "Condition": {"StringEquals": {"AWS:SourceArn": distribution_arn}},
    }


def _site_file(key: str) -> dict[str, Any]:
    """A dashboard file as the site serves it: the page says it reads the static data source
    (#228); every other file is the packaged one."""
    path = DASHBOARD / key
    if key != "index.html":
        return {"source": pulumi.FileAsset(str(path))}
    page = path.read_text(encoding="utf-8")
    api, static = (
        '<meta name="data-finops-source" content="api">',
        '<meta name="data-finops-source" content="static">',
    )
    if api not in page:
        raise RuntimeError("the dashboard's page has no data-finops-source meta to set")
    return {"content": page.replace(api, static)}


def _publisher_policy(
    settings: Settings, inbox_arn: str, bucket_arn: str, group_arn: str
) -> dict[str, Any]:
    """Read the database object; read and move `current.json`, and write the snapshots
    (spec 09 §The publisher). `s3:ListBucket` lets S3 answer a missing object `NoSuchKey`."""
    pointer = f"{bucket_arn}/{CURRENT}"
    return allow(
        [
            ("s3:GetObject", [f"{inbox_arn}/{DATABASE_KEY}", pointer]),
            ("s3:ListBucket", [inbox_arn, bucket_arn]),
            ("s3:PutObject", [pointer, f"{bucket_arn}/{SNAPSHOTS_PREFIX}*"]),
            log_writing(group_arn),
            *key_grant(settings, ["kms:Decrypt"]),  # the database object's key, if any
        ]
    )


def _generator_policy(bucket_arn: str, group_arn: str) -> dict[str, Any]:
    """Write the dataset, and nothing else in the bucket (spec 09 §The demo dataset)."""
    return allow([("s3:PutObject", [f"{bucket_arn}/{DATASET_PREFIX}*"]), log_writing(group_arn)])
