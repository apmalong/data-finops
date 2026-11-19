# M7 decisions (owner, 2026-11-07)

Code, specs, PRs and tickets cite these as "Q<n>". The numbers continue from M6's log (`m6-decisions.md`, Q1–Q52). Each question was asked with a recommended answer. The owner's answer is recorded where it differs from the recommendation; otherwise the recommendation was accepted. The Source column says which (AGENTS.md, Work with AI). In Q70, the brief is the owner's, and the timing was the recommendation. ADRs 0029–0031 and spec 09 remain authoritative; this log records only who decided what.

The owner's brief (2026-11-07): stand data-finops up for real, then show what the published site looks like and why it is better than AWS's own FinOps tools. "Ideally people could go to the repo or docs site and click their way to the dashboard to see it load with recent data." The DAG data may be simulated, but it must be believable and tied to real Athena usage, without running MWAA, "because that costs a lot of money".

## Round 1

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q53 | What the "persisted site" is | **Owner:** a hosted React or HTML site. Refined by Q61–Q63 into a static snapshot of the read model, served by the existing dashboard | Owner | ADR 0029, spec 09 |
| Q54 | Which workload | **Owner:** "some lambdas that run daily to generate data to play with". Refined by Q64–Q65 | Owner | ADR 0030, spec 09 |
| Q55 | Evaluation window and budget | 7 days, and a $10 account-wide Budget alert | Accepted recommendation | spec 09 §Evaluation |
| Q56 | AWS tools to compare | Cost Explorer; the Athena console's query history; querying the FOCUS export with Athena SQL; Cloud Intelligence Dashboards from their documentation only, with no QuickSight spend | Accepted recommendation | spec 09 §Evaluation |
| Q57 | The questions that decide "better" | What did a dbt model cost last week? What did an Airflow DAG run cost? Which queries drove a workgroup's bill? How much of the Athena bill is explained, and with what confidence? Does every allocated cent reconcile to the invoice? | Accepted recommendation | spec 09 §Evaluation |
| Q58 | Who runs the deploy | The owner, with `deploy-and-verify.sh` | Accepted recommendation | spec 09 §Owner-only steps |
| Q59 | Where the result goes | A site explanation page, "Compared with AWS's cost tools", with the raw evaluation record in progress.md | Accepted recommendation | spec 09 §Documentation |

## Round 2

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q60 | What bill data is public | Only the demo workgroup's Athena usage lines; account IDs replaced; minimal Cost Explanations | Accepted recommendation | ADR 0029 |
| Q61 | How the dashboard gets its data | A static snapshot: after each Collection Run, a publisher writes the read model's answers as JSON behind CloudFront. No public compute, no public API | Accepted recommendation | ADR 0029 |
| Q62 | React or plain HTML | The existing M5 dashboard, adapted to read the static JSON. No build step | Accepted recommendation | ADR 0029, spec 09 |
| Q63 | Where it lives | A new component in the same Pulumi stack, on CloudFront's own domain, linked from the README and the docs site | Accepted recommendation | spec 09 |
| Q64 | The synthetic workload | A simulated orchestrator: a scheduled Lambda that runs believable DAGs whose dbt models run real SQL in a demo workgroup, with genuine dbt query comments, and writes OpenLineage events and dbt artifacts to the Inbox | Accepted recommendation | ADR 0030 |
| Q65 | The demo dataset | A few hundred MB of generated orders and events data in S3, so queries scan visibly different amounts | Accepted recommendation | ADR 0030, spec 09 |
| Q66 | Lifetime and cost | Runs indefinitely at about $3 a month or less, with the $10 Budget alert and the kill switch; the 7-day evaluation runs inside it | Accepted recommendation | spec 09 |
| Q67 | Honesty on the page | A banner: the workload is simulated, its Athena charges are real and reconciled to the bill, and data trails by about a day | Accepted recommendation | ADR 0030, spec 09 |

## Round 3 (2026-11-09)

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q68 | Publish amounts before credits | Yes | Accepted recommendation | ADR 0029 |
| Q69 | Create the M7 spec issue and tickets | Yes, with the order revised for Q70 | Accepted recommendation | plan.md §M7 |
| Q70 | **Owner's brief:** "an attribution plane allowing users to configure this for their own resources". When | A small core in M7, before the simulated orchestrator: configured dimensions from the query comment, OpenLineage tags and a workgroup map | Accepted recommendation | ADR 0031, spec 09 §Cost Dimensions |
| Q71 | Its name | **Owner:** not "attribution plane". Named Cost Dimensions | Owner | GLOSSARY, ADR 0031 |
| Q72 | When a configuration change takes effect | **Owner:** "dimension changes should be applied going forward. no need for retroactive updates" | Owner | ADR 0031, spec 09 §Cost Dimensions |

## Round 2 (2026-11-15, PR #262)

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q73 | Which run's tags count for a query (asked with options only) | **Owner:** A. The run of the dbt invocation, which the Evidence of the Allocation names, then its parents up to the root. The per-model run of dbt-ol is not read | Owner | spec 09 §Cost Dimensions |
| Q74 | Sensitive values in the full API | `/v1/allocations` and `/v1/rollups/dimensions/{name}` show them. "The public form of the dimension route" is part of the Demo Snapshot (#227) | Accepted recommendation | spec 09 §Cost Dimensions |
| Q75 | What `dimensions_sha256` covers | The configuration only. ADR 0031 says that tag data is not in it | Accepted recommendation | ADR 0031 §Consequences |

## Round 3 (2026-11-15, #225)

Asked with options only. The owner chose each option, so no recommendation was given.

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q76 | How the dataset generator writes Parquet | **Owner:** with pyarrow. ADR 0018 lets the generator use it, and the import-boundary test allows that one module. The other option was Athena `CREATE TABLE AS SELECT` | Owner | ADR 0018 §Amendment (2026-11-15), spec 09 §The demo dataset |
| Q77 | Where the dataset is | **Owner:** in the demo bucket, under `dataset/`. #225 makes the bucket, and #229 limits CloudFront to the prefix of the site. The other options were the bucket of the Deployment, or a third bucket | Owner | spec 09 §The demo dataset, §The site |
| Q78 | What runs the generator | **Owner:** a Lambda function, with a role of its own, that the owner invokes one time. The other option was a CLI command with the owner's credentials | Owner | spec 09 §Layout, §The demo dataset |

## Round 4 (2026-11-15, #226)

Asked with options only. The owner chose each option, so no recommendation was given.

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q79 | Which ticket makes the simulator's function, role, schedule and policy-pack allowlist | **Owner:** all in #226. #229 keeps CloudFront, and the trigger, role and allowlist of the publisher. The other option was the Python code only in #226 | Owner | spec 09 §The simulated orchestrator |
| Q80 | The simulator's S3 grant | **Owner:** add what Athena needs: `s3:ListBucket` and `s3:GetBucketLocation` on the demo bucket, and `s3:AbortMultipartUpload` on the results. The other option was the grant of spec 09 as it was written | Owner | spec 09 §The simulated orchestrator |
| Q81 | How the demo workgroup gets into the collector's workgroups | **Owner:** the stack adds it when `publicDemo` is `true`. The other option was that the owner sets it | Owner | spec 09 §The simulated orchestrator |

## Round 5 (2026-11-15, #227)

Asked with options only. The owner chose each option, so no recommendation was given.

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q82 | How the publisher keeps only the demo's lines | **Owner:** a filter of Billing Lines in the read model, which the publisher sets to the demo workgroup's usage lines. The public numbers are the owner's Calculation Run, restricted. The other option was a Calculation Run on a filtered copy of the database | Owner | spec 06 §Which snapshot a read serves, spec 09 §The publisher |
| Q83 | Whether the publisher's role reads the Cost Dimensions file | **Owner:** no. Each Allocation records whether a dimension is sensitive. The other option was the grant that spec 09 named | Owner | spec 09 §Cost Dimensions |
| Q84 | Whether the publisher writes the lineage of runs that are not root runs | **Owner:** yes, for each run that published cost reaches (#271). Before, a task or model run opened from a Roll-up or a Cost Explanation showed "not in the public demo" | Owner | spec 09 §The publisher |

## Triage (2026-11-16)

The owner triaged the open issues, oldest first. The questions were asked with options only, so no recommendation was given.

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q85 | Policy 3 checks spec 08's absolute bounds for the timeout and the memory, not the values that each stack sets (#201) | **Owner:** confirmed. The pack cannot read a stack's settings | Owner | spec 08 §Policy pack |
| Q86 | The deploy wizard checks retain-on-delete on the real state after `pulumi up`, and warns instead of stopping (#203) | **Owner:** confirmed. A preview cannot show it, and the stack already exists then | Owner | the deploy wizard |
| Q87 | The Deployment's own policies are validated with IAM Access Analyzer only after `up`, from the real state (#203) | **Owner:** confirmed | Owner | spec 08 §IAM |
| Q88 | Teardown records `deleteDataOnDestroy` with a `pulumi up`, which also deploys the code of the checkout, after a warning (#203) | **Owner:** confirmed | Owner | the teardown wizard |
| Q89 | How a Deployment gets more `/tmp` than Lambda's 512 MB default (#206) | **Owner:** an `ephemeralStorageMb` setting, from 512 to 10,240 MB, default 10,240, and a policy pack check that it is set (#278). The other options were a fixed 10,240 MB, a smaller Inbox cap, and a documented limit | Owner | #278 |
| Q90 | Whether to recompute `athena-billable-usage/1` at the function level is enough for spec 03's "cut a new version rather than changing the old one" (#206) | **Owner:** yes. A Calculation Run uses the current versions, and `/1` stays callable as a function. The other options were a Calculation Run that pins a method version, and to decide later | Owner | this log |
| Q91 | Build the Collection Run warning for Inbox objects about to expire, or drop it from spec 08 (#211) | **Owner:** build it. The summary warns about each uncollected object that S3 expires in 7 days or fewer. The other option was to drop it | Owner | #211 |
| Q92 | What happens when a loaded bill does not reconcile with its source (#212) | **Owner:** `ingest focus` and each Collection Run reconcile each imported Export Snapshot. On a difference, the snapshot is rejected (exit code 1), and a run does not calculate on it. The other options were a warning only, and to keep reconciliation in tests and scripts | Owner | #212 |
| Q93 | Where `Ledger.ingest_focus(path)`, one entry point for FOCUS files, is built (#214) | **Owner:** in #212, which reconciles there. The other options were to build #214 first, and to keep both separate | Owner | #212, #214 |
| Q94 | Whether local OpenLineage files get the 512 MiB whole-file cap of Inbox files and dbt artifacts (#214) | **Owner:** yes, the same cap. The other option was no cap for local files | Owner | #214 |
| Q95 | Whether the Cost Summary takes `calculation_run_id` and `source`, as the other snapshot reads do (#215) | **Owner:** add both. The other option was to keep it current-only | Owner | #215 |
| Q96 | The GLOSSARY term for the SQLite file that holds the Ledger and the workload store (#216) | **Owner:** Database. The Ledger's _Avoid_ narrows to "do not call the Ledger the database". The other options were Database File and Store | Owner | #216 |
| Q97 | How to cut #242's property-based tests | **Owner:** split. #242 keeps the accounting invariants (owner-checked oracles), and reader fuzzing is a new ticket. The other option was one ticket | Owner | #242 |
| Q98 | Where the mutation tool of #243 runs | **Owner:** a CI workflow, weekly and on request, against a stored baseline. The other options were on request only, and locally only | Owner | #243 |
| Q99 | The test seams of #247 | **Owner:** build's stamped HTML, and a drift test. The other option was a drift test only | Owner | #247 |
| Q100 | Which real bill the owner computes the oracle of #244 from | **Owner:** the Public Demo's first real create-new Delivery. The other options were the #91 reference bill, and both | Owner | #244 |

## After M7: the units of Athena's billed quantity (#310, 2026-11-18)

Research: `docs/research/m7-athena-billing-units.md`. Two independent answers were asked for each recommendation (ask twice).

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q165 | How to read Athena's billed `TB`, and what each query bills | **Owner:** `athena-billable-usage/3`. `TB` is 2^40 bytes. Each query is its raw scan, at least 10^-5 TB. Each UTC hour's sum is rounded down to a multiple of 5 × 10^-6 TB. The other options were to keep decimal units, a binary unit with MiB rounding, and to ask AWS Support first. The owner first chose the binary unit without the hourly step. That alone gave Coverage 1.003 on the demo lines, and the owner then chose the full pattern | Owner | spec 03 item 8, ADR 0017 |
| Q166 | Whether the Coverage tolerance grows to one 5 × 10^-6 TB step | **Owner:** no. Add it only if lines land just over 1. One of the two answers recommended it | Owner | ADR 0017 |
| Q167 | How an hour's queries share the rounded-down sum | **Owner:** in proportion to their raw quantities. Both answers agreed. The other options were raw weights (then Coverage exceeds 1) and a rounding of each query | Owner | spec 03 item 8 |
| Q168 | Which hour a query belongs to | **Owner:** the UTC hour of its completion. The other option was the hour of submission | Owner | spec 03 item 8 |

## After M7: files straight under a Billing Period (#302, 2026-11-19)

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q169 | Whether a Collection Run warns about the files directly under a Billing Period that has Delivery folders | **Owner:** no warning. Create-new mode always keeps the newest manifest there (#305), so a warning would show on each run. The run ignores those files, as #306 built. The other options were a warning for each file, and a warning for each file except the export's own manifest | Owner | spec 08 §Collection Run 2 |

## After M7: CI runs only the checks that a change needs (#321, 2026-11-19)

| # | Question | Decision | Source | Recorded in |
|---|---|---|---|---|
| Q170 | When CI runs | On a PR and on a push to `main`. A push to another branch runs nothing until it has a PR. A new push to a PR cancels its run in progress | Owner (#321) | `.github/workflows/ci.yml`, AGENTS.md |
| Q171 | What a documents-only change runs | `scripts.gates --docs` (site drift check, site check, STE) and the secret scan. A document is Markdown at the root, or Markdown under `docs/` or `.claude/`. Any other file there, for example an image or `settings.json`, runs every check. The root `uv sync` still runs, because the site build imports `data_finops.cli` for the CLI reference | Accepted recommendation | `scripts/ci_changes.py`, AGENTS.md |
| Q172 | Which paths skip the `infra` job | `site/`, `tests/`, `scripts/` (except `gates.py`), `fixtures/`, `schemas/`, `examples/` and documents. The infra job reads only `infra/`, `src/`, the root `pyproject.toml` and `uv.lock`. Any other path runs every check | Accepted recommendation | `scripts/ci_changes.py`, AGENTS.md |
| Q173 | What happens when the sort fails | Every check runs. `quality` always starts, and `infra` runs unless the sort succeeded and said that it is not needed | Accepted recommendation | `.github/workflows/ci.yml` |

## Facts the decisions rest on

From the research note `docs/research/m7-aws-finops-tools.md`, checked 2026-11-07:
- No AWS-native tool attributes Athena cost below the workgroup: "You cannot tag queries."
- Cost Explorer's resource-level data covers 14 days, and Athena keeps query history for 45 days.
- MWAA cost stops at the environment.
- dbt Cloud's Cost Insights doesn't support Athena, and says its estimates are "not billing reconciliation".
