# Security, deployment and operations

## Deployment
Local first: Python CLI + SQLite (ADR 0018) + FastAPI + static HTML.

The `data-finops` command (stdlib `argparse`) has these subcommands:
- `demo`: it loads the fixtures into a temporary database, runs a Calculation Run, serves on loopback with no token and prints the URL. The database is removed when `demo` stops. This is the one command of spec 01.
  - Run it from a source checkout, because it reads the `fixtures/` folder of the repository.
  - M6 decided that there is no PyPI release, so the fixtures stay out of the wheel (m6-decisions Q6).
- `serve --db PATH`
- `ingest {focus,openlineage,dbt,athena} PATH --db PATH` (athena also takes `--account-id` and `--region`)
- `calculate --db PATH`
- `collect --db PATH --config FILE`: one Collection Run against AWS (spec 08)

Exit codes:
- 0: everything was stored.
- 1: something was rejected. The rest is stored.
- 2: nothing was done (bad arguments, a file that cannot be read, or a file that is not a data-finops database).
- `collect` also exits with 2 when it could not read a source. It still collected the other sources and calculated (spec 08 §6).

Rejected items are named by position and reason, never by content.

FastAPI and uvicorn are an optional `api` extra. Without it, `demo` and `serve` print how to install it. By default, the server binds to loopback (ADR 0024). The token comes from `DATA_FINOPS_TOKEN` or from a token file, never from both. It is at least 16 characters, without the spaces at its start and end (#108).

Production AWS: Pulumi Python deploys a private ingestion API or a scheduled pull, a read-only collector IAM, object storage, compute and metrics. As an option, it also deploys a dashboard behind authentication. Avoid a mandatory NAT, VPC, RDS, Marquez or Kubernetes.

Spec 08 decided these points for M6:
- two Lambda functions without a VPC (ADR 0026)
- ingress by scheduled pull from S3 Inboxes (ADR 0027)
- a read-only API behind IAM
- the dashboard used locally

## IAM
Keep the deployer identity separate from the collector identity.
- Billing export bucket: read only, scoped to the path.
- Athena query history: read and list only, where the APIs permit it.
- CloudWatch: read only, optional.

The collector has no permission to run arbitrary customer queries, to change data or to remediate automatically. Document the AWS API actions after validation. Do not invent ARNs or permissions.

## Threat model
Risks:
- billing metadata leakage
- malicious OpenLineage payloads
- event or SQL text that is too large
- forged run identity
- SSRF through configurable endpoints
- query injection
- supply-chain compromise
- unexpected cloud spend

Controls:
- authentication
- strict payload limits
- schema validation
- bounded pagination
- an explicit endpoint allowlist
- parameterized SQL
- pinned dependencies
- SBOM
- provenance
- budgets and alerts
- no shell execution from metadata

## Observability
Metrics: collector failures, lag, ingestion counts, dedupe counts, identity match coverage, unallocated share, Reconciliation Delta, attribution policy version and platform cost. Structured logs redact sensitive fields. There is no hidden external telemetry.

## Operational procedures
- backfill and late billing corrections
- retry with exponential backoff
- version migrations
- disaster recovery from raw inputs
- key rotation
- uninstall through Pulumi, with retention choices
- a monthly cost report for the FinOps service itself
