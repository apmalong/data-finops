"""The public demo (spec 09; ADR 0029, ADR 0030), made only when `publicDemo` is on: the demo
bucket, the `data-finops-demo` Athena workgroup, the Glue tables of the demo dataset, and the
function that generates it (#225). The schedule, the simulator, the publisher and CloudFront
come later (#226, #227, #229)."""

import json
from collections.abc import Callable
from typing import Any

import pulumi
import pulumi_aws as aws

from deployment.compute import allow, log_writing, trust_policy
from deployment.settings import Settings
from deployment.storage import S3_MANAGED, private_bucket

WORKGROUP = "data-finops-demo"
"""The Athena workgroup of the simulated workload. Only it runs the demo's queries."""
DATABASE = "data_finops_demo"
"""The Glue database of the demo dataset. Athena names take letters, digits and `_` only."""
DATASET_PREFIX = "dataset/"
"""Where the dataset is in the demo bucket (`data_finops.public_demo.dataset.PREFIX`)."""
RESULTS_PREFIX = "athena-results/"
"""Where Athena writes the results of the demo's queries. S3 deletes them after a day."""
HANDLER = "data_finops.lambda_handlers.generator"
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
            expiring={RESULTS_PREFIX: 1},
            opts=child,
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
        self.ready = pulumi.Output.all(*[r.urn for r in resources])
        self.register_outputs(self.outputs())

    def outputs(self) -> dict[str, pulumi.Output[str]]:
        """The component's stack outputs (spec 09 §Stack)."""
        return {"demoBucket": self.bucket.bucket, "demoGeneratorFunction": self.generator.name}


def _s3_url(prefix: str) -> Callable[[str], str]:
    """The S3 URL of a prefix in a bucket, from the bucket's name."""
    return lambda bucket: f"s3://{bucket}/{prefix}"


def _generator_policy(bucket_arn: str, group_arn: str) -> dict[str, Any]:
    """Write the dataset, and nothing else in the bucket (spec 09 §The demo dataset)."""
    return allow([("s3:PutObject", [f"{bucket_arn}/{DATASET_PREFIX}*"]), log_writing(group_arn)])
