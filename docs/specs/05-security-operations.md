# Security, deployment and operations

## Deployment
Local first: Python CLI + SQLite (ADR 0018) + FastAPI + static HTML. The `data-finops` command (stdlib `argparse`) has `demo` (loads the fixtures into a temporary database, removed when it stops, runs a Calculation Run, serves on loopback with no token and prints the URL; spec 01's one command, run from a source checkout because it reads the repo's `fixtures/`; shipping them in the wheel is left to M6 packaging, owner decision #116), `serve --db PATH`, `ingest {focus,openlineage,dbt,athena} PATH --db PATH` (athena also `--account-id` and `--region`) `calculate --db PATH` and `collect --db PATH --config FILE` (one Collection Run against AWS, spec 08). Exit codes: 0 when everything was stored, 1 when something was rejected (the rest is stored), 2 when nothing was done (bad arguments, an unreadable file, a file that is not a data-finops database). Rejected items are named by position and reason, never by content. FastAPI and uvicorn are an optional `api` extra; without it `demo` and `serve` print how to install it. The server binds to loopback by default (ADR 0024). The token comes from `DATA_FINOPS_TOKEN` or a token file, never both, and is at least 16 characters without surrounding spaces (#108). Production AWS: Pulumi Python deploys private ingestion API or scheduled pull, read-only collector IAM, object storage, compute, metrics, and optionally dashboard behind auth. Avoid mandatory NAT, VPC, RDS, Marquez, and Kubernetes. Decided for M6 in spec 08: two Lambda functions without a VPC (ADR 0026), ingress by scheduled pull from S3 Inboxes (ADR 0027), a read-only API behind IAM, and the dashboard used locally.

## IAM
Separate deployer identity from collector identity. Billing export bucket read only scoped to path; Athena query history read/list only where APIs permit; CloudWatch read only optional. No permission to execute arbitrary customer queries, mutate data, or remediate automatically. Document AWS API actions after validation; don't invent ARNs or permissions.

## Threat model
Risks: billing metadata leakage, malicious OpenLineage payloads, oversized event/SQL text, forged run identity, SSRF through configurable endpoints, query injection, supply-chain compromise, unexpected cloud spend. Controls: authentication, strict payload limits, schema validation, bounded pagination, explicit endpoint allowlist, parameterized SQL, dependency pinning, SBOM, provenance, budgets/alerts, no shell execution from metadata.

## Observability
Metrics: collector failures, lag, ingestion counts, dedupe counts, identity match coverage, unallocated share, reconciliation delta, attribution policy version, platform cost. Structured logs redact sensitive fields. No hidden external telemetry.

## Operational procedures
Backfill and late billing corrections; retry with exponential backoff; version migrations; disaster recovery from raw inputs; key rotation; uninstall via Pulumi with retention choices; monthly cost report for FinOps service itself.
