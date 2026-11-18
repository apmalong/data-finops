# AWS deployment (M6)

Decided in the M6 design session (2026-11-06). Each owner decision is logged in `docs/implementation/m6-decisions.md`. The topology is in ADR 0026, the ingress is in ADR 0027, and the deployed Cost Explanation is in ADR 0028. Research notes: `docs/research/m6-aws-topology.md`, `m6-openlineage-ingress.md`, `m6-collector-iam.md`, `m6-pulumi-components-policies.md`. The terms (Deployment, Collection Run, Inbox, Collection Watermark) are in GLOSSARY.md. M8 changes the API, the bucket, IAM and the wizards: spec 10 and ADR 0032.

## Scope
Reusable, customer-managed Pulumi Python components that deploy data-finops into **one** AWS account. That account holds the FOCUS export bucket, the Athena workgroups and the Deployment. There is one example stack. Cross-account reads, multi-account aggregation, a hosted dashboard and enterprise authentication are out of scope.

## Layout
- `infra/pulumi/aws/`: its own uv project, with the components, one example stack, the package build script and their tests. Pulumi never goes into the `data_finops` wheel (ADR 0009).
  - The state is a local `file://` backend in `.local/`. The program refuses all other backends (ADR 0001), as the reference workload does.
- `infra/pulumi/aws/policy/`: the policy pack. It is its own uv project with its own environment, because `pulumi-policy` 1.21.0 pins protobuf 5.
- Pins:
  - the Pulumi SDK and CLI at 3.272.0, the newest release that is at least two weeks old (build-vs-reuse register). A test checks it against the installed CLI.
  - pulumi-aws 7.52.1, the release of the reference workload (owner Q23, `docs/implementation/m6-decisions.md`)
  - pulumi-policy 1.21.0 (#185, #188)
- `src/data_finops/providers/aws/`: the AWS collectors, behind a new optional `aws` extra (boto3, pinned). They give the current readers what these readers read today: Delivery files and a BatchGetQueryExecution response.
- `src/data_finops/collection/`: the Collection Run. It is a thin orchestrator over `providers.aws`, the ingest functions and `calculation` (architecture-boundaries.md).
- The Lambda handlers are thin entry points over `collection` and `api`, as `cli` is.

## Topology (ADR 0026)

### Collector function
The collector is the only writer.
- EventBridge Scheduler starts it (`schedule`, default `cron(0 6 * * ? *)` UTC). It gets 2 retries at most, and the schedule has no flexible window.
- It downloads the database object to `/tmp`, runs one Collection Run, and uploads with `If-Match` on the downloaded ETag.
- A 412 fails the run before anything else is written. The next run starts again from the current object.
- Its handler is `data_finops.lambda_handlers.collector`.
- Its settings come in one environment variable, `DATA_FINOPS_COLLECTOR_SETTINGS`. This variable holds JSON with the keys of the `collect` TOML file, and it is validated in the same way (#183).
- The database object is `database/data-finops.sqlite`. The first run writes it with `If-None-Match: *`.
- Exit codes 0 and 1 return the summary. Exit code 2, a write conflict or bad settings raise `CollectionFailed`. Thus the invocation counts as an error, and the scheduler retries it.
- One `PutObject` holds up to 5 GB. Multipart upload is a revisit trigger.

### API function
- An API Gateway HTTP API in front of it, with an `AWS_IAM` route for each read under `/v1/` (spec 10 §The API, #290). It is read-only. The Function URL is removed.
- The `$default` stage limits the requests to `apiRateLimit` and `apiBurstLimit`.
- It serves the `/v1/*` read endpoints from the current database object. It keeps the object in `/tmp` by ETag. `/health` has no route.
- The OpenLineage receiver and the static dashboard are not mounted.
- Cost Explanations use `explanationDetail` (ADR 0028).
- Its handler is `data_finops.lambda_handlers.api`, which serves the app through Mangum 0.22.0 (#184).
- Each request asks S3 for the object with `If-None-Match` on the cached ETag. Thus an unchanged database is never downloaded again.
- Environment: `DATA_FINOPS_BUCKET` and `DATA_FINOPS_EXPLANATION_DETAIL` (default `minimal`).

### Both functions
- Python 3.12, arm64, and one zip built from the checkout. Each function has its own handler.
- `python -m deployment.package` (in `infra/pulumi/aws`) builds the zip into `build/data-finops-lambda.zip` before `pulumi up`. It installs the versions in `uv.lock` with their hashes, then the wheel without dependencies (#209).
- `pulumi up` refuses to run without the zip. It uploads the zip as `lambda/data-finops-lambda.zip`. When the SHA-256 of the zip changes, the functions are deployed again (#186, #187).
- Measured on 2026-11-07: 66 MB zipped, and 173 MB unzipped, which is below the 250 MB limit of Lambda.

### Bucket
The bucket holds the database object, `inbox/openlineage/`, `inbox/dbt/<invocation>/` and the Lambda package. When `costDimensions` is set, it also holds `config/cost-dimensions.toml` (spec 09).
- all four public access blocks
- `BucketOwnerEnforced`
- a bucket policy that denies requests without TLS (`aws:SecureTransport`)
- default encryption SSE-S3, or SSE-KMS with Bucket Keys when `kmsKeyArn` is set
- versioning on, with noncurrent versions that expire after `noncurrentVersionDays`
- Inbox objects expire after `inboxRetentionDays`.

## Collection Run
1. The sources run in a fixed order: dbt artifacts from the Inbox, OpenLineage events from the Inbox, Athena history, FOCUS Deliveries.
   - Before the sources, the run reads the Cost Dimensions (spec 09). If it cannot read or use them, it stops before it collects anything, and no Calculation Run occurs.
   - Each source is tried, even if an earlier one failed.
   - Then a Calculation Run occurs for each Snapshot Scope, on the data that was stored.
2. **Deliveries:** list the `metadata/` prefix of the export, for the `billingLookbackPeriods` most recent Billing Periods.
   - The default is 2, and the minimum is 1.
   - An execution folder with a manifest is a complete Delivery.
   - Follow the real layout as it was observed (lowercase `billing_period=`, `-Manifest-FOCUS.json`), not the names in the AWS documentation.
   - Ingest each Delivery that is newer than the current Export Snapshot of its Snapshot Scope (the configured account, the period, the source of the export), oldest first. Thus the newest becomes current, and the others are kept as Superseded Snapshots (#182).
   - To ingest a Delivery, download it to `/tmp` and give it to the current FOCUS reader, unchanged.
   - If a folder does not have both manifests, Data Exports has not finished it yet. It stays for a later run.
   - A folder whose name is not `<CreatedAt>-<executionId>` gets a warning, and it is ignored.
   - A Delivery that a run took one time (stored, replayed or rejected) is recorded in the Collection Watermark. It is never downloaded again (#182).
   - Only the data files that the manifest lists are downloaded, and only from the folder of that Delivery in the export bucket. A manifest that names a different object is rejected, and that object is not read (#182).
   - A data file over 2 GiB, or a Delivery over 8 GiB, is rejected before any data is downloaded (#182). This is not measured on a large account, and it is a revisit trigger.
   - The export must use "create new" delivery, because ADR 0020 takes `published_at` from the execution folder. A layout in overwrite mode is refused, with a reason. Overwrite mode puts the files directly under the Billing Period. It makes no Delivery folder. Create-new mode also keeps the manifest of the newest Delivery there. When a Billing Period has a Delivery folder, the run ignores the files directly under it (#305).
   - There is no Data Exports API permission.
3. **Athena history:** for each configured workgroup, do these steps:
   - Page `ListQueryExecutions` up to `maxHistoryPages` (default 200 pages of 50).
   - Call `BatchGetQueryExecution` only for IDs that are not stored yet.
   - Store only finished queries, through the current #61 allowlist. Thus `Query`, `ExecutionParameters`, the results location and free text are removed before storage.
   - A `QUEUED` or `RUNNING` query is not stored, so the next run fetches it again. If it were stored, its finished report would be a changed copy with a flag (#181).
4. **Inboxes:** process the Inbox objects that are not yet in the Collection Watermark.
   - The watermark keys them by S3 key and ETag, so an overwritten key is collected again (Q40).
   - The current limits apply: 1 MiB for each event, and 512 MiB for each file.
   - Record each processed object.
5. **Collection Watermark:** it is stored in the database.
   - It holds the Delivery folders ingested, the Athena query IDs stored, and the Inbox objects processed (by key and ETag).
   - It is never a clock time alone.
6. **Outcome:** rejected items are logged by position or key and reason, never by content. The run still calculates.
   - This is the meaning of exit code 1 of the CLI.
   - If a source failed (it could not be read, or access was denied: the meaning of exit code 2), the run calculates on the stored data. Then it ends as failed, so the scheduler retries it. Replay is idempotent.
7. **Warnings:** a run shows a warning in these cases:
   - The history page limit was reached.
   - The complete list of a workgroup is not empty, but it overlaps none of its stored Query Executions. Thus history can have expired before a run read it. Athena keeps 45 days. This is detected from the data, not from a stored clock (#181).
   - The layout is in overwrite mode.
   - A dbt Inbox folder still waits for one of its files.
   - An OpenLineage event is not supported.
   - BatchGetQueryExecution did not return some Athena IDs.
   - Planned but not built: an Inbox key older than `inboxRetentionDays`. The collector does not get this setting (#211).
8. **Summary:** one structured JSON log line for each run. It has these items:
   - for each source, the counts of stored, duplicate and rejected items
   - the rejections, by position or key and reason
   - the warnings
   - the Calculation Run IDs, one for each Snapshot Scope

   There are no custom metrics.
9. **`data-finops collect --db PATH --config FILE`** runs the same Collection Run from a laptop, with the AWS profile of the operator.
   - The settings file is TOML, with the keys of the collector.
   - Its exit codes follow the CLI (0, 1, 2).

## Read a Deployment
- JSON only. Each request is signed with SigV4 (service `execute-api`) by an IAM principal that has `execute-api:Invoke` on the route. Examples: `data-finops <read> --deployment NAME`, `curl --aws-sigv4` from curl 7.75, or awscurl. The stack outputs a reader policy that gives this permission.
- The dashboard: download the current database object, and run `data-finops serve --db` locally.
- Kill switch: set `apiRateLimit` and `apiBurstLimit` to 0. API Gateway then answers 429 to each request (spec 10).

## IAM
The policies that `deployment/compute.py` grants supersede the research drafts in `docs/research/m6-collector-iam.md`. After `pulumi up`, the deploy wizard (#192) validates these policies and the bucket policy with IAM Access Analyzer. It reads them from the real state. Until the owner runs the wizard, they are not validated.

### Collector role
- `s3:ListBucket` on the export bucket, limited by `s3:prefix`, and `s3:GetObject` on the export prefix.
- `athena:ListQueryExecutions` and `BatchGetQueryExecution` on the configured workgroup ARNs only. These are the only Athena calls that the collector makes (#181).
- `s3:GetObject` and `s3:PutObject` on `database/data-finops.sqlite` only. Each write has `If-Match` or `If-None-Match`, which the code sends. To enforce that in IAM is a revisit trigger.
- `s3:GetObject` on `inbox/` of its own bucket, and `s3:ListBucket` on its own bucket.
- If `costDimensions` is set, `s3:GetObject` on `config/cost-dimensions.toml` of its own bucket (spec 09). The API role does not get it.
- its own log group
- An explicit deny on `athena:StartQueryExecution`, `StopQueryExecution`, `GetQueryResults` and `GetQueryRuntimeStatistics`.
- If `kmsKeyArn` is set, `kms:Decrypt` and `kms:GenerateDataKey` on that key. S3 requires these to read and write SSE-KMS objects (#187).
- An SSE-KMS export is not supported yet. It is not verified that Data Exports delivers into one (`docs/research/m6-collector-iam.md`). This is a revisit trigger.

### API role
- `s3:GetObject` on the database object, and `s3:ListBucket` on its own bucket.
- its own log group
- Without `s3:ListBucket`, S3 answers a request for an object that does not exist with `AccessDenied`, not `NoSuchKey`. Thus both roles require it on the bucket of the Deployment. The first run requires it, and so does an API that serves before the first run (#183, #184).
- If `kmsKeyArn` is set, `kms:Decrypt` on that key (#187).

### Other policies
- **Producer policy** (output): `s3:PutObject` on `inbox/*` only.
  - If `kmsKeyArn` is set, also `kms:GenerateDataKey` on that key. S3 requires it to write an SSE-KMS object (#270).
- **Reader, consumer and operator policies** (outputs): `execute-api:Invoke` on the routes of the Control Role (spec 10). Until the Command routes come (#291), each of the three gives the read routes only. A path parameter is `*` in the ARN, and so is the stage.
- **API Gateway:** a Lambda permission lets `apigateway.amazonaws.com` invoke the API function, for `GET /v1/*` of this API only.
- **Scheduler role:** `lambda:InvokeFunction` on the collector function only. EventBridge Scheduler assumes this role to start the Collection Run (#187).
- **Deployer:** the owner creates it, never the stack. A draft deployer policy with least privilege is `infra/pulumi/aws/iam/deployer-policy.draft.json`. It is not validated until the IAM Access Analyzer step of the deploy wizard (#192). It keeps `lambda:GetFunctionUrlConfig` and `lambda:DeleteFunctionUrlConfig` so that `pulumi up` can delete the Function URL of an older Deployment (#290). Remove them when no Deployment has one.

## Settings
Required: `aws:region`, `exportBucket`, `exportName` and `workgroups`. `workgroups` is a list that must not be empty, and the stack refuses an empty list.

`exportPrefix` is optional. It is empty when the export has no S3 path prefix, as the collector reads it (#185). The collector also requires the account ID, because BatchGetQueryExecution does not give it. The stack gives its own account ID.

The TOML file of `data-finops collect` names the same settings in snake case (`account_id`, `workgroups`, `max_history_pages`, …). It adds `bucket`, `region` and an optional `profile` (#181). It can also name `cost_dimensions_key`, the key of a Cost Dimensions file in the bucket (spec 09).

| Setting | Default | Meaning |
|---|---|---|
| `billingLookbackPeriods` | 2 | Billing Periods listed in each run (1 to 24) |
| `maxHistoryPages` | 200 | `ListQueryExecutions` pages for each workgroup in each run (1 to 1000) |
| `schedule` | `cron(0 6 * * ? *)` | Collection Run schedule, UTC |
| `collectorTimeoutSeconds`, `collectorMemoryMb` | 900, 2048 | Collector limits |
| `apiTimeoutSeconds`, `apiMemoryMb` | 30, 1024 | API limits |
| `apiReservedConcurrency` | unset | An optional limit on the API. New accounts can lack the quota. |
| `apiRateLimit`, `apiBurstLimit` | 10, 20 | The rate limits of the API stage (0 to 10,000, and 0 to 5,000). Both at 0 is the kill switch (spec 10) |
| `logRetentionDays` | 30 | Both log groups, which the stack creates |
| `noncurrentVersionDays` | 30 | Expiry of old database object versions |
| `inboxRetentionDays` | 30 | Expiry of Inbox objects |
| `kmsKeyArn` | unset | A customer-managed key for the bucket (about $1 a month). The key ARN only: not an alias, and no wildcard (#270) |
| `explanationDetail` | `minimal` | Cost Explanation detail (ADR 0028) |
| `budgetMonthlyUsd`, `budgetEmail` | unset | An optional AWS Budget alert for the whole account, on forecast and actual spend. Set both or neither. |
| `alarmEmail` | unset | An optional SNS topic and one alarm on the `Errors` metric of the collector |
| `deleteDataOnDestroy` | false | Whether `destroy` deletes the bucket. By default, the bucket is kept. |
| `costDimensions` | unset | An optional path to a Cost Dimensions file (spec 09). The stack uploads it to `config/cost-dimensions.toml`, and the collector applies it. |

Outputs: the bucket name, the API URL, the ARNs of the producer, reader, consumer and operator policies, and the two function names.

## Cost limits
AWS has no hard limit on spend, and Budget actions cannot stop Lambda. Thus the cost limits are these:
- limited resources: the timeouts, the memory, one daily schedule with a limited number of retries, the log retention and the S3 expiries
- the optional Budget alert
- the kill switch

The deployment explanation page gives the researched monthly estimates. The cost is less than $1 at the two scales while the Lambda free tier covers it. Without the free tier, it is about $1.20 at the larger scale. A customer-managed key adds $1.

## Policy pack
A small custom Python pack. The open-source AWS packs of Pulumi are TypeScript only, and they are stale. All policies are mandatory. Each policy checks only literal inputs. A value that is unknown at preview gives only an advisory.
1. S3: all four public access blocks, default encryption, a TLS-only bucket policy, and versioning on.
2. Each API Gateway route is `AWS_IAM`, and no function has a Function URL. Each stage sets a rate limit and a burst limit of 0 or more (spec 10).
3. Each Lambda function has its log group, `/aws/lambda/<name>`, with retention set. It sets its timeout and memory explicitly.
   - The timeout and memory must be in the limits of spec 08 (1–900 s, 128–10,240 MB).
   - The pack cannot read the settings of the stack. Thus it checks these limits, not the configured values (#189).
4. The IAM policies of the collector and the API use only allowlisted actions.
   - No `*` action, and nothing that runs queries or reads results.
   - The API role is read-only on the bucket.
   - A `kms:` action names only a KMS key, never another resource (#270).
5. Stack level: no VPC, subnet, NAT gateway, EIP, RDS, EKS, ECS or EFS resource (spec 05).
6. A configured Budget sends alerts on both forecast and actual spend.
7. The public demo's CloudFront distribution: each origin uses origin access control, and each cache behavior redirects HTTP to HTTPS or allows HTTPS only (spec 09).

The policies are in a plain module. Only `__main__.py` builds the `PolicyPack`, because the construction blocks. `pulumi policy analyze` gives resource policies the inputs of each resource, and stack policies the outputs of the stack. Thus a state written for it has both (#189).

## Tests
Each seam is test-first, with hand-made mutation checks recorded in the PR. No test touches a real account.
1. **The `providers.aws` collectors**, through their public functions with the `Stubber` of botocore.
   - Delivery discovery on committed fixtures. These have the shape of the real anonymized layout, with fake IDs.
   - Athena pages, the page limit, and fetches of only the IDs not seen before
   - `Query` removed.
   - the Inbox list and its limits
2. **The Collection Run** on a temporary database with stubbed clients, asserted only through `reporting` and the summary.
   - It covers idempotent replay, partial rejection, a failure of one source, the conditional upload and the 412 path.
3. **The Lambda handlers:** `handler(event, context)` with stubs. Only how they connect the parts, and the exit semantics.
4. **`data-finops collect`** through `main(argv)`.
5. **The minimal Cost Explanation** on the current `reporting` and HTTP seams, with the OpenAPI drift test.
6. **The Pulumi components under mocks:** resources, settings validation, outputs.
7. **The policy pack:**
   - (a) unit cases: one that passes and one that violates, for each policy.
   - (b) the stack, run under mocks. Its recorded resources are written as a state file and checked with `pulumi policy analyze --file`. It must have zero violations, and a fixture that is not compliant on purpose must exit with 1.
8. **The package build:** the handler is at the root of the zip, and there is no `__pycache__` or test data.
   - The zip is below 250 MB unzipped.
   - Each distribution is at its `uv.lock` version.

A new CI job, `infra`, installs the pinned Pulumi CLI and runs 6–8. The owner makes it a required check after its first green run.

## Documentation (spec 07)
- How-to: deploy to AWS. Send OpenLineage events and dbt artifacts to the Inbox. Read the deployed API, and open the dashboard on a local copy. Tear down, with the retention choices. The deploy and teardown commands are marked owner-run, and the page runner does not run them.
- Reference: the settings and outputs of the stack. `scripts/site` generates them from the settings dataclass, with a drift test.
- Explanation: the deployment architecture, with a D2 diagram, the trust boundaries and the cost estimates.

## Owner-only steps
Each step is a `wizard` script that the owner runs. Agents never run them.
- **Deploy and verify** (optional for the exit):
  - a dedicated deployer profile
  - as an option, validate the draft policies with IAM Access Analyzer
  - `pulumi preview --policy-pack`
  - `pulumi up`
  - one Collection Run
  - read the result back through the API
  - record the output in progress.md
- **Teardown** (#26 joins M6): `pulumi destroy` of the Deployment and of the reference workload. Then a check that nothing from the two stacks remains.

## Exit
Required: tests 6–8 pass offline in CI, and the policy pack rejects the fixtures that are not compliant. Optional: the deploy-and-verify wizard of the owner, recorded in progress.md only if it was run. A deployment occurs only with the explicit authorization and the credentials of the owner.
