# M6 collector IAM: AWS Data Exports (FOCUS 1.2) and Athena query history

Status: research note, 2026-11-06. Nothing here was checked against a live AWS account. No AWS API was called and no credentials were used. Every action, resource type and condition key below comes from the AWS sources cited. The policy in section 7 is a **draft that has not been validated** (spec 05: "Document AWS API actions after validation").

Scope: the M6 collector's runtime identity. It reads (a) the Data Exports FOCUS 1.2 delivery from S3, as `src/data_finops/billing/aws_data_exports.py` and ADR 0015 / ADR 0020 describe, and (b) Athena query history in the BatchGetQueryExecution shape that spec 04 already accepts.

## Sources (all checked 2026-11-06)

| Id | Source | Notes |
|---|---|---|
| SRI | AWS Service Reference Information, machine-readable SAR: `https://servicereference.us-east-1.amazonaws.com/v1/{athena,bcm-data-exports,s3,kms}/*.json` (index at `https://servicereference.us-east-1.amazonaws.com/`) | Official AWS JSON form of the Service Authorization Reference. It lists actions, resource types, ARN formats, condition keys and an IsWrite/IsList annotation. **It has no access-level column and no dependent actions.** |
| SAR | `https://docs.aws.amazon.com/service-authorization/latest/reference/list_{amazonathena,awsbillingandcostmanagementdataexports,amazons3,awskeymanagementservice}.html` | **Not retrievable today.** The fetch returned only a redirect stub. Access levels and dependent actions are therefore **unverified** and must be read from these HTML pages before the policy is final. |
| BC-ATH | botocore `develop`, `botocore/data/athena/2017-05-18/{service-2,paginators-1}.json` (raw.githubusercontent.com; latest tag 1.43.125) | API shapes, limits, field docs. |
| BC-BCM | botocore `develop`, `botocore/data/bcm-data-exports/2023-11-26/{service-2,paginators-1}.json` | Same. |
| DX-DEL | https://docs.aws.amazon.com/cur/latest/userguide/dataexports-export-delivery.html | Delivery layout, refresh, overwrite and create-new modes. |
| DX-S3 | https://docs.aws.amazon.com/cur/latest/userguide/dataexports-s3-bucket.html | Destination bucket policy, cross-account owner. |
| DX-DP | https://docs.aws.amazon.com/cur/latest/userguide/data-protection.html | Encryption at rest. |
| DX-Q | https://docs.aws.amazon.com/cur/latest/userguide/dataexports-quotas.html | Export quotas. |
| GR-BILL | https://docs.aws.amazon.com/general/latest/gr/billing.html | Data Exports endpoint. |
| ATH-API | https://docs.aws.amazon.com/athena/latest/APIReference/API_ListQueryExecutions.html | ListQueryExecutions. |
| ATH-HIST | https://docs.aws.amazon.com/athena/latest/ug/queries-viewing-history.html | 45-day history. |
| ATH-WG | https://docs.aws.amazon.com/athena/latest/ug/example-policies-workgroup.html | Workgroup policy examples. |
| ATH-Q | https://docs.aws.amazon.com/general/latest/gr/athena.html and https://docs.aws.amazon.com/athena/latest/ug/service-limits.html | API rate quotas. |
| ATH-PR | https://aws.amazon.com/athena/pricing/ | Pricing. |
| S3-KEYS | https://docs.aws.amazon.com/AmazonS3/latest/userguide/amazon-s3-policy-keys.html | `s3:prefix` examples. |
| S3-XACCT | https://docs.aws.amazon.com/AmazonS3/latest/userguide/example-walkthroughs-managing-access-example2.html | Cross-account bucket access. |
| S3-KMS | https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingKMSEncryption.html | SSE-KMS permissions, cross-account use, encryption context. |
| S3-PR | https://aws.amazon.com/s3/pricing/ | Request pricing. |
| LAMBDA | https://docs.aws.amazon.com/lambda/latest/dg/lambda-intro-execution-role.html | Execution role. |
| IAM-BP | https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html | IAM best practices. |
| REPO | ADR 0020 amendment, `src/data_finops/billing/aws_data_exports.py` | Observed real FOCUS 1.2 deliveries (2026-10, 2026-11). |

## 1. Data Exports: layout, delivery modes, discovery, encryption, API

### S3 layout (DX-DEL)
- Data: `s3://<bucket>/<prefix>/<export-name>/data/<partition>/`. In create-new mode it is `.../data/<partition>/<timestamp>-<execution-id>/<export-name>-<chunk-number>.snappy.parquet`. Chunk numbers have five digits and start at `00001`.
- Metadata: `s3://<bucket>/<prefix>/<export-name>/metadata/<partition>/<export-name>-Manifest.json`. The summary section of the same page calls it `.../metadata/<partition>/Manifest.json`, so the page names the file both ways.
- The docs spell the partition `BILLING_PERIOD=YYYY-MM`, and they describe it only for CUR 2.0. **The real FOCUS 1.2 deliveries use lowercase `billing_period=YYYY-MM` and name the files `<export>-Manifest.json` and `<export>-Manifest-FOCUS.json`** (REPO, ADR 0020 amendment). The docs do not mention the `-Manifest-FOCUS.json` file at all. The collector should match what is observed, not what the docs spell, and should not depend on the case of the partition key.
- Manifest contents per DX-DEL: the columns, the list of data files with their paths ("We recommend identifying which files to ingest by programmatically reading this list"), the time period covered, and `additionalOutputFiles`. "The `Manifest.json` is only delivered once all of the export data files have been delivered to S3." The observed manifest fields are `executionId`, `exportArn`, `columns`, `dataFiles` (S3 URIs) and `additionalOutputFiles`, and none of them is a publication time (REPO).

### Overwrite vs create new (DX-DEL; enum in BC-BCM)
- `S3OutputConfigurations.Overwrite` takes `CREATE_NEW_REPORT` or `OVERWRITE_REPORT` (BC-BCM).
- **Create new:** every refresh is kept under `<timestamp>-<execution-id>/`. Two manifests are written per execution. One goes to `metadata/<partition>/<timestamp>-<execution-id>/` (that execution only). The other overwrites the one in `metadata/<partition>/`, which "always represents the most recent refresh".
- **Overwrite:** files sit directly under `data/<partition>/` and are overwritten on each delivery of that partition. "Data Exports overwrites any extra chunks from the last refresh with empty data". So listing `data/` can show stale empty chunks, and the manifest's `dataFiles` is the only reliable list. A single manifest lives in `metadata/<partition>/` and is overwritten.
- **Consequence for this product:** ADR 0020's `published_at` comes from the `<timestamp>-<execution-id>` folder name, which only create-new mode writes. In overwrite mode that timestamp is not in any key name (per DX-DEL), and neither manifest carries a time (REPO). **M6 should require `CREATE_NEW_REPORT`**, or else define a different `published_at` source, which ADR 0020 does not allow today.
- Refresh (DX-DEL): "each time the source data is updated. For CUR 2.0, this occurs at least once a day." After a period closes, "AWS may update the export delivery for the previous billing period within the first two weeks after it ended." BC-BCM: `RefreshCadence.Frequency` has the single value `SYNCHRONOUS`, documented as "refreshes each time the source data updates, up to three times daily." The docs state the cadence only for CUR 2.0, so the FOCUS cadence is **unverified**.

### Discovering a new execution
Two documented options:
1. **S3 only, no billing API.** List `metadata/billing_period=YYYY-MM/` for the current and previous period and treat each `<timestamp>-<execution-id>/` folder that contains a manifest as a complete Delivery, because the manifest is written last (DX-DEL). This needs only `s3:ListBucket` (with a prefix) and `s3:GetObject`.
2. **Data Exports API** (BC-BCM, SRI):
   - `ListExecutions(ExportArn, MaxResults 1..300, NextToken)` returns `Executions[]` of `{ExecutionId, ExecutionStatus{StatusCode, StatusReason, CreatedAt, CompletedAt, LastUpdatedAt}}`. It is paginated (`NextToken`/`Executions`).
   - `GetExecution(ExportArn, ExecutionId)` returns `{ExecutionId, Export (snapshot of the definition at execution time), ExecutionStatus}`.
   - `StatusCode` values: `INITIATION_IN_PROCESS`, `QUERY_QUEUED`, `QUERY_IN_PROCESS`, `QUERY_FAILURE`, `DELIVERY_IN_PROCESS`, `DELIVERY_SUCCESS`, `DELIVERY_FAILURE`. `StatusReason` values: `INSUFFICIENT_PERMISSION`, `BILL_OWNER_CHANGED`, `INTERNAL_FAILURE`, `DEPRECATED`.
   - IAM actions `bcm-data-exports:ListExecutions` and `bcm-data-exports:GetExecution` take resource type `export`, ARN `arn:${Partition}:bcm-data-exports:${Region}:${Account}:export/${Identifier}`, with condition key `aws:ResourceTag/${TagKey}`. `ListExports` has no resource type (SRI). The access level is **unverified** (SAR not retrievable). SRI marks `ListExecutions` and `ListExports` as list actions and none of the four as write actions.
   - Endpoint: only `bcm-data-exports.us-east-1.api.aws` (GR-BILL).
   - The repo found that the folder `<CreatedAt>` equals the `CreatedAt` that ListExecutions reports (REPO). ADR 0020 keeps import offline on purpose, so the API is optional. It is useful only to tell that an execution has failed (`DELIVERY_FAILURE`), which S3 alone cannot show.
   - **Inference, unverified:** an export belongs to the account that created it, which is normally the management or payer account for an organisation-wide export. Calling ListExecutions therefore needs credentials in that account, for example by assuming a role there. No resource-based policy on exports is documented.

### Encryption
- DX-DP: "By default, your data exports are encrypted using server-side encryption with Amazon S3 managed keys (SSE-S3). If you want to use ... (SSE-KMS) to encrypt your exports, you need to trigger encryption with KMS after the export has been delivered." It links to S3 default bucket encryption.
- DX-S3's required bucket policy grants `bcm-data-exports.amazonaws.com` only `s3:PutObject`, and no KMS key-policy statement for that principal is documented. **Whether a bucket whose default encryption is SSE-KMS with a customer managed key accepts Data Exports deliveries is unverified.** The collector policy should still include an optional `kms:Decrypt` statement for buckets where the operator re-encrypts objects or sets SSE-KMS.
- Quotas (DX-Q): at most 2 exports of the FOCUS 1.2 table (`FOCUS_1_2_AWS`) per account.

## 2. Minimal S3 permissions for one prefix
- `s3:ListBucket` applies to resource type `bucket` (`arn:${Partition}:s3:::${BucketName}`). Its action condition keys include `s3:prefix`, `s3:delimiter`, `s3:max-keys` and `s3:ResourceAccount` (SRI). S3-KEYS shows the documented pattern: allow `s3:ListBucket` with `StringEquals`/`StringLike` on `s3:prefix`, optionally with an explicit `Deny` when `StringNotEquals`/`StringNotLike`. For a versioned bucket, listing versions needs `s3:ListBucketVersions` instead, which also supports `s3:prefix` (S3-KEYS). The collector lists current objects, so it does not need `ListBucketVersions`.
- `s3:GetObject` applies to resource type `object` (`arn:${Partition}:s3:::${BucketName}/${ObjectName}`). Its condition keys include `s3:ResourceAccount` and `s3:ExistingObjectTag/<key>`, but not `s3:prefix` (SRI). The prefix is enforced by the resource ARN `.../<prefix>/<export-name>/*`.
- `s3:GetObjectVersion` is needed only when a request names a `versionId` (SRI, S3-KEYS example 3). The collector does not need it.
- `s3:GetBucketLocation` (resource type `bucket`) is not needed if the region is configured.
- SSE-KMS: "To download an object encrypted with an AWS KMS key, you need `kms:Decrypt` permissions for the key" (S3-KMS). `kms:Decrypt` applies to resource type `key` (`arn:${Partition}:kms:${Region}:${Account}:key/${KeyId}`), and its condition keys include `kms:ViaService`, `kms:CallerAccount` and `kms:EncryptionContext:${EncryptionContextKey}` (SRI). S3 uses encryption context `aws:s3:arn` = object ARN, or the bucket ARN when an S3 Bucket Key is enabled (S3-KMS). An SSE-S3 object needs no KMS permission. A `kms:ViaService` value of `s3.<region>.amazonaws.com` is the usual pattern, but this research did not confirm it against a KMS page, so it is **unverified**.
- Cross-account, where the bucket is in the payer or management account and the collector is elsewhere:
  - The requester needs permission from both the resource owner (bucket policy) and its own account (identity policy) (S3-XACCT note).
  - "Objects encrypted using SSE-KMS with AWS managed keys can't be shared cross-account", so a customer managed key is required and its key policy must allow the other account (S3-KMS).
  - With ACLs enabled, Data Exports applies `BucketOwnerFullControl`, and AWS recommends disabling ACLs (DX-S3).
  - Data Exports can deliver to a bucket owned by another account (`S3BucketOwner`, `S3Region` in BC-BCM; DX-S3), so the bucket may not be in the payer account at all.
  - Option A: cross-account grant through the bucket policy, the identity policy, and the KMS key policy if a CMK is used.
  - Option B: the collector assumes a read-only role in the bucket-owning account. This needs `sts:AssumeRole` and is not covered by this research.

## 3. Athena query history
| Action | SRI resource / condition | API facts (BC-ATH, ATH-API) |
|---|---|---|
| `athena:ListQueryExecutions` | `workgroup`, `aws:ResourceTag/${TagKey}` | Input `WorkGroup` (pattern `[a-zA-Z0-9._-]{1,128}`; defaults to `primary`), `MaxResults` 0..50, `NextToken` (1..1024). Output `QueryExecutionIds` (≤50) and `NextToken`. Has a boto3 paginator. **No time filter. The order of IDs is not documented (unverified).** |
| `athena:BatchGetQueryExecution` | `workgroup` | `QueryExecutionIds` 1..50 (required). Returns `QueryExecutions[]` and `UnprocessedQueryExecutionIds[]`. |
| `athena:GetQueryExecution` | `workgroup` | A single ID (1..128). |
| `athena:GetWorkGroup` | `workgroup` | Optional. |
| `athena:ListWorkGroups` | no resource type, so `Resource: "*"` | Optional. ATH-WG's example uses `"Resource": "*"`. |

- Workgroup ARN: `arn:${Partition}:athena:${Region}:${Account}:workgroup/${WorkGroupName}` (SRI, ATH-WG). A query belongs to its workgroup: "Requires you to have access to the workgroup in which the queries ran" (BC-ATH).
- Retention: "Athena keeps a query history for 45 days" (ATH-API, ATH-HIST). The collector must poll more often than every 45 days and should treat any gap longer than that as unrecoverable.
- **Query text is returned.** `QueryExecution.Query` is "The SQL query statements which the query execution ran" (BC-ATH). The response also carries `ResultConfiguration` (the S3 output location), `ExecutionParameters`, `Status.StateChangeReason` and `AthenaError`. No documented parameter suppresses `Query`, so the collector receives SQL and must drop it before storage, as the #61 allowlist already does for files (spec 04).
- **None of these read actions runs a query.** Among Athena actions, SRI marks `StartQueryExecution`, `StopQueryExecution`, `CancelQueryExecution` and `RunQuery` as write actions, together with the named-query, prepared-statement, workgroup, session and notebook actions. The five actions above are not write actions. `GetQueryResults` and `GetQueryRuntimeStatistics` are reads but return result rows or query plans, so they must not be granted.
- Throttling (ATH-Q, per account per region, adjustable):

  | API | Replenishment rate | Burst multiplier |
  |---|---|---|
  | ListQueryExecutions | 5/s | 2 |
  | BatchGetQueryExecution | 10/s | 2 |
  | GetQueryExecution | 500/s in large regions, 100/s elsewhere | 1 or 2 |

  Exceeding a quota raises `ThrottlingException: Rate exceeded`. These quotas are shared with every other caller in the account, so use backoff and BatchGet (50 per call) rather than GetQueryExecution per ID.

## 4. Do these reads need Glue or results-bucket S3 permissions?
- The SRI JSON has no dependent-actions field, and the SAR HTML that lists them could not be retrieved. **Whether any dependent actions exist is unverified.**
- ListQueryExecutions, BatchGetQueryExecution and GetQueryExecution return metadata held by Athena: the API shapes contain no result rows, only the `OutputLocation` string (BC-ATH). ATH-WG's examples grant them with no `s3:` or `glue:` statement. Results-bucket S3 access is a known need of `GetQueryResults` and `StartQueryExecution`, neither of which the collector calls.
- **Inference, unverified:** none of the collector's Athena reads needs `glue:*` or `s3:*`. Validate this by calling each action with only the draft policy attached.
- `bcm-data-exports:ListExecutions` and `GetExecution` read no S3 objects (inference).

## 5. Runtime identity vs deployer identity
- A Lambda function runs as its execution role. "Lambda automatically assumes your execution role when you invoke your function", and the trust policy must name `lambda.amazonaws.com` (LAMBDA). Workloads should use "temporary credentials with IAM roles" (IAM-BP).
- Apply least privilege, use IAM Access Analyzer to generate a policy from CloudTrail activity and to validate it, and use conditions (IAM-BP, LAMBDA). AWS does not say "separate the deployer from the runtime" in those words. It does recommend multi-account separation and SCP/RCP guardrails (IAM-BP). Spec 05's split follows from least privilege: the runtime role gets only the reads in section 7, and the deployer is the human or CI principal that creates the stack.
- Deployer, out of scope: it needs `iam:CreateRole`, `iam:PassRole` for the collector role, and Lambda, EventBridge and CloudWatch Logs write permissions. The collector role itself also needs CloudWatch Logs write (`AWSLambdaBasicExecutionRole`, LAMBDA). None of this is detailed here.

## 6. Cost
- **Athena API calls:** ATH-PR prices only data scanned (example `$5/TB`), federated queries and the related S3 and Glue charges. It lists no charge for ListQueryExecutions, BatchGetQueryExecution or GetQueryExecution. "Free" is inferred from that absence and is **unverified**. Collector reads scan no data.
- **S3:** "S3 GET requests from the S3 Standard storage class cost $0.0004 per 1,000 requests" (S3-PR, us-east-1, quoted in an example on that page). "LIST requests for any storage class are charged at the same rate as S3 Standard PUT, COPY, and POST requests", but the PUT rate was not shown in the fetched page (**unverified**). Transfer from S3 to AWS services in the same region is not charged (S3-PR). A few LISTs and a handful of GETs per poll cost very little.
- **KMS:** SSE-KMS has "additional charges", and S3 Bucket Keys reduce KMS request cost by up to 99% (S3-KMS). The rates were not fetched.
- **Data Exports:** "Storing the exports data in any S3 bucket is billed at standard Amazon S3 rates" (DX-S3). Create-new mode keeps every refresh and uses more storage (DX-DEL). No first-party page stating the Data Exports price was found (the pricing URL returned 404), so whether the export itself is free is **unverified**.
- **Data Exports API calls:** no pricing found (**unverified**).

## 7. Draft least-privilege policy (DRAFT, UNVALIDATED)

> **DRAFT, UNVALIDATED. Do not deploy as-is.** Placeholders: account `123456789012`, bucket `example-export-bucket`, prefix `focus/`, export name `example-export`, workgroup `example-workgroup`, region `us-east-1`, key and export IDs `EXAMPLE-...`. Every action, resource type and condition key is from SRI. Before use: check access levels and dependent actions in the SAR HTML, run IAM Access Analyzer policy validation, and run each collector call against a real account (spec 05).

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListExportPrefixOnly",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::example-export-bucket",
      "Condition": {
        "StringLike": { "s3:prefix": ["focus/example-export/", "focus/example-export/*"] },
        "StringEquals": { "s3:ResourceAccount": "123456789012" }
      }
    },
    {
      "Sid": "ReadExportObjectsOnly",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::example-export-bucket/focus/example-export/*",
      "Condition": {
        "StringEquals": { "s3:ResourceAccount": "123456789012" }
      }
    },
    {
      "Sid": "DecryptExportObjectsIfSseKms",
      "Effect": "Allow",
      "Action": "kms:Decrypt",
      "Resource": "arn:aws:kms:us-east-1:123456789012:key/EXAMPLE-KEY-ID",
      "Condition": {
        "StringEquals": { "kms:ViaService": "s3.us-east-1.amazonaws.com" }
      }
    },
    {
      "Sid": "OptionalExportExecutionStatus",
      "Effect": "Allow",
      "Action": [
        "bcm-data-exports:ListExecutions",
        "bcm-data-exports:GetExecution"
      ],
      "Resource": "arn:aws:bcm-data-exports:us-east-1:123456789012:export/EXAMPLE-EXPORT-ID"
    },
    {
      "Sid": "AthenaQueryHistoryReadOnly",
      "Effect": "Allow",
      "Action": [
        "athena:ListQueryExecutions",
        "athena:BatchGetQueryExecution",
        "athena:GetQueryExecution",
        "athena:GetWorkGroup"
      ],
      "Resource": "arn:aws:athena:us-east-1:123456789012:workgroup/example-workgroup"
    },
    {
      "Sid": "OptionalListWorkGroups",
      "Effect": "Allow",
      "Action": "athena:ListWorkGroups",
      "Resource": "*"
    },
    {
      "Sid": "NeverRunOrReadQueryResults",
      "Effect": "Deny",
      "Action": [
        "athena:StartQueryExecution",
        "athena:StopQueryExecution",
        "athena:CancelQueryExecution",
        "athena:RunQuery",
        "athena:GetQueryResults",
        "athena:GetQueryResultsStream",
        "athena:GetQueryRuntimeStatistics"
      ],
      "Resource": "*"
    }
  ]
}
```

Notes on the draft:
- Drop `DecryptExportObjectsIfSseKms` when the bucket uses SSE-S3, which is the default (DX-DP). If an S3 Bucket Key is enabled, an encryption-context condition would use the bucket ARN, not the object ARN (S3-KMS). The `kms:ViaService` value is unverified.
- Drop `OptionalExportExecutionStatus` if discovery stays S3-only (section 1). The export identifier format is a placeholder, because the SRI gives only `export/${Identifier}`. In a cross-account setup this statement must sit on a role in the export-owning account (section 1, inference).
- Repeat the workgroup ARN for each workgroup the collector reads, or scope by `aws:ResourceTag/${TagKey}` (SRI).
- The explicit `Deny` is defence in depth against a later broad grant. Each denied action appears in SRI (`RunQuery`, `CancelQueryExecution` and `GetQueryResultsStream` included).
- Cross-account: the same S3 and KMS grants must also appear in the bucket policy and the key policy of the owning account (S3-XACCT, S3-KMS).

## Open items to validate before M6 closes
1. Read the SAR HTML for access levels and dependent actions of every action in section 7.
2. Confirm on the real export that `CREATE_NEW_REPORT` is set, and decide whether M6 rejects overwrite-mode exports.
3. Check whether SSE-KMS default bucket encryption works with Data Exports delivery.
4. Find out whether ListQueryExecutions returns IDs newest first. This decides how the poller stops paging.
5. Confirm that the Athena reads work with no Glue or S3 permissions.
6. Get current S3 LIST and KMS request prices and any Data Exports price from first-party pricing pages.
