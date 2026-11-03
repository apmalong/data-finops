# Security, deployment and operations

## Deployment
Local first: Python CLI + SQLite (ADR 0018) + FastAPI + static HTML. The `data-finops` command (stdlib `argparse`) has `demo` (loads the fixtures into a temporary database, runs a Calculation Run, serves and prints the URL; spec 01's one command), `serve --db PATH`, `ingest {focus,openlineage,dbt,athena} PATH` and `calculate`. FastAPI and uvicorn are an optional `api` extra; without it `demo` and `serve` print how to install it. The server binds to loopback by default (ADR 0024). Production AWS: Pulumi Python deploys private ingestion API or scheduled pull, read-only collector IAM, object storage, compute, metrics, and optionally dashboard behind auth. Avoid mandatory NAT, VPC, RDS, Marquez, and Kubernetes.

## IAM
Separate deployer identity from collector identity. Billing export bucket read only scoped to path; Athena query history read/list only where APIs permit; CloudWatch read only optional. No permission to execute arbitrary customer queries, mutate data, or remediate automatically. Document AWS API actions after validation; don't invent ARNs or permissions.

## Threat model
Risks: billing metadata leakage, malicious OpenLineage payloads, oversized event/SQL text, forged run identity, SSRF through configurable endpoints, query injection, supply-chain compromise, unexpected cloud spend. Controls: authentication, strict payload limits, schema validation, bounded pagination, explicit endpoint allowlist, parameterized SQL, dependency pinning, SBOM, provenance, budgets/alerts, no shell execution from metadata.

## Observability
Metrics: collector failures, lag, ingestion counts, dedupe counts, identity match coverage, unallocated share, reconciliation delta, attribution policy version, platform cost. Structured logs redact sensitive fields. No hidden external telemetry.

## Operational procedures
Backfill and late billing corrections; retry with exponential backoff; version migrations; disaster recovery from raw inputs; key rotation; uninstall via Pulumi with retention choices; monthly cost report for FinOps service itself.
