# M6 AWS deployment topology: primary-source facts for OQ-5

**Status:** research note (facts, not decisions). All sources checked **2026-11-06** unless marked otherwise. Prices are for **us-east-1** and come from the AWS public Price List offer files (`https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/<Service>/current/us-east-1/index.json`, an unauthenticated download; the `publicationDate` of each file is given) or from the AWS pricing and documentation pages named. No AWS API call was made, no credentials were used, and no pulumi or aws CLI was run. Anything not confirmed from a primary source is marked **UNVERIFIED**.

Question (OQ-5): which deployment topology keeps AWS operating cost lowest at small scale (a few thousand to about 1M billing lines a month), given the spec 05 limits: no mandatory NAT, VPC, RDS, Marquez or Kubernetes. The store is a single SQLite file in WAL mode (ADR 0018).

---

## 1. AWS Lambda limits, runtimes and pricing

Source: Lambda quotas, https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html

| Item | Value (verbatim or close paraphrase) |
|---|---|
| .zip package | "50 MB (zipped, when uploaded through the Lambda API or SDKs). Upload larger files with Amazon S3." / "250 MB The maximum size of the contents of a deployment package, including layers and custom runtimes. (unzipped)" |
| Container image | "10 GB (maximum uncompressed image size, including all layers)" |
| Units | "The Lambda documentation, log messages, and console use the abbreviation MB (rather than MiB) to refer to 1,024 KB." |
| Layers | 5 per function |
| `/tmp` | "Between 512 MB and 10,240 MB, in 1-MB increments" |
| Timeout | "900 seconds (15 minutes)" (90 min only for Lambda Managed Instances async/ESM) |
| Memory | "128 MB to 10,240 MB, in 1-MB increments"; "At 1,769 MB, a function has the equivalent of one vCPU." |
| Invocation payload | "6 MB each for request and response (synchronous)"; "200 MB for each streamed response"; "1 MB (asynchronous)" |
| Streamed-response bandwidth | uncapped for the first 6 MB, then 2 MBps |
| Concurrency | 1,000 by default. The page also says: "New AWS accounts have reduced concurrency and memory quotas ... AWS raises these quotas automatically based on your usage." The reduced numbers are not stated (**UNVERIFIED**). |
| Env vars | 4 KB aggregate |

**Runtimes** (https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html):
- `python3.12` (AL2023): deprecated Oct 31, 2028.
- `python3.13` and `python3.14` (AL2023): deprecated Jun 30, 2029.
- `python3.15` is in public preview and not for production.
- "All supported Lambda runtimes support both x86_64 and arm64 architectures."

**New Lambda products seen on the quotas page.** "Lambda Web Functions" (serverless HTTP servers: 512 MB unzipped package, 2 GB memory, 2 vCPU, 30 s default request timeout with range 3–900, "Private endpoint access with AWS PrivateLink: Not available") and "Lambda MicroVMs" are listed. This note did not evaluate them beyond that listing, so their fit for this app is **UNVERIFIED**.

**Pricing** (Price List `AWSLambda`, publicationDate 2026-10-30, plus https://aws.amazon.com/lambda/pricing/):

| Dimension | us-east-1 price |
|---|---|
| Requests (x86 and Arm) | $0.20 per 1M ($0.0000002/request) |
| Duration x86, tier 1 (0–6B GB-s) | $0.0000166667 per GB-s |
| Duration Arm, tier 1 (0–7.5B GB-s) | $0.0000133334 per GB-s |
| Ephemeral storage | $0.0000000309 per GB-s. That only `/tmp` above the 512 MB default is billed is **UNVERIFIED** here; the page's table did not render. |
| Response streaming | "You can stream the first 6 MB per request at no cost"; then $0.008/GB, with a 100 GB/month free tier |
| Free tier | "one million requests and 400,000 GB-seconds per month" (Price List: `Global-Request` 0–1,000,000 at $0; `Global-Lambda-GB-Second` 0–400,000 at $0) |

### Does pyarrow + pydantic-core fit a .zip?

I measured this with `pip download --only-binary=:all: --platform manylinux_2_28_<arch> --python-version 3.12` into the scratchpad. The figures are wheel metadata only (sum of `ZipInfo.file_size`). Nothing was installed or executed. The newest versions that satisfy `pyproject.toml` were selected on 2026-11-06.

| Wheel (cp312) | x86_64 zipped / unzipped | aarch64 zipped / unzipped |
|---|---|---|
| pyarrow 24.0.0 (manylinux_2_28) | 48.9 MB / **153.9 MB** | 45.7 MB / **140.0 MB** |
| pydantic-core 2.50.4 (manylinux2014) | 2.3 MB / 5.4 MB | 2.1 MB / 4.9 MB |
| pydantic 2.14.3 | 0.5 / 2.0 MB | same |
| jsonschema 4.26.0 + deps (referencing, rpds-py, attrs, specs) | ~0.6 / ~1.9 MB | same |
| boto3 1.43.125 + botocore 1.43.125 + s3transfer etc. | ~16.5 / ~23 MB | same |
| pyarrow 18.1.0 (lower bound, x86_64) | 40.1 MB / 131.1 MB | – |

These are decimal MB (10^6 bytes). The full arm64 closure (pyarrow, pydantic, jsonschema, boto3 and their dependencies) unzips to **172.2 MB = 164.2 MiB**, which is under the 250 MB unzipped limit with about 85 MiB to spare. Not measured: the app code, `rfc3339-validator`, and the `api` extra (fastapi, starlette, uvicorn, plus any Lambda HTTP adapter); these are **UNVERIFIED** but likely only a few MB. Even the compressed pyarrow wheels alone (45.7–48.9 MB) come close to the 50 MB direct-upload limit. Adding botocore (16.2 MB zipped) goes over it, so the .zip would need to be uploaded through S3, which the quotas page allows. A container image (10 GB) removes the size question. Cold-start import time for pyarrow is **UNVERIFIED**.

## 2. SQLite on Lambda

**sqlite.org guidance**
- https://www.sqlite.org/wal.html: "All processes using a database must be on the same host computer; WAL does not work over a network filesystem. This is because WAL requires all processes to share a small amount of memory..."
- https://www.sqlite.org/useovernet.html:
  - "SQLite relies on exclusive locks for write operations, and those have been known to operate incorrectly for some network filesystems. This has led to database corruption."
  - "Rely upon it at your (and your customers') peril."
  - The page lists three options: a client/server DB, WAL with every read and write on the DB host behind a proxy, or "Use SQLite in rollback mode ... multiple simultaneous readers or one writer, but not simultaneous readers and writers."

**(a) Download from S3 to /tmp, modify, upload.** `/tmp` is local to the execution environment (512 MB to 10,240 MB), so WAL works there for the life of one environment.

Single writer:
- **Reserved concurrency.** https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html: reserved concurrency "sets both the maximum and minimum number of concurrent instances"; "Configuring reserved concurrency for a function incurs no additional charges"; "To intentionally throttle an Event Function, set its reserved concurrency to 0."
  - Caveat: "You can reserve up to the Unreserved account concurrency value minus 100." A new account with a reduced concurrency quota may not be able to reserve even 1. That is **UNVERIFIED**, because the reduced quota value is not published on the page.
- **S3 conditional writes** (https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html):
  - `If-None-Match: *` (create only) and `If-Match: <ETag>` (compare-and-swap) are supported on PutObject, CompleteMultipartUpload and CopyObject. Requests must be SigV4-signed.
  - `If-Match` needs `s3:PutObject` and `s3:GetObject`.
  - A mismatch returns `412 Precondition Failed`. "If multiple conditional writes or copies occur for the same object name, the first write operation to finish succeeds", and the others get 412.
  - A concurrent delete can produce `409 Conflict` (If-None-Match) or `404` (If-Match).
  - Bucket policies can enforce conditional writes (https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes-enforce.html, linked but not read).
  - Whether a failed (412) conditional PUT is billed as a PUT is **UNVERIFIED**; the pricing page does not say.
  - Put together, GET (note the ETag) → modify locally → PUT with If-Match gives optimistic concurrency without a lock service.
- With SSE-KMS plus a Bucket Key, replicated objects can get a different ETag (bucket-key page). This affects replication only, not same-object If-Match.

**(b) EFS.** VPC is **required**. https://docs.aws.amazon.com/lambda/latest/dg/configuration-filesystem-efs.html: "A function connects to a file system over the local network in a VPC". It needs mount targets per AZ and NFS port 2049 in the security groups. A VPC-attached function loses default internet access. https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc-internet.html: "Connecting a function to a public subnet doesn't give it internet access"; private subnets need a NAT gateway. An S3 Gateway endpoint is free ("There is no cost for this option"). EFS Standard storage is $0.30/GB-month, and Elastic Throughput writes are $0.06/GB (Price List `AmazonEFS`, 2026-11-04). The sqlite.org network-filesystem caveats apply to EFS (NFS). EFS's own SQLite/NFS locking guarantees were not read (**UNVERIFIED**).

**(c) S3 Files** (new; https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-files.html and https://docs.aws.amazon.com/lambda/latest/dg/configuration-filesystem-s3files.html).
- It is "Built using Amazon EFS", supports NFS 4.1/4.2, and offers "read-after-write data consistency, file locking, and POSIX permissions".
- Lambda prerequisites: "A Lambda function in the same VPC as the mount target", so a **VPC is required**.
- Storage on the high-performance tier is $0.30/GB-month (Price List `AmazonS3` `USE1-Files-TimedStorage-ByteHrs`, 2026-11-04).
- It is still a network filesystem, so the WAL caveat above applies.
- A Lambda function can use EFS or S3 Files, not both.

**(c') Mountpoint for S3** (https://docs.aws.amazon.com/AmazonS3/latest/userguide/mountpoint.html): it "cannot modify existing files ... does not support ... file locking". That makes it unusable for a SQLite database file. Whether it can run inside Lambda at all is **UNVERIFIED** (the docs list it as Linux-only).

## 3. HTTPS ingress with IAM

**Lambda Function URLs**
- https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html: `AuthType` is either `AWS_IAM` ("only authenticated users and roles") or `NONE` (public; the resource policy must still grant access). Since October 2025, new URLs need both `lambda:InvokeFunctionUrl` and `lambda:InvokeFunction`. Use the `lambda:InvokedViaFunctionUrl` and `lambda:FunctionUrlAuthType` condition keys; an SCP can deny any non-`AWS_IAM` URL.
- https://docs.aws.amazon.com/lambda/latest/dg/urls-configuration.html:
  - Endpoint format: `https://<url-id>.lambda-url.<region>.on.aws`.
  - "You can access your function URL through the public Internet only. While Lambda Event Functions do support AWS PrivateLink, function URLs do not."
  - Throttle with reserved concurrency (429). Setting it to 0 deactivates the URL.
- Cost (https://docs.aws.amazon.com/lambda/latest/dg/furls-http-invoke-decision.html): "There are no additional charges for the URL endpoint itself."
- Payload limits: 6 MB sync request/response, 200 MB streamed response (quotas page). Response streaming is available "through a function URL using the ResponseStream invoke mode" (pricing page).

**API Gateway**
- HTTP API IAM auth (https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-access-control-iam.html): clients "must use ... SigV4"; "Resource policies aren't currently supported for HTTP APIs."
- Pricing (Price List `AmazonApiGateway`, 2026-10-13): HTTP API $1.00/M for the first 300M, REST $3.50/M for the first 333M. Free tier: 1M calls/month each, "for up to 12 months" (https://aws.amazon.com/api-gateway/pricing/).
- Private APIs (https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-private-apis.html): "A private API is a REST API that is only callable from within an Amazon VPC" through an interface VPC endpoint; "Only REST APIs are supported."
- Interface endpoint cost: $0.01 per endpoint-hour per AZ (Price List `AmazonVPC` `USE1-VpcEndpoint-Hours`, 2026-11-05) plus $0.01/GB processed (https://aws.amazon.com/privatelink/pricing/). One AZ for 730 h is $7.30/month; two AZs is $14.60/month.

**Is there an IAM-only HTTPS endpoint without a VPC?** Yes, two:
1. A Lambda Function URL with `AuthType=AWS_IAM`, at no extra charge.
2. An API Gateway HTTP or REST API with IAM authorization.

Both are reachable over the public internet, but every request must be SigV4-signed by a principal that holds the permission. "Private" (network-isolated) needs a VPC plus an interface endpoint (REST only).

## 4. Containers

**ECS Fargate**
- https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-task-networking.html: every task gets an ENI in **your VPC subnets**. To pull an image, the task needs "a route to the internet": a public IP in a public subnet, a NAT gateway, or ECR interface endpoints.
- Price List `AmazonECS` (2026-10-19), us-east-1:
  - x86: $0.04048 per vCPU-hour and $0.004445 per GB-hour.
  - ARM: $0.03238 per vCPU-hour and $0.00356 per GB-hour.
  - Ephemeral storage beyond 20 GB: $0.000111/GB-hour.
- Per-second billing, "1-minute minimum". The smallest task is "0.25 vCPU" with 0.5, 1 or 2 GB (https://aws.amazon.com/fargate/pricing/).
- Public IPv4: $0.005/hour in use (Price List `AmazonVPC` `USE1-PublicIPv4:InUseAddress`).
- NAT gateway: the VPC pricing page quotes $0.045/hour + $0.045/GB for US East (Ohio). The us-east-1 rate was not read (**UNVERIFIED**, and avoided anyway).
- ALB: $0.0225/hour + $0.008/LCU-hour (Price List `AWSELB`, 2026-11-05).
- ECR storage: $0.10/GB-month (Price List `AmazonECR`, 2026-09-11).
- A bare Fargate task with a public IP has **no TLS termination and no IAM auth** in front of it. Those come from an ALB (auth via OIDC/Cognito, not SigV4; **UNVERIFIED** for IAM) or from an app-level token.

**AWS App Runner is closed to new customers.**
- https://docs.aws.amazon.com/apprunner/latest/relnotes/welcome.html: "AWS App Runner will no longer be open to new customers starting April 30, 2026."
- https://docs.aws.amazon.com/apprunner/latest/dg/apprunner-availability-change.html: existing customers continue; "we do not plan to introduce new features"; AWS recommends **ECS Express Mode**, which provisions "an ECS service on Fargate, an Application Load Balancer, auto scaling, and networking". There is "no additional charge" for Express Mode, but it includes an ALB.

**Lightsail containers** (https://aws.amazon.com/lightsail/pricing/):
- Nano (0.25 shared vCPU, 512 MB): $7/month per node.
- Micro (1 GB): $10/month.
- 500 GB transfer included.
- Whether containers get an IAM role for S3 access or IAM-based ingress auth is **UNVERIFIED**.

**Minimum always-on (730 h/month):**
- Fargate ARM 0.25 vCPU / 0.5 GB: 0.25×0.03238 + 0.5×0.00356 = $0.009875/h, or **$7.21**. Adding a public IPv4 ($3.65) gives **$10.86**.
- The same task on x86: 0.25×0.04048 + 0.5×0.004445 = $0.0123425/h, or $9.01. With public IPv4: $12.66.
- Lightsail Nano: **$7.00**.
- Whether 0.5 GB is enough RAM for pyarrow + FastAPI is **UNVERIFIED**.

## 5. Scheduling

- **EventBridge Scheduler pricing** (Price List `AWSEvents`, 2026-09-24): `USE1-ScheduledInvocation` is free for 0–14,000,000 invocations a month, then $1.00/M. https://aws.amazon.com/eventbridge/pricing/ says "Unused free usage does not roll over".
- **Features** (https://docs.aws.amazon.com/scheduler/latest/UserGuide/what-is-scheduler.html):
  - cron, rate and one-time schedules
  - templated targets including Lambda
  - "at-least-once event delivery". The ingest job must be idempotent; the existing idempotent ingest matches this.
  - flexible time windows (`Mode: FLEXIBLE`, `MaximumWindowInMinutes`; https://docs.aws.amazon.com/scheduler/latest/UserGuide/managing-schedule-flexible-time-windows.html). The allowed max range was not shown on that page (**UNVERIFIED**).
- **RetryPolicy** (https://docs.aws.amazon.com/scheduler/latest/APIReference/API_RetryPolicy.html): `MaximumRetryAttempts` 0–185; `MaximumEventAgeInSeconds` 60–86,400.
- **EventBridge scheduled rules** (https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-create-rule-schedule.html):
  - "Scheduled rules are a legacy feature of EventBridge ... We recommend that you use Scheduler".
  - "All scheduled events use UTC+0 time zone".
  - Pricing for scheduled rules was not found on the pricing page (**UNVERIFIED**).

## 6. Encryption at rest

- **S3** (https://docs.aws.amazon.com/AmazonS3/latest/userguide/default-encryption-faq.html): since January 5, 2023, SSE-S3 is applied to all new objects "at no additional cost", and it cannot be disabled.
- **SSE-KMS key cost** (Price List `awskms`, 2026-10-26; https://aws.amazon.com/kms/pricing/):
  - A customer-managed key is **$1/month** (prorated hourly). Rotation adds $1/month per key for the first and second rotations, capped after that.
  - AWS-managed keys (`aws/s3`) have no key charge, but "you will be charged on any API request made to AWS-managed keys".
  - Symmetric requests cost $0.03 per 10,000, with 20,000 free requests a month.
- **S3 Bucket Keys** (https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucket-key.html) "can reduce AWS KMS request costs by up to 99 percent".
- **CloudWatch Logs** (https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/encrypt-log-data-kms.html):
  - "Log group data is always encrypted ... AES-GCM" by default. A KMS key per log group is optional and needs a key-policy grant to `logs.<region>.amazonaws.com`.
  - Retention defaults to never expire ("stored ... indefinitely"). `retentionInDays` accepts 1, 3, 5, 7, 14, 30, 60, 90, ... 3653 (https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_PutRetentionPolicy.html).
  - Deletion "typically takes up to 72 hours" after expiry. Account-level retention policies exist, with a minimum of 30 days.
- **Lambda env vars** (https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars-encryption.html): "Lambda always provides server-side encryption at rest with an AWS KMS key. By default, Lambda uses an AWS managed key ... AWS doesn't charge you to use this key." A customer-managed key is optional at standard KMS prices. AWS recommends Secrets Manager for credentials.

## 7. Cost limits

- **AWS Budgets** (https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/):
  - "You can monitor and receive notifications on your budgets free of charge."
  - "Your first two action-enabled budgets are free ... per month", then $0.10/day per action-enabled budget.
  - Reports cost $0.01 each.
- **Data lag** (https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html): "AWS Budgets information is updated up to three times a day" and "You might incur additional costs ... that exceed your budget notification threshold before AWS Budgets can notify you."
- **Budget actions** (https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-controls.html, .../budgets-action-configure.html): apply an IAM policy, apply an SCP (management account only), or target specific EC2 or RDS instances, either automatically or after approval. **Lambda is not a listed target.** A budget action can attach a Deny IAM policy, but whether that stops a function that is already invoking (its execution role is not the budget-targeted principal by default) is **UNVERIFIED**.
- **Other caps under your control:**
  - Lambda reserved concurrency caps parallelism, and 0 stops invocations.
  - The Function URL rate is capped at 10× reserved concurrency (urls-configuration page).
  - CloudWatch Logs retention.
  - S3 lifecycle (including `AbortIncompleteMultipartUpload`, recommended on the conditional-writes page).
- **Hard spending cap.** None for paid accounts. The only "no charges" mechanism is the **Free account plan** (https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html): "No charges incur during usage", but the "account closes when credits are depleted or when the plan duration ends" (6 months). It also lacks some services, and joining AWS Organizations upgrades it to paid. That makes it unsuitable for production.

## 8. Rough monthly cost, us-east-1 (730 h; prices from §1–§6)

Assumptions (illustrative, **not measured**):
- Lambda on arm64 with 1,024 MB memory.
- Daily ingest of 60 s (small) to 300 s at 2 GB (about 1M lines/month).
- API: 10,000 requests/month at 1 s each.
- SQLite file of 0.1 GB (small) to 12 GB (one year at about 1M lines/month, ~1 KB/row; **UNVERIFIED** density).
- Logs: 0.1–1 GB/month.
- S3 requests: 10,000 GET + 1,000 PUT.

**(i) Lambda + Function URL + S3 + Scheduler + Logs**

| Item | Arithmetic | Small | ~1M lines |
|---|---|---|---|
| Lambda duration (ingest) | 30 × 60 s × 1 GB = 1,800 GB-s; at scale 30 × 300 s × 2 GB = 18,000 GB-s | – | – |
| Lambda duration (API) | 10,000 × 1 s × 1 GB = 10,000 GB-s | – | – |
| Lambda total GB-s | 11,800 or 28,000, both < 400,000 free | $0.00 | $0.00 |
| (without free tier) | 11,800 × $0.0000133334 = $0.157; 28,000 × … = $0.373 | ($0.16) | ($0.37) |
| Lambda requests | 10,030 < 1M free (without free tier: 10,030 × $0.0000002 = $0.002) | $0.00 | $0.00 |
| Ephemeral /tmp (if set to 10 GB at scale) | (10 − 0.5) GB × 9,000 s × $0.0000000309 = $0.003 (whether the first 512 MB is free is **UNVERIFIED**) | ~$0 | ~$0.003 |
| Function URL | no charge | $0.00 | $0.00 |
| EventBridge Scheduler | 30 invocations < 14M free | $0.00 | $0.00 |
| S3 storage | 0.1 GB × $0.023 = $0.0023; 12 GB × $0.023 = $0.276 | $0.002 | $0.28 |
| S3 requests | 10,000 × $0.0000004 = $0.004; 1,000 × $0.000005 = $0.005 | $0.009 | $0.009 |
| CloudWatch Logs ingest | 0.1 GB × $0.50 = $0.05; 1 GB × $0.50 = $0.50 (5 GB/month free tier) | $0.00–0.05 | $0.00–0.50 |
| CloudWatch Logs storage (30-day retention) | 0.1–1 GB × $0.03 | $0.003 | $0.03 |
| KMS (SSE-S3 default) | $0 | $0.00 | $0.00 |
| (option) customer-managed KMS key | $1.00 + requests (Bucket Keys; < 20,000 free) | (+$1.00) | (+$1.00) |
| Budgets (notify only) | free | $0.00 | $0.00 |
| **Total** | | **≈ $0.01–$0.06** (≈ $0.23 without free tier) | **≈ $0.32–$0.82** (≈ $1.20 without free tier) |

Caveat at about 1M lines: download-modify-upload moves the whole DB file on each cold invocation. A 12 GB file needs `/tmp` close to its 10,240 MB ceiling, and it will not fit at all once it outgrows that limit. Same-region S3 to Lambda transfer is assumed free (**UNVERIFIED** here). The cost is small, but the latency and the size limit are real constraints.

**(ii) Fargate smallest always-on + S3**

| Item | Arithmetic | Monthly |
|---|---|---|
| Fargate ARM 0.25 vCPU / 0.5 GB | (0.25 × $0.03238 + 0.5 × $0.00356) × 730 = $0.009875 × 730 | $7.21 |
| (x86 instead) | (0.25 × $0.04048 + 0.5 × $0.004445) × 730 = $0.0123425 × 730 | ($9.01) |
| Public IPv4 for the task | $0.005 × 730 | $3.65 |
| ECR image storage (~0.3 GB, **UNVERIFIED** size) | 0.3 × $0.10 | $0.03 |
| S3 (as in (i)) | | $0.01–$0.29 |
| Logs (as in (i)) | | $0.00–$0.53 |
| Scheduler (if the job is a separate Fargate RunTask; **UNVERIFIED** target pricing) | < 14M free | $0.00 |
| **Total, no ALB / no TLS / no IAM auth** | | **≈ $10.9–$11.7 (ARM)**, ≈ $12.7–$13.5 (x86) |
| + ALB for HTTPS | $0.0225 × 730 = $16.43 + LCU-hours ($0.008 each; partial use) | **+ ≥ $16.43** |
| + NAT instead of public IP (Ohio rate; us-east-1 **UNVERIFIED**) | $0.045 × 730 = $32.85 + $0.045/GB | (+$32.85) |

## Implications for OQ-5 (options, not decisions)

1. **Lambda (zip via S3 upload, or container image) + Function URL `AWS_IAM` + S3 + Scheduler.** No VPC and no NAT. It costs about $0–$1/month across the stated range. Every request needs SigV4, and the endpoint is public-internet but IAM-gated. Single-writer comes from S3 `If-Match` compare-and-swap, optionally plus reserved concurrency 1 (account-quota caveat above). Risks: whole-file transfer per cold start grows with the DB, the 10 GB `/tmp` ceiling, pyarrow cold-start time (**UNVERIFIED**), and FastAPI needing a Lambda adapter (**UNVERIFIED**).
2. **Split: scheduled Lambda writer + read-only Lambda API.** Same services as option 1. Readers fetch an immutable snapshot (for example, a versioned key written by the writer), so the API never writes. This fits "one writer, many readers" without a network filesystem.
3. **Lambda + EFS or S3 Files.** This mounts the DB file directly, but it **requires a VPC** (against spec 05's "avoid mandatory VPC"). Internet egress then needs NAT or endpoints. sqlite.org advises against WAL on network filesystems, so it would need rollback-journal mode and a single writer.
4. **Fargate (or Lightsail Nano) always-on with the DB on local or ephemeral disk.** WAL is native here. The floor is about $7–$11/month before HTTPS. IAM-authenticated ingress needs an extra layer (an ALB does not give SigV4; API Gateway or a Lambda proxy would). Fargate needs a VPC with a public IP or NAT. App Runner is not available to new customers (since 2026-04-30).
5. **API Gateway HTTP API (IAM auth) in front of Lambda** instead of a Function URL. It costs $1/M requests after the 12-month free tier, and adds throttling and a custom domain. A truly private API means a REST API plus an interface endpoint (≥ $7.30/month per AZ) plus a VPC.
6. **Spend guardrails available in any option:** free Budgets notifications (8–12 h lag), up to two free action-enabled budgets (IAM/SCP/EC2/RDS only), reserved concurrency = 0 as a kill switch, log retention, and S3 lifecycle. A paid account has no hard cap.
