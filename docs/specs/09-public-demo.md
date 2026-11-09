# Public demo and evaluation (M7)

Decided in the M7 design session (2026-11-07); each owner decision is logged in `docs/implementation/m7-decisions.md` (Q53–Q67). The hosted site is ADR 0029 and the simulated workload ADR 0030. Facts about AWS's own tools are in `docs/research/m7-aws-finops-tools.md`.

## Scope
Stand data-finops up for real in the owner's account, keep a believable workload running, and publish a dashboard anyone can reach from the repository or the docs site, loading recent data. Then evaluate it against AWS's own cost tools for the question data-finops exists to answer, and publish the comparison. Out of scope: a hosted dashboard for customers, authentication, MWAA, real dbt in Lambda.

## Before deploying
The Deployment must not ship unvetted code. These go first:
- **#209:** the Lambda package installs exactly `uv.lock`'s versions.
- **#210:** the CLI version test reads the SDK pin.
- **#208:** CI fails on a stale lockfile.

## Layout
- `src/data_finops/public_demo/`: the simulated orchestrator and the dataset generator (ADR 0030), and the publisher (ADR 0029). Not to be confused with the `data-finops demo` command, which loads the fixtures locally. Entry points are `data_finops.lambda_handlers.simulator` and `data_finops.lambda_handlers.publisher`, in the same package zip; their AWS clients come from `aws_clients`, the only module that imports boto3.
- `infra/pulumi/aws/deployment/demo.py`: the `PublicDemo` component, created only when `publicDemo` is `true`.
- `src/data_finops/api/dashboard/`: the static mode (below).

## The simulated orchestrator (ADR 0030)
- **Schedule:** EventBridge Scheduler, a few times a day, each start between :10 and :40 past the hour so that no query finishes near an hour boundary (OQ-25).
- **DAGs:** `daily_orders` (once a day), `hourly_events` (every six hours) and `weekly_finance` (Mondays). Each has two to four tasks, and each dbt task runs two to five models from the demo's dbt project.
- **Queries:**
  - each model runs one to three real statements in the `data-finops-demo` workgroup, against the demo dataset;
  - the first line of each is the dbt-athena query comment (`-- /* {"app": "dbt", "node_id": …, "invocation_id": …} */`), with the run's own invocation ID;
  - the workgroup has a per-query scan cutoff, as the reference workload's did.
- **Variety:** models scan from under 10 MB to a few hundred MB. About 5% of tasks fail once and succeed on retry, and an occasional run fails outright.
- **Records:**
  - each run writes its OpenLineage events (Airflow DAG run, task runs with a ParentRunFacet, dbt-ol model runs) to `inbox/openlineage/`;
  - each dbt invocation writes its `manifest.json` and `run_results.json` to `inbox/dbt/<invocation>/`;
  - events and artifacts are templated from `fixtures/reference-workload/`, and must pass the existing readers unchanged.
- **IAM, a role of its own:**
  - `athena:StartQueryExecution` and `athena:GetQueryExecution` on the demo workgroup only;
  - `glue:Get*` on the demo database and its tables;
  - `s3:GetObject` on the dataset and `s3:PutObject` on the results prefix;
  - the producer grant on `inbox/*`;
  - its log group.

  The collector's explicit deny on running queries is unchanged.

## The demo dataset
- **Contents:** generated once, by the dataset generator run as a one-off invocation: a few hundred MB of orders, customers and events as Parquet in the demo bucket.
- **Tables:** declared as Glue tables by the stack.
- **Determinism:** seeded, so a rebuild is the same data.

## The publisher (ADR 0029)
- **Trigger:** S3 sends an event when the collector writes the database object. The publisher downloads the object (as the API handler does) and runs the `reporting` read model in-process.
- **Files:** for the current and previous Billing Periods, it writes:
  - `costs/summary.json`;
  - the Allocation pages;
  - the Roll-ups by kind;
  - the Workload Run listing and each root run's lineage;
  - each published Allocation's minimal Cost Explanation;

  plus `snapshot.json`: when it was published, the Calculation Run, the period covered, and the explained share.
- **What is public:**
  - only Athena usage lines whose ResourceId is the demo workgroup, and what derives from them;
  - every 12-digit account ID becomes `123456789012`;
  - credit lines are left out, and the page says amounts are before credits.
- **Atomic swap:** files are written under a new prefix, then `current.json` is switched to it, so readers never see half a snapshot. Old prefixes expire after a few days.
- **IAM, a role of its own:** `s3:GetObject` on the database object; `s3:PutObject` and `s3:ListBucket` on the demo bucket; its log group.

## The site
- **Hosting:** a private demo bucket (all four public access blocks, encrypted) behind a CloudFront distribution with origin access control, HTTPS only, on CloudFront's own domain, with caching suited to a daily snapshot.
- **The dashboard:** the M5 dashboard, served with a `static` data source. Each API call maps to a file in the current snapshot, and a call the snapshot doesn't cover shows "not in the public demo".
- **The banner (Q67):** "Simulated workload; real Athena charges, reconciled to the bill; data trails by about a day", with the snapshot's time and Calculation Run.
- **Links:** the README and the docs site (home page, navigation) link the public URL. The stack outputs it as `demoUrl`.

## Stack, settings and policy pack
- **New settings:** `publicDemo` (default `false`); `demoScanCutoffBytes`; `demoSchedule`. Each takes a docstring, so the generated settings reference picks it up.
- **New outputs:** `demoUrl`, `demoBucket`.
- **Policy pack:**
  - the demo bucket gets the same S3 policies as the Deployment's bucket;
  - a new policy: every CloudFront distribution uses origin access control and redirects HTTP to HTTPS;
  - policy 4's allowlists gain the simulator and publisher policies, and the simulator alone may start queries, only on the demo workgroup.
- **The deployer draft policy:** gains CloudFront, Glue, EventBridge and the new roles.

## Cost limits
About $3 a month or less (Q66): Athena about $1–2, with CloudFront, S3, Lambda, EventBridge and Glue inside their free tiers at demo traffic. The $10 Budget alert stays. The kill switches:
- set `publicDemo` to `false` and run `pulumi up`;
- or disable the simulator's schedule.

## Evaluation (Q55–Q57)
- **The window:** once the demo has run for 7 days, answer each of Q57's questions with data-finops (the public dashboard and the API) and with each of Q56's tools:
  - Cost Explorer;
  - the Athena console's query history;
  - the FOCUS export queried with Athena SQL;
  - Cloud Intelligence Dashboards, from their documentation.
- **The record:** what each can answer, at what granularity, with what reconciliation, and what it costs. Recorded in progress.md.
- **Balance:** the comparison states where AWS is better: breadth across services, commitments, forecasting, anomaly detection, zero build.

## Documentation (spec 07)
- **Explanation pages:**
  - "Compared with AWS's cost tools", with the evaluation's table and its honest limits;
  - the demo's own page: what is simulated and what is real.
- **Reference:** the generated settings page gains the new settings and outputs.
- **How-to:** deploy-to-aws gains the `publicDemo` step.
- **Links:** the README, and the docs site's home page and navigation, link the public demo.

## Testing
Each seam test-first, with hand-made mutation checks recorded in the PR. No test touches a real account.
1. **The simulated orchestrator:**
   - run with botocore's Stubber for Athena and S3, and a fixed seed;
   - every event and artifact it writes passes the existing readers;
   - every query's first line is a Trusted Query Comment;
   - ingesting its output into a temporary database links each Query Execution to its Model Execution and on to the DAG run.
2. **The dataset generator:** deterministic for a seed, with its size and schema as declared.
3. **The publisher:**
   - on a temporary database with a demo line and an unrelated line, it writes only the demo's files;
   - no 12-digit number other than the example;
   - each file validates against the OpenAPI response schema of the route it stands for;
   - the swap is atomic.
4. **The dashboard's static mode:** every route the dashboard calls resolves to a file, or to the "not in the public demo" state.
5. **The stack:** under mocks, the `PublicDemo` component only when enabled; the policy pack's new and extended policies, with non-compliant fixtures through `pulumi policy analyze`.

## Owner-only steps
- **Deploy:** the deploy wizard gains the `publicDemo` stage. It sets the setting, runs `pulumi up`, triggers the dataset generator once and the simulator once, then the collector the next day, and opens `demoUrl`.
- **Evaluation:** the owner reviews the evaluation before it is published.

## Exit
- `demoUrl` loads the dashboard with a snapshot less than 48 hours old, reached by clicking from the README and from the docs site.
- The demo's lines reconcile, and their Coverage is recorded.
- The 7-day evaluation is recorded in progress.md, and the comparison page is published.
- A month's real cost of the demo is recorded against the $3 target.
