# Public demo and evaluation (M7)

Decided in the M7 design session (2026-11-07). Each owner decision is logged in `docs/implementation/m7-decisions.md` (Q53–Q72). The hosted site is ADR 0029, the simulated workload is ADR 0030, and Cost Dimensions is ADR 0031. Facts about the cost tools of AWS are in `docs/research/m7-aws-finops-tools.md`.

## Scope
Deploy data-finops for real in the account of the owner. Keep a believable workload in operation. Publish a dashboard with recent data, that anyone can reach from the repository or the docs site. Then evaluate it against the cost tools of AWS, for the question that data-finops answers, and publish the comparison. Out of scope: a hosted dashboard for customers, authentication, MWAA, and real dbt in Lambda.

## Before the deployment
The Deployment must not contain code that is not vetted. These tickets go first:
- **#209:** the Lambda package installs exactly the versions in `uv.lock`.
- **#210:** the CLI version test reads the SDK pin.
- **#208:** CI fails on a stale lockfile.

## Layout
- `src/data_finops/public_demo/`: the simulated orchestrator and the dataset generator (ADR 0030), and the publisher (ADR 0029).
  - This is not the `data-finops demo` command, which loads the fixtures locally.
  - The entry points are `data_finops.lambda_handlers.simulator`, `data_finops.lambda_handlers.publisher` and `data_finops.lambda_handlers.generator` (the dataset generator, Q78), in the same package zip.
  - Their AWS clients come from `aws_clients`, the only module that imports boto3.
- `infra/pulumi/aws/deployment/demo.py`: the `PublicDemo` component. The stack creates it only when `publicDemo` is `true`.
- `src/data_finops/api/dashboard/`: the static mode (below).

## Cost Dimensions (ADR 0031)
- **Configuration:** one TOML file, the same for `data-finops ingest`, `calculate` and `collect` (`--dimensions FILE`). There are 8 dimensions at most. An unknown source or key is refused, as in the settings of `collect`. For example:

  ```toml
  [dimensions.team]
  sources = ["query_comment.team", "openlineage_tag.team", "workgroup_map"]
  workgroup_map = { "etl-finance" = "finance" }

  [dimensions.tenant]
  sources = ["query_comment.tenant_id"]
  sensitive = true
  ```

- **Sources:** in the configured order. The first source with a value wins.
  - `query_comment.<key>`: a key in the first-line JSON comment of the query. It has the syntax of the Trusted Query Comment, but a dimension does not require `app`, `node_id` or `invocation_id`.
  - `openlineage_tag.<key>`: a tag in the OpenLineage tags facets of the Workload Run linked to the query. That run is the run of its dbt invocation, which the Evidence of the Allocation names (Q73). The tags of that run come first, then the tags of its parents up to the root. A key with two values on one run gives no value.
  - `workgroup_map`: the workgroup of the query, or the workgroup of the line for an amount with no query.
- **Capture:** ingest keeps only the comment keys that the configuration names, in a new `QueryExecution.claims`.
  - There are 16 keys at most, and each value is 128 characters at most.
  - A longer value is removed, and the ingest result counts it.
  - A key added later applies only to queries ingested after it.
- **Resolution:** a Calculation Run records `dimensions: {name: {value, source}}` on each Allocation.
  - Cost with no value is `unassigned`.
  - A new version, `cost-dimensions/1`, joins the policy version.
  - The SHA-256 of the configuration is recorded on the snapshot, but it is not part of the input digest. Thus a change to the configuration never changes a stored snapshot, and does not start a new snapshot by itself. It applies from the next snapshot that new inputs make (Q72).
- **Reads:** `GET /v1/rollups/dimensions/{name}` returns a page of values, with their amounts for each Cost Basis and how the source of each value splits.
  - The total of each dimension, which includes `unassigned`, is equal to the total of the snapshot.
  - The dashboard gets a dimension view, in both data sources.
- **A Deployment:** the setting `costDimensions` holds the path of the file.
  - The stack uploads the file to `config/cost-dimensions.toml` in the bucket of the Deployment. The reason is that the settings of the collector go in an environment variable, which is limited to 4 KB.
  - The roles of the collector and the publisher can read that one object.
  - The collector reads the object before it collects anything. If the object cannot be read or used, the run stops, and the error names the key, never the content.
  - `data-finops collect` takes a `--dimensions` file or a bucket key in its settings (`cost_dimensions_key`), not both.
- **Sensitive dimensions:** they are not in the minimal Cost Explanation, the public form of the dimension route or the Demo Snapshot. The public form of the route is part of the Demo Snapshot (Q74). The full API shows them.

## The simulated orchestrator (ADR 0030)
- **Schedule:** EventBridge Scheduler, a few times a day. Each start is between :10 and :40 past the hour, so that no query finishes near the boundary of an hour (OQ-25).
  - The setting `demoSchedule` is `cron(M H/6 * * ? *)`: a start every six hours, `cron(25 0/6 * * ? *)` by default. M is from 10 to 40. H, from 0 to 5, is the first start of the day, which runs `daily_orders`.
  - The schedule gives the simulator its scheduled time. The plan of a start comes from a seed and that time only, so a start that is invoked again plans the same runs.
  - The schedule and Lambda do not retry a failed start, because a retry runs the queries again.
  - The simulator stops a query that runs for more than two minutes. Then its model fails.
  - The `PublicDemo` component makes the function, its role and the schedule (#226, Q79).
- **DAGs:** `daily_orders` (one time each day), `hourly_events` (each six hours) and `weekly_finance` (Mondays). Each DAG has two to four tasks. Each dbt task runs two to five models from the dbt project of the demo.
- **Queries:**
  - Each model runs one to three real statements in the `data-finops-demo` workgroup, against the demo dataset.
  - The first line of each statement is the dbt-athena query comment (`-- /* {"app": "dbt", "node_id": …, "invocation_id": …, "team": …, "tenant_id": …, "dag_run_id": …} */`). It has the invocation ID of the run, and believable fictional teams and tenants.
  - The workgroup has a scan cutoff for each query, as the reference workload had.
- **Variety:** models scan from less than 10 MB to a few hundred MB. About 5% of tasks fail one time and succeed on retry. Sometimes a run fails completely.
  - A model fails as a real dbt model fails: Athena fails its query, which names a column that does not exist. Athena does not charge for the failed query. The models after it are skipped.
- **Records:**
  - Each run writes its OpenLineage events (Airflow DAG run, task runs with a ParentRunFacet, dbt-ol model runs) to `inbox/openlineage/`.
  - Each dbt invocation writes its `manifest.json` and `run_results.json` to `inbox/dbt/<invocation>/`.
  - The events and artifacts come from templates in `fixtures/reference-workload/`. They must pass the current readers, unchanged.
- **IAM, a role of its own:**
  - `athena:StartQueryExecution`, `athena:GetQueryExecution` and `athena:StopQueryExecution` on the demo workgroup only
  - `glue:GetDatabase`, `glue:GetTable` and `glue:GetPartitions` on the catalog, the demo database and its tables
  - `s3:ListBucket` and `s3:GetBucketLocation` on the demo bucket, which Athena also needs (Q80)
  - `s3:GetObject` on the dataset, and `s3:PutObject` and `s3:AbortMultipartUpload` on the results prefix
  - the producer grant on `inbox/*`, and `kms:GenerateDataKey` on the key of the bucket when `kmsKeyArn` is set
  - its log group

  The explicit deny of the collector on query execution does not change.
- **The collector:** when `publicDemo` is `true`, the stack adds `data-finops-demo` to the workgroups whose history the collector reads, and to its grant. The `workgroups` setting does not change (Q81).

## The demo dataset
- **Contents:** the dataset generator makes it one time, in a one-off invocation. It is a few hundred MB of orders, customers and events, as Parquet in the demo bucket, under `dataset/` (Q77).
- **The generator:** a Lambda function in the stack, which the output `demoGeneratorFunction` names (Q78). The owner invokes it one time. Its role can only write `dataset/*` in the demo bucket, and its log group.
  - It writes the Parquet with pyarrow (ADR 0018, amended by Q76).
  - It makes one part of a million rows at a time, so that the memory stays small.
- **Tables:** the stack declares them as Glue tables, in the Glue database `data_finops_demo`. The columns of the tables and of the Parquet files are the same, and a test compares them.
- **Determinism:** the generator has a seed, so a rebuild gives the same data.
- **The demo bucket:** the `PublicDemo` component makes it with the dataset (#225). It has the same guards as the bucket of the Deployment, with the S3-managed key. All its contents can be made again, so it is deleted with the demo.
  - Athena writes the results of the demo's queries under `athena-results/`. S3 deletes them after one day.

## The publisher (ADR 0029)
- **Trigger:** S3 sends an event when the collector writes the database object. The publisher downloads the object (as the API handler does), and runs the `reporting` read model in the same process.
- **Files:** for the current and the previous Billing Periods, it writes these files:
  - `costs/summary.json`
  - the Allocation pages
  - the Roll-ups by kind
  - the Workload Run list, and the lineage of each root run
  - the Roll-ups by each Cost Dimension that is not sensitive
  - the minimal Cost Explanation of each published Allocation

  It also writes `snapshot.json`: when it was published, the Calculation Run, the period covered and the explained share.
- **What is public:**
  - Only the Athena usage lines whose ResourceId is the demo workgroup are public, and what comes from them.
  - Each 12-digit account ID becomes `123456789012`.
  - Credit lines are not included, and the page says that amounts are before credits.
- **Atomic swap:** the files are written under a new prefix. Then `current.json` changes to that prefix. Thus readers never see half a snapshot. Old prefixes expire after a few days.
- **IAM, a role of its own:** `s3:GetObject` on the database object, `s3:PutObject` and `s3:ListBucket` on the demo bucket, and its log group.

## The site
- **Host:** a private demo bucket (all four public access blocks, encrypted) behind a CloudFront distribution with origin access control. It is HTTPS only, on the CloudFront domain (no custom domain), with a cache configuration for a daily snapshot.
  - CloudFront serves only the prefix of the site. It never serves `dataset/` or `athena-results/` (Q77).
- **The dashboard:** the M5 dashboard, served with a `static` data source. Each API call maps to a file in the current snapshot. A call that the snapshot does not cover shows "not in the public demo".
- **The banner (Q67):** "Simulated workload; real Athena charges, reconciled to the bill; data trails by about a day", with the time and the Calculation Run of the snapshot.
- **Links:** the README and the docs site (home page, navigation) link to the public URL. The stack outputs it as `demoUrl`.

## Stack, settings and policy pack
- **New settings:** `publicDemo` (default `false`), `demoScanCutoffBytes`, `demoSchedule` and `costDimensions`. Each has a docstring, so that the generated settings reference shows it.
- **New outputs:** `demoUrl`, `demoBucket`, `demoGeneratorFunction` and `demoSimulatorFunction`. They exist only when `publicDemo` is `true`.
- **Policy pack:**
  - The demo bucket gets the same S3 policies as the bucket of the Deployment.
  - A new policy: each CloudFront distribution uses origin access control, and redirects HTTP to HTTPS.
  - The allowlists of policy 4 get the generator, simulator and publisher policies. Only the simulator can start queries, and only on the demo workgroup.
- **The deployer draft policy:** it gets CloudFront, Glue, EventBridge and the new roles.

## Cost limits
About $3 a month or less (Q66). Athena costs about $1–2. CloudFront, S3, Lambda, EventBridge and Glue stay in their free tiers at demo traffic. The $10 Budget alert stays. The kill switches:
- Set `publicDemo` to `false`, and run `pulumi up`.
- Or disable the schedule of the simulator.

## Evaluation (Q55–Q57)
- **The window:** when the demo has run for 7 days, answer each question of Q57. Also answer "what did each team and tenant cost, reconciled?" (Q70). Answer them with data-finops (the public dashboard and the API) and with each tool of Q56:
  - Cost Explorer
  - the query history in the Athena console
  - the FOCUS export, queried with Athena SQL
  - Cloud Intelligence Dashboards, from their documentation
- **The record:** what each tool can answer, at what granularity, with what reconciliation, and what it costs. It is recorded in progress.md.
- **Balance:** the comparison states where AWS is better: breadth across services, commitments, forecasts, anomaly detection, and no build.

## Documentation (spec 07)
- **Explanation pages:**
  - "Compared with AWS's cost tools", with the table of the evaluation and its honest limits
  - the page of the demo: what is simulated and what is real
- **Reference:** the generated settings page gets the new settings and outputs. The CLI, HTTP and data-contract pages are generated again.
- **How-to:** a new page, `configure-cost-dimensions`, with the dbt macro that writes the comment.
- **How-to:** deploy-to-aws gets the `publicDemo` step.
- **Links:** the README, and the home page and navigation of the docs site, link to the public demo.
- **Diagrams, as each part lands:** `system-context.d2`, `ingest-flow.d2` and `deployment.d2`, rendered again. Also the architecture README: the domain model, a Cost Dimensions and Public Demo section, and the table of diagrams.
- **Specs, as each part lands:** spec 02 (`QueryExecution.claims`, the `dimensions` of the Allocation), spec 06 (the dimension route, the static mode of the dashboard) and spec 05 (the public surface).

## Tests
Each seam is test-first, with hand-made mutation checks recorded in the PR. No test touches a real account.
0. **Cost Dimensions:**
   - the validation of the configuration
   - only configured keys are stored, and the limits apply
   - the first source wins, across all three sources
   - `unassigned`
   - for each Cost Basis, the total of each dimension is equal to the total of the snapshot
   - a changed configuration alone makes no new snapshot, and stored snapshots do not change. The next snapshot from new inputs uses the changed configuration.
   - a claim never makes an Identity Link
   - sensitive dimensions are not shown
   - OpenAPI drift
1. **The simulated orchestrator:**
   - It runs with the Stubber of botocore for Athena and S3, and a fixed seed.
   - Each event and artifact that it writes passes the current readers.
   - The first line of each query is a Trusted Query Comment.
   - When its output is ingested into a temporary database, each Query Execution links to its Model Execution, and then to the DAG run.
2. **The dataset generator:** deterministic for a seed, with the declared size and schema.
3. **The publisher:**
   - On a temporary database with a demo line and an unrelated line, it writes only the files of the demo.
   - No 12-digit number other than the example.
   - Each file validates against the OpenAPI response schema of its route.
   - The swap is atomic.
4. **The static mode of the dashboard:** each route that the dashboard calls resolves to a file, or to the "not in the public demo" state.
5. **The stack:**
   - Under mocks, the `PublicDemo` component exists only when it is enabled.
   - The new and extended policies of the policy pack, with fixtures that are not compliant, through `pulumi policy analyze`.

## Owner-only steps
- **Deploy:** the deploy wizard gets the `publicDemo` stage.
  - It sets the setting and runs `pulumi up`.
  - It invokes `demoGeneratorFunction` one time and the simulator one time, then the collector on the next day.
  - It opens `demoUrl`.
- **Evaluation:** the owner reviews the evaluation before it is published.

## Exit
- `demoUrl` loads the dashboard with a snapshot less than 48 hours old. You can get to it with clicks from the README and from the docs site.
- The lines of the demo reconcile, their Coverage is recorded, and the total of each public Cost Dimension is equal to the total of the snapshot.
- The 7-day evaluation is recorded in progress.md, and the comparison page is published.
- The real cost of the demo for one month is recorded against the $3 target.
