# ADR 0026: The AWS deployment is two Lambda functions without a VPC, sharing one SQLite file in S3

Status: Accepted (design, 2026-11-06). Decides OQ-5's topology for M6. Research: `docs/research/m6-aws-topology.md`, `docs/research/m6-pulumi-components-policies.md`.

## Context
M6 deploys data-finops to one AWS account with Pulumi Python (ADR 0002): scheduled read-only ingestion, a private API, encrypted storage and cost limits. Spec 05 rules out a mandatory VPC, NAT, RDS, Marquez or Kubernetes. The store is one SQLite file in WAL mode with one writer (ADR 0018), and sqlite.org says WAL does not work over a network filesystem. EFS and S3 Files both need a VPC; Mountpoint for S3 cannot modify files. App Runner closed to new customers on 2026-04-30. Always-on Fargate costs about $11 a month before any IAM authentication, and an ALB adds at least $16. At the expected scale (a few thousand to about a million Billing Lines a month), Lambda, S3, EventBridge Scheduler and CloudWatch Logs cost under $1 a month.

## Decision
1. **Two functions, no VPC.** Both run Python 3.12 on arm64 from one zip package built from the checkout (no PyPI release), uploaded as an S3 object.
2. **The collector is the only writer.** An EventBridge Scheduler schedule starts one Collection Run a day. It downloads the database object to `/tmp`, ingests, runs one Calculation Run, and writes the file back with an S3 conditional `If-Match` on the ETag it downloaded. A concurrent write gets a 412 and fails the run, so no write is lost and no reserved concurrency is needed (a new account may not have the quota to reserve any).
3. **The API is read-only.** A Lambda Function URL with `AWS_IAM` authorization serves the existing FastAPI read endpoints from a read-only copy of the current database object, cached in `/tmp` by ETag. The OpenLineage receiver and the static dashboard are not mounted there (ADR 0027; spec 08). The Lambda-to-ASGI adapter is chosen through the ADR 0014 gate in its ticket.
4. **One private bucket** holds the database object, the Inboxes (ADR 0027) and the Lambda package: public access blocked, owner-enforced, TLS-only, encrypted (SSE-S3 by default, SSE-KMS with a customer key as an option), versioned, retained on `destroy` unless the operator opts in to deletion.
5. **Single account.** The FOCUS export bucket, the Athena workgroups and the Deployment are in one account (Q2 of the M6 design session).

## Consequences
Each Collection Run moves the whole database file in and out of S3. At about a million lines that file, cold start time with pyarrow, and the 10 GB `/tmp` ceiling are the limits to watch; none is measured yet. The API serves the snapshot the last Collection Run wrote, never a half-written one. A browser cannot sign SigV4, so the dashboard is used locally against a downloaded copy (spec 08).

## Alternatives rejected
- **Fargate or Lightsail containers:** always-on cost and a VPC or public IP, with no IAM authentication of their own.
- **App Runner:** closed to new customers.
- **Lambda with EFS or S3 Files:** needs a VPC, and NAT for AWS API access, which spec 05 rules out.
- **API Gateway private REST API:** needs a VPC interface endpoint (about $7.30 a month per zone) and callers inside the VPC.
- **Postgres or RDS:** spec 05 and ADR 0009; ADR 0018's revisit trigger still applies.

## Revisit triggers
- Cold start or Collection Run time at real scale exceeds the Lambda limits (the container-image fallback: ECR, needs Docker).
- More than one writer is needed (ADR 0018's trigger).
- Cross-account export buckets or member-account Athena history.
