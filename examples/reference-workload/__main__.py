"""Pulumi entry point for the reference workload (ADR 0021)."""

import infra
import pulumi

stack = infra.build(infra.Settings.from_config(pulumi.Config()))
pulumi.export("bucket", stack.bucket_name)
pulumi.export("database", stack.database_name)
pulumi.export("workgroup", stack.workgroup_name)
