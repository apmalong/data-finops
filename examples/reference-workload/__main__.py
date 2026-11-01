"""Pulumi entry point for the reference workload (ADR 0021)."""

import infra
import pulumi

infra.require_local_state()
outputs = infra.build(infra.Settings.from_config(pulumi.Config(), pulumi.Config("aws")))
pulumi.export("bucket", outputs.bucket_name)
pulumi.export("database", outputs.database_name)
pulumi.export("workgroup", outputs.workgroup_name)
