"""The Deployment's functions, schedule, IAM and optional alerts (spec 08 §Topology, §IAM,
§Cost limits; ADR 0026)."""

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pulumi
import pulumi_aws as aws

from deployment.gateway import Gateway, build_gateway
from deployment.package import ARCHITECTURE, PYTHON
from deployment.settings import Settings
from deployment.storage import INBOX, Storage

DATABASE_KEY = "database/data-finops.sqlite"
"""The database object (spec 08; `data_finops.collection.remote.DATABASE_KEY`)."""
PACKAGE_KEY = "lambda/data-finops-lambda.zip"
DIMENSIONS_KEY = "config/cost-dimensions.toml"
"""Where the Cost Dimensions file goes, when `costDimensions` is set (#224)."""
RUNTIME = f"python{PYTHON}"
HANDLERS = {
    "collector": "data_finops.lambda_handlers.collector",
    "api": "data_finops.lambda_handlers.api",
}
QUERY_RUNNING = [
    "athena:StartQueryExecution",
    "athena:StopQueryExecution",
    "athena:GetQueryResults",
    "athena:GetQueryRuntimeStatistics",
]
"""Athena calls that run a query or read its results: denied outright (spec 08 §IAM)."""


def trust_policy(service: str) -> str:
    """A role trust policy letting one AWS service assume the role."""
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": service},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    )


class Compute(pulumi.ComponentResource):
    collector: aws.lambda_.Function
    api: aws.lambda_.Function
    gateway: Gateway
    code: dict[str, Any]
    """Where every function's code is: the package object and its hash, the runtime and the
    architecture. The demo's functions use it too."""
    producer_policy: aws.iam.Policy
    ready: pulumi.Output[list[Any]]
    """Settles once every resource of the component is registered."""

    def __init__(
        self,
        name: str,
        settings: Settings,
        storage: Storage,
        *,
        package: Path,
        account_id: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("data-finops:index:Compute", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)
        bucket = storage.bucket
        stack = pulumi.get_stack()
        code = aws.s3.BucketObjectv2(
            f"{name}-package",
            bucket=bucket.id,
            key=PACKAGE_KEY,
            source=pulumi.FileAsset(str(package)),
            opts=child,
        )
        if settings.cost_dimensions is not None:
            aws.s3.BucketObjectv2(
                f"{name}-cost-dimensions",
                bucket=bucket.id,
                key=DIMENSIONS_KEY,
                source=pulumi.FileAsset(settings.cost_dimensions),
                opts=child,
            )
        groups = {
            role: aws.cloudwatch.LogGroup(
                f"{name}-{role}",
                name=f"/aws/lambda/{name}-{stack}-{role}",
                retention_in_days=settings.log_retention_days,
                opts=child,
            )
            for role in HANDLERS
        }
        roles = {
            role: aws.iam.Role(
                f"{name}-{role}",
                assume_role_policy=trust_policy("lambda.amazonaws.com"),
                opts=child,
            )
            for role in HANDLERS
        }
        resources: list[pulumi.Resource] = [code, *groups.values(), *roles.values()]
        resources.append(
            aws.iam.RolePolicy(
                f"{name}-collector",
                role=roles["collector"].id,
                policy=pulumi.Output.all(bucket.arn, account_id, groups["collector"].arn).apply(
                    lambda values: json.dumps(_collector_policy(settings, *values))
                ),
                opts=child,
            )
        )
        resources.append(
            aws.iam.RolePolicy(
                f"{name}-api",
                role=roles["api"].id,
                policy=pulumi.Output.all(bucket.arn, groups["api"].arn).apply(
                    lambda values: json.dumps(_api_policy(settings, *values))
                ),
                opts=child,
            )
        )
        common: dict[str, Any] = {
            "runtime": RUNTIME,
            "architectures": [ARCHITECTURE],
            "s3_bucket": bucket.bucket,
            "s3_key": code.key,
            "source_code_hash": _code_hash(package),
        }
        self.code = common
        self.collector = aws.lambda_.Function(
            f"{name}-collector",
            name=f"{name}-{stack}-collector",
            role=roles["collector"].arn,
            handler=HANDLERS["collector"],
            timeout=settings.collector_timeout_seconds,
            memory_size=settings.collector_memory_mb,
            environment={
                "variables": {
                    "DATA_FINOPS_COLLECTOR_SETTINGS": pulumi.Output.all(
                        bucket.bucket, account_id
                    ).apply(lambda values: json.dumps(_collector_settings(settings, *values)))
                }
            },
            opts=pulumi.ResourceOptions(parent=self, depends_on=[groups["collector"]]),
            **common,
        )
        api_options: dict[str, Any] = {}
        if settings.api_reserved_concurrency is not None:
            api_options["reserved_concurrent_executions"] = settings.api_reserved_concurrency
        self.api = aws.lambda_.Function(
            f"{name}-api",
            name=f"{name}-{stack}-api",
            role=roles["api"].arn,
            handler=HANDLERS["api"],
            timeout=settings.api_timeout_seconds,
            memory_size=settings.api_memory_mb,
            environment={
                "variables": {
                    "DATA_FINOPS_BUCKET": bucket.bucket,
                    "DATA_FINOPS_EXPLANATION_DETAIL": settings.explanation_detail,
                }
            },
            opts=pulumi.ResourceOptions(parent=self, depends_on=[groups["api"]]),
            **common,
            **api_options,
        )
        self.gateway = build_gateway(name, settings, self.api, child)
        scheduler_role = aws.iam.Role(
            f"{name}-scheduler",
            assume_role_policy=trust_policy("scheduler.amazonaws.com"),
            opts=child,
        )
        resources.append(
            aws.iam.RolePolicy(
                f"{name}-scheduler",
                role=scheduler_role.id,
                policy=self.collector.arn.apply(
                    lambda arn: json.dumps(allow([("lambda:InvokeFunction", [arn])]))
                ),
                opts=child,
            )
        )
        resources.append(
            aws.scheduler.Schedule(
                f"{name}-collector",
                schedule_expression=settings.schedule,
                schedule_expression_timezone="UTC",
                flexible_time_window={"mode": "OFF"},
                target={
                    "arn": self.collector.arn,
                    "role_arn": scheduler_role.arn,
                    "retry_policy": {"maximum_retry_attempts": 2},
                },
                opts=child,
            )
        )
        self.producer_policy = aws.iam.Policy(
            f"{name}-producer",
            description="data-finops: write OpenLineage events and dbt artifacts to the Inbox",
            policy=bucket.arn.apply(lambda arn: _producer_policy(settings, arn)),
            opts=child,
        )
        resources += [scheduler_role, self.collector, self.api, *self.gateway.resources]
        resources.append(self.producer_policy)
        resources += _alerts(name, settings, self.collector, child)
        self.ready = pulumi.Output.all(*[r.urn for r in resources])
        self.register_outputs(self.outputs())

    def outputs(self) -> dict[str, pulumi.Output[str]]:
        """The component's stack outputs (spec 08 §Settings, Outputs)."""
        return {
            "apiUrl": self.gateway.url,
            "producerPolicyArn": self.producer_policy.arn,
            **{f"{role}PolicyArn": p.arn for role, p in self.gateway.policies.items()},
            "collectorFunction": self.collector.name,
            "apiFunction": self.api.name,
        }


def _collector_settings(settings: Settings, bucket: str, account_id: str) -> dict[str, Any]:
    """The collector's settings, with the keys of the `collect` file (spec 08, Q50)."""
    return {
        "bucket": bucket,
        "region": settings.region,
        "account_id": account_id,
        "workgroups": list(settings.collected_workgroups),
        "export_bucket": settings.export_bucket,
        "export_prefix": settings.export_prefix,
        "export_name": settings.export_name,
        "billing_lookback_periods": settings.billing_lookback_periods,
        "max_history_pages": settings.max_history_pages,
    } | ({"cost_dimensions_key": DIMENSIONS_KEY} if settings.cost_dimensions is not None else {})


def allow(grants: list[tuple[str | list[str], list[str]]]) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": action, "Resource": resources}
            for action, resources in grants
        ],
    }


def log_writing(group_arn: str) -> tuple[list[str], list[str]]:
    return ["logs:CreateLogStream", "logs:PutLogEvents"], [f"{group_arn}:*"]


def key_grant(settings: Settings, actions: list[str]) -> list[tuple[str | list[str], list[str]]]:
    """The grant of `actions` on the bucket's KMS key, when the bucket has one."""
    return [] if settings.kms_key_arn is None else [(actions, [settings.kms_key_arn])]


def _collector_policy(
    settings: Settings, bucket_arn: str, account_id: str, group_arn: str
) -> dict[str, Any]:
    """Read the export and the named workgroups' history; read the Inbox, and the Cost
    Dimensions object when there is one; read and write the database object alone; deny
    running queries or reading their results (spec 08 §IAM).
    `s3:ListBucket` on its own bucket lets S3 answer a missing object `NoSuchKey`."""
    root = "/".join(p.strip("/") for p in (settings.export_prefix, settings.export_name) if p)
    export = f"arn:aws:s3:::{settings.export_bucket}"
    workgroups = [
        f"arn:aws:athena:{settings.region}:{account_id}:workgroup/{name}"
        for name in settings.collected_workgroups
    ]
    policy = allow(
        [
            ("s3:GetObject", [f"{export}/{root}/*"]),
            (["athena:ListQueryExecutions", "athena:BatchGetQueryExecution"], workgroups),
            ("s3:ListBucket", [bucket_arn]),
            (
                "s3:GetObject",
                [f"{bucket_arn}/{INBOX}*", f"{bucket_arn}/{DATABASE_KEY}"]
                + (
                    [f"{bucket_arn}/{DIMENSIONS_KEY}"]
                    if settings.cost_dimensions is not None
                    else []
                ),
            ),
            ("s3:PutObject", [f"{bucket_arn}/{DATABASE_KEY}"]),
            log_writing(group_arn),
            *key_grant(settings, ["kms:Decrypt", "kms:GenerateDataKey"]),
        ]
    )
    policy["Statement"].insert(
        0,
        {
            "Effect": "Allow",
            "Action": "s3:ListBucket",
            "Resource": [export],
            "Condition": {"StringLike": {"s3:prefix": [f"{root}/*"]}},
        },
    )
    policy["Statement"].append({"Effect": "Deny", "Action": QUERY_RUNNING, "Resource": "*"})
    return policy


def _api_policy(settings: Settings, bucket_arn: str, group_arn: str) -> dict[str, Any]:
    """Read the database object alone (spec 08 §IAM)."""
    return allow(
        [
            ("s3:GetObject", [f"{bucket_arn}/{DATABASE_KEY}"]),
            ("s3:ListBucket", [bucket_arn]),
            log_writing(group_arn),
            *key_grant(settings, ["kms:Decrypt"]),
        ]
    )


def _producer_policy(settings: Settings, bucket_arn: str) -> str:
    """Write into the Inbox and nothing else. With a customer key, S3 also needs
    `kms:GenerateDataKey` on it for each write (#270)."""
    return json.dumps(
        allow(
            [
                ("s3:PutObject", [f"{bucket_arn}/{INBOX}*"]),
                *key_grant(settings, ["kms:GenerateDataKey"]),
            ]
        )
    )


def _alerts(
    name: str, settings: Settings, collector: aws.lambda_.Function, child: pulumi.ResourceOptions
) -> list[pulumi.Resource]:
    """The optional errors alarm and Budget alert (spec 08 §Cost limits)."""
    made: list[pulumi.Resource] = []
    if settings.alarm_email is not None:
        topic = aws.sns.Topic(f"{name}-alarm", opts=child)
        made += [
            topic,
            aws.sns.TopicSubscription(
                f"{name}-alarm",
                topic=topic.arn,
                protocol="email",
                endpoint=settings.alarm_email,
                opts=child,
            ),
            aws.cloudwatch.MetricAlarm(
                f"{name}-collector-errors",
                namespace="AWS/Lambda",
                metric_name="Errors",
                dimensions={"FunctionName": collector.name},
                statistic="Sum",
                period=3600,
                evaluation_periods=1,
                threshold=0,
                comparison_operator="GreaterThanThreshold",
                treat_missing_data="notBreaching",
                alarm_actions=[topic.arn],
                opts=child,
            ),
        ]
    if settings.budget is not None:
        made.append(
            aws.budgets.Budget(
                f"{name}-budget",
                budget_type="COST",
                limit_amount=str(settings.budget.monthly_usd),
                limit_unit="USD",
                time_unit="MONTHLY",
                notifications=[
                    {
                        "comparison_operator": "GREATER_THAN",
                        "notification_type": kind,
                        "threshold": 100,
                        "threshold_type": "PERCENTAGE",
                        "subscriber_email_addresses": [settings.budget.email],
                    }
                    for kind in ("FORECASTED", "ACTUAL")
                ],
                opts=child,
            )
        )
    return made


def _code_hash(package: Path) -> str:
    """The package's base64 SHA-256, so a changed zip redeploys the functions."""
    return base64.b64encode(hashlib.sha256(package.read_bytes()).digest()).decode()
