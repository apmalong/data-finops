# M6 decisions (owner, 2026-11-06 to 2026-11-07)

Code, specs, PRs and the register cite these as "Q<n>". Each was asked with a recommended answer, and the owner accepted that answer. Where a decision changed a design record, the record is named. ADRs 0026–0028 and spec 08 remain authoritative; this log records only who decided what.

## Design session (PR #176)

| # | Decision | Recorded in |
|---|---|---|
| Q1 | Reusable Pulumi Python components in `infra/pulumi/aws/`, their own uv project, local `file://` state | spec 08 §Layout |
| Q2 | One account holds the export bucket, the workgroups and the Deployment; cross-account reads are a revisit trigger | ADR 0026 |
| Q3 | AWS collectors in `providers.aws` behind an optional `aws` extra; tested with botocore's Stubber | spec 08 §Layout |
| Q4 | A Collection Run ingests every source since its Collection Watermark, then starts one Calculation Run | spec 08 §Collection Run |
| Q5 | Workgroups are listed explicitly; an empty list is refused | spec 08 §Settings |
| Q6 | The package is built from the checkout; no PyPI release, no fixtures in the wheel | spec 08 §Topology |
| Q7 | A Deployment's Cost Explanation is minimal by default, full by opt-in (#110) | ADR 0028 |
| Q8 | #91 stays an M4 follow-up | plan.md |
| Q9 | #26 joins M6 as its last owner-only step | plan.md |
| Q10 | M6 closes on offline checks; the owner's real deployment is optional | spec 08 §Exit |
| Q11 | A minimal Billing Line is the declared `FocusBillingLine` columns minus `Tags` | ADR 0028 |
| Q12 | Data Exports must create new files; an overwrite layout is refused | spec 08 §Collection Run |
| Q13 | Deliveries are found from S3 `metadata/` alone, in the observed layout | spec 08 §Collection Run |
| Q14 | `billingLookbackPeriods` defaults to 2 | spec 08 §Settings |
| Q15 | The Athena watermark is the stored query IDs; a page cap with a warning | spec 08 §Collection Run |
| Q16 | A partly rejected run still calculates; only exit code 2 fails it | spec 08 §Collection Run |
| Q17 | Two Lambda functions without a VPC; the collector the only writer, by S3 compare-and-swap; a read-only API behind `AWS_IAM` | ADR 0026 |
| Q18 | Ingress by scheduled pull from S3 Inboxes | ADR 0027 |
| Q19 | One bucket: private, owner-enforced, TLS-only, encrypted, versioned, expiring, kept on destroy unless opted in | spec 08 §Topology |
| Q20 | Cost limits are bounded resources, an optional Budget alert and a kill switch | spec 08 §Cost limits |
| Q21 | Six mandatory policies, checking literal inputs | spec 08 §Policy pack |
| Q22 | The pack is validated by unit tests and by `pulumi policy analyze` on mock-generated state, in a new `infra` CI job | spec 08 §Testing |
| Q23 | Own uv projects for the stack and the pack; Pulumi SDK and CLI at one release, the newest at least two weeks old; pulumi-aws 7.52.1, as the reference workload; pulumi-policy 1.21.0 | spec 08 §Layout; register |
| Q24 | One arm64 zip from the checkout, uploaded as an S3 object | spec 08 §Topology |
| Q25 | The deployed API serves JSON only; the dashboard runs locally on a copy | spec 08 §Reading a Deployment |
| Q26 | The documentation pages M6 adds | spec 07, spec 08 §Documentation |
| Q27 | Glossary terms: Deployment, Collection Run, Inbox, Collection Watermark | GLOSSARY.md |
| Q28 | One summary log line per run; an optional errors alarm; no custom metrics | spec 08 §Collection Run |
| Q29 | The owner creates the deployer identity; draft policies stay unvalidated until the wizard checks them | spec 08 §IAM |
| Q30 | The stack reads an existing FOCUS export | spec 08 §Settings |
| Q31 | A run attempts every source, calculates, then reports failure | spec 08 §Collection Run |
| Q32 | `data-finops collect` runs a Collection Run from a laptop | spec 08 §Collection Run |
| Q33 | The eight test seams | spec 08 §Testing |

## Tickets and implementation

| # | Decision | Recorded in |
|---|---|---|
| Q34–Q37 | The ticket breakdown, documentation per ticket, the owner wizards as code, and the PR grouping | plan.md |
| Q38 | boto3 and botocore 1.43.118 in the `aws` extra | register (#179) |
| Q39 | `providers.aws` types its clients with its own Protocols | register (#179) |
| Q40 | The Inbox part of the Collection Watermark lives in the workload store, keyed by S3 key and ETag | spec 08 §Collection Run |
| Q41 | The `collect` settings file grows its keys ticket by ticket; unknown keys are refused | spec 08 §Collection Run |
| Q42 | `collect` prints the summary line and the rejections | spec 08 §Collection Run |
| Q43 | Lost Athena history is detected from missing overlap, not a stored clock | spec 08 §Collection Run (#181) |
| Q44 | botocore's standard retries, five attempts | register (#181) |
| Q45 | Fetch query IDs not yet stored; `account_id`, `workgroups` and `max_history_pages` settings | spec 08 §Collection Run (#181) |
| Q46 | Every Delivery newer than the current Export Snapshot is ingested, oldest first | spec 08 §Collection Run (#182) |
| Q47 | Data files only from the Delivery's own folder in the export | spec 08 §Collection Run (#182) |
| Q48 | Caps of 2 GiB per data file and 8 GiB per Delivery | spec 08 §Collection Run (#182) |
| Q49 | Mangum 0.22.0 adapts the API to Lambda | register (#184) |
| Q50 | The collector's settings come as one JSON variable with the `collect` file's keys | spec 08 §Topology (#183) |
| Q51 | The database object and its compare-and-swap | spec 08 §Topology (#183) |
| Q52 | The handlers' exit semantics, and the API's cached copy | spec 08 §Topology (#183, #184) |
