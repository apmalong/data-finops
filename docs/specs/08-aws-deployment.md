# AWS deployment (M6)

Decided in the M6 design session (2026-11-06); each owner decision is logged in `docs/implementation/m6-decisions.md`. Topology in ADR 0026, ingress in ADR 0027, the deployed Cost Explanation in ADR 0028. Research notes: `docs/research/m6-aws-topology.md`, `m6-openlineage-ingress.md`, `m6-collector-iam.md`, `m6-pulumi-components-policies.md`. Terms (Deployment, Collection Run, Inbox, Collection Watermark) are in GLOSSARY.md.

## Scope
Reusable, customer-managed Pulumi Python components that deploy data-finops into **one** AWS account holding the FOCUS export bucket, the Athena workgroups and the Deployment. One example stack. Cross-account reads, multi-account aggregation, a hosted dashboard and enterprise auth are out of scope.

## Layout
- `infra/pulumi/aws/`: its own uv project (Pulumi never enters the `data_finops` wheel, ADR 0009): the components, one example stack, the package build script and their tests. State is a local `file://` backend under `.local/`; the program refuses any other backend (ADR 0001), as the reference workload does.
- `infra/pulumi/aws/policy/`: the policy pack, its own uv project and environment, because `pulumi-policy` 1.21.0 pins protobuf 5.
- Pins: the Pulumi SDK and CLI at 3.272.0, the newest release at least two weeks old (build-vs-reuse register), checked by a test against the installed CLI; pulumi-aws 7.52.1 (the reference workload's release, owner Q23, `docs/implementation/m6-decisions.md`); pulumi-policy 1.21.0 (#185, #188).
- `src/data_finops/providers/aws/`: the AWS collectors, behind a new optional `aws` extra (boto3, pinned). They hand the existing readers what they read today: Delivery files and a BatchGetQueryExecution response.
- `src/data_finops/collection/`: the Collection Run, a thin orchestrator over `providers.aws`, the ingest functions and `calculation` (architecture-boundaries.md).
- Lambda handlers are thin entry points over `collection` and `api`, like `cli`.

## Topology (ADR 0026)
- **Collector function:** the only writer. EventBridge Scheduler starts it (`schedule`, default `cron(0 6 * * ? *)` UTC; at most 2 retries; no flexible window). It downloads the database object to `/tmp`, runs one Collection Run, and uploads with `If-Match` on the downloaded ETag. A 412 fails the run before anything else is written; the next run starts again from the current object. Its handler is `data_finops.lambda_handlers.collector`; its settings come as one environment variable, `DATA_FINOPS_COLLECTOR_SETTINGS`, holding JSON with the `collect` TOML file's keys and validated the same way (#183). The database object is `database/data-finops.sqlite`; the first run writes it with `If-None-Match: *`. Exit codes 0 and 1 return the summary; exit code 2, a write conflict or bad settings raise `CollectionFailed`, so the invocation counts as an error and the scheduler retries it. One `PutObject` holds up to 5 GB; multipart upload is a revisit trigger.
- **API function:** a Function URL with `AWS_IAM` authorization; read-only. It serves the `/health` and `/v1/*` read endpoints from the current database object, cached in `/tmp` by ETag. The OpenLineage receiver and the static dashboard are not mounted. Cost Explanations use `explanationDetail` (ADR 0028). Its handler is `data_finops.lambda_handlers.api`, serving the app through Mangum 0.22.0 (#184). Each request asks S3 for the object with `If-None-Match` on the cached ETag, so an unchanged database is never downloaded again. Environment: `DATA_FINOPS_BUCKET` and `DATA_FINOPS_EXPLANATION_DETAIL` (default `minimal`).
- Both functions: Python 3.12, arm64, one zip built from the checkout, each with its own handler. `python -m deployment.package` (in `infra/pulumi/aws`) builds it into `build/data-finops-lambda.zip` before `pulumi up`, which refuses to run without it and uploads it as `lambda/data-finops-lambda.zip`; the zip's SHA-256 redeploys the functions when it changes (#186, #187). Measured 2026-11-07: 66 MB zipped, 173 MB unzipped, under Lambda's 250 MB.
- **Bucket:** the database object, `inbox/openlineage/`, `inbox/dbt/<invocation>/`, and the Lambda package. All four public access blocks; `BucketOwnerEnforced`; a bucket policy denying requests without TLS (`aws:SecureTransport`); default encryption SSE-S3, or SSE-KMS with Bucket Keys when `kmsKeyArn` is set; versioning on, noncurrent versions expire after `noncurrentVersionDays`; Inbox objects expire after `inboxRetentionDays`.

## Collection Run
1. Sources run in a fixed order: dbt artifacts from the Inbox, OpenLineage events from the Inbox, Athena history, FOCUS Deliveries. Every source is attempted even if an earlier one failed. Then one Calculation Run on whatever was stored.
2. **Deliveries:** list the export's `metadata/` prefix for the `billingLookbackPeriods` most recent Billing Periods (default 2, at least 1). An execution folder with a manifest is a complete Delivery. Follow the observed real layout (lowercase `billing_period=`, `-Manifest-FOCUS.json`), not the AWS docs' spelling. Ingest every Delivery newer than the current Export Snapshot of its Snapshot Scope (the configured account, the period, the export's source), oldest first, so the newest becomes current and the others are kept as Superseded Snapshots (#182), by downloading it to `/tmp` and passing it to the existing FOCUS reader unchanged. A folder without both manifests is still being written and is left for a later run; a folder whose name is not `<CreatedAt>-<executionId>` is warned about and ignored; a Delivery once taken (stored, replayed or rejected) is recorded in the Collection Watermark and never downloaded again (#182). Only the data files the manifest lists are downloaded, and only from that Delivery's folder in the export bucket; a manifest naming any other object is rejected unread (#182). A data file over 2 GiB, or a Delivery over 8 GiB, is rejected before any data is downloaded (#182; unmeasured on a large account, a revisit trigger). The export must use "create new" delivery (ADR 0020 takes `published_at` from the execution folder); an overwrite-mode layout is refused with a reason. No Data Exports API permission.
3. **Athena history:** for each configured workgroup, page `ListQueryExecutions` up to `maxHistoryPages` (default 200 pages of 50), call `BatchGetQueryExecution` only for IDs not yet stored, and store only finished queries (a `QUEUED` or `RUNNING` one stays unstored, so the next run fetches it again; storing it would make its finished report a flagged changed copy, #181) through the existing #61 allowlist, so `Query`, `ExecutionParameters`, the results location and free text are dropped before storage.
4. **Inboxes:** process keys not yet in the Collection Watermark with the existing caps (1 MiB per event, 512 MiB per dbt artifact); record each processed key.
5. **Collection Watermark:** stored in the database: Delivery folders ingested, Athena query IDs stored, Inbox keys processed. Never a clock time alone.
6. **Outcome:** rejected items are logged by position or key and reason, never content, and the run still calculates (the CLI's exit code 1 semantics). If any source failed (unreadable, access denied: exit code 2 semantics), the run calculates on what was stored and then ends as failed, so the scheduler retries; replay is idempotent.
7. **Warnings:** the history page cap was hit; a workgroup's complete, non-empty listing overlaps none of its stored Query Executions, so history may have expired unread (Athena keeps 45 days; detected from the data, not a stored clock, #181); an overwrite-mode layout; an Inbox key older than `inboxRetentionDays` would have expired.
8. **Summary:** one structured JSON log line per run: per source, counts stored, duplicate and rejected; the rejections by position or key and reason; the warnings; the Calculation Run ID. No custom metrics.
9. **`data-finops collect --db PATH --config FILE`** runs the same Collection Run from a laptop with the operator's own AWS profile. The settings file is TOML with the collector's keys. Its exit codes follow the CLI's (0, 1, 2).

## Reading a Deployment
- JSON only, signed with SigV4 by an IAM principal granted `lambda:InvokeFunctionUrl` (for example `curl --aws-sigv4` from curl 7.75, or awscurl). The stack outputs a reader policy for that grant.
- The dashboard: download the current database object and run `data-finops serve --db` locally.
- Kill switch: set the API function's reserved concurrency to 0, which deactivates the Function URL.

## IAM
Draft policies are in `docs/research/m6-collector-iam.md` and stay labelled unvalidated until the owner's wizard checks them with IAM Access Analyzer.
- **Collector role:** `s3:ListBucket` on the export bucket limited by `s3:prefix`; `s3:GetObject` on the export prefix; `kms:Decrypt` only if the export is SSE-KMS; `athena:ListQueryExecutions` and `BatchGetQueryExecution` on the configured workgroup ARNs only, the only Athena calls the collector makes (#181); `s3:GetObject` and `s3:PutObject` (conditional) on `database/data-finops.sqlite` only, `s3:GetObject` on its own bucket's `inbox/`, and `s3:ListBucket` on its own bucket; its own log group. An explicit deny on `athena:StartQueryExecution`, `StopQueryExecution`, `GetQueryResults` and `GetQueryRuntimeStatistics`.
- **API role:** `s3:GetObject` on the database object and `s3:ListBucket` on its own bucket; its own log group. Without `s3:ListBucket`, S3 answers a missing object with `AccessDenied`, not `NoSuchKey`, so both roles need it on the Deployment's own bucket for the first run and for an API serving before it (#183, #184).
- **Producer policy** (output): `s3:PutObject` on `inbox/*` only.
- **Reader policy** (output): `lambda:InvokeFunctionUrl` on the API function.
- **Deployer:** created by the owner, never by the stack; a draft least-privilege deployer policy is documented, unvalidated.

## Settings
Required: `aws:region`, `exportBucket`, `exportName`, `workgroups` (a non-empty list; the stack refuses an empty one). `exportPrefix` is optional: empty when the export has no S3 path prefix, as the collector reads it (#185). The collector also needs the account ID (BatchGetQueryExecution does not carry it); the stack passes its own. `data-finops collect`'s TOML file names the same settings in snake case (`account_id`, `workgroups`, `max_history_pages`, …) and adds `bucket`, `region` and an optional `profile` (#181).

| Setting | Default | Meaning |
|---|---|---|
| `billingLookbackPeriods` | 2 | Billing Periods listed each run (1 to 24) |
| `maxHistoryPages` | 200 | `ListQueryExecutions` pages per workgroup per run (1 to 1000) |
| `schedule` | `cron(0 6 * * ? *)` | Collection Run schedule, UTC |
| `collectorTimeoutSeconds`, `collectorMemoryMb` | 900, 2048 | Collector bounds |
| `apiTimeoutSeconds`, `apiMemoryMb` | 30, 1024 | API bounds |
| `apiReservedConcurrency` | unset | Optional cap on the API (new accounts may lack the quota) |
| `logRetentionDays` | 30 | Both log groups, created by the stack |
| `noncurrentVersionDays` | 30 | Expiry of old database object versions |
| `inboxRetentionDays` | 30 | Expiry of Inbox objects |
| `kmsKeyArn` | unset | Customer-managed key for the bucket (about $1 a month) |
| `explanationDetail` | `minimal` | Cost Explanation detail (ADR 0028) |
| `budgetMonthlyUsd`, `budgetEmail` | unset | Optional account-wide AWS Budget alert on forecast and actual spend; both or neither |
| `alarmEmail` | unset | Optional SNS topic and one alarm on the collector's `Errors` metric |
| `deleteDataOnDestroy` | false | Whether `destroy` deletes the bucket; by default it is retained |

Outputs: the bucket name, the API URL, the producer and reader policy ARNs, the two function names.

## Cost limits
AWS has no hard spending cap, and Budget actions cannot stop Lambda. Cost limits are therefore bounded resources (the timeouts, memory, one daily schedule with bounded retries, log retention, S3 expiries), the optional Budget alert, and the kill switch. The deployment explanation page gives the researched monthly estimates: under $1 at either scale, plus $1 with a customer-managed key.

## Policy pack
A small custom Python pack (Pulumi's open-source AWS packs are TypeScript only and stale). All mandatory, each checking literal inputs only (an unknown value at preview only yields an advisory):
1. S3: all four public access blocks; default encryption; a TLS-only bucket policy; versioning on.
2. Every Function URL is `AWS_IAM`.
3. Every Lambda function has a log group with retention set, and a timeout and memory within the stack's bounds.
4. The collector's and API's IAM policies use only allowlisted actions: no `*` action, nothing that runs queries or reads results; the API role is read-only on the bucket.
5. Stack level: no VPC, subnet, NAT gateway, EIP, RDS, EKS, ECS or EFS resource (spec 05).
6. A configured Budget alerts on both forecast and actual spend.

The policies live in a plain module; only `__main__.py` builds the `PolicyPack`, because constructing it blocks.

## Testing
Each seam test-first, with hand-made mutation checks recorded in the PR. No test touches a real account.
1. **`providers.aws` collectors** through their public functions with botocore's `Stubber`: Delivery discovery on committed fixtures shaped like the real anonymized layout with fake IDs; Athena paging, the cap and unseen-only fetches; `Query` dropped; Inbox listing and caps.
2. **The Collection Run** on a temporary database with stubbed clients, asserted only through `reporting` and the summary: idempotent replay, partial rejection, one source failing, the conditional upload and the 412 path.
3. **The Lambda handlers:** `handler(event, context)` with stubs; wiring and exit semantics only.
4. **`data-finops collect`** through `main(argv)`.
5. **The minimal Cost Explanation** on the existing `reporting` and HTTP seams, with the OpenAPI drift test.
6. **The Pulumi components under mocks:** resources, settings validation, outputs.
7. **The policy pack:** (a) unit cases, a passing and a violating one per policy; (b) the stack run under mocks, its recorded resources written as a state file and checked with `pulumi policy analyze --file`: zero violations, and a deliberately non-compliant fixture exits 1.
8. **The package build:** handler at the zip root, no `__pycache__` or test data, unzipped under 250 MB.

A new CI job, `infra`, installs the pinned Pulumi CLI and runs 6–8; the owner makes it a required check after its first green run.

## Documentation (spec 07)
- How-to: deploy to AWS; send OpenLineage events and dbt artifacts to the Inbox; read the deployed API and open the dashboard on a local copy; tear down, with the retention choices. Deploy and teardown commands are marked owner-run and are not executed by the page runner.
- Reference: the stack's settings and outputs, generated from the settings dataclass by `scripts/site`, with a drift test.
- Explanation: the deployment architecture, with a D2 diagram, the trust boundaries and the cost estimates.

## Owner-only steps
Each is a `wizard` script the owner runs; agents never run them.
- **Deploy and verify** (optional for the exit): dedicated deployer profile; optionally validate the draft policies with IAM Access Analyzer; `pulumi preview --policy-pack`; `pulumi up`; one Collection Run; read it back through the API; record the output in progress.md.
- **Teardown** (#26 joins M6): `pulumi destroy` of the Deployment and of the reference workload, and a check that nothing from either stack remains.

## Exit
Required: tests 6–8 pass offline in CI, and the policy pack rejects the non-compliant fixtures. Optional: the owner's deploy-and-verify wizard, recorded in progress.md only if run. Deployment only with the owner's explicit authorization and credentials.
