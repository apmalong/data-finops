# M7 decisions (owner, 2026-11-07)

Code, specs, PRs and tickets cite these as "Q<n>". The numbers continue from M6's log (`m6-decisions.md`, Q1–Q52). Each question was asked with a recommended answer. The owner's answer is recorded where it differs from the recommendation; otherwise the recommendation was accepted. ADRs 0029–0030 and spec 09 remain authoritative; this log records only who decided what.

The owner's brief (2026-11-07): stand data-finops up for real, then show what the published site looks like and why it is better than AWS's own FinOps tools. "Ideally people could go to the repo or docs site and click their way to the dashboard to see it load with recent data." The DAG data may be simulated, but it must be believable and tied to real Athena usage, without running MWAA, "because that costs a lot of money".

## Round 1

| # | Question | Decision | Recorded in |
|---|---|---|---|
| Q53 | What the "persisted site" is | **Owner:** a hosted React or HTML site. Refined by Q61–Q63 into a static snapshot of the read model, served by the existing dashboard | ADR 0029, spec 09 |
| Q54 | Which workload | **Owner:** "some lambdas that run daily to generate data to play with". Refined by Q64–Q65 | ADR 0030, spec 09 |
| Q55 | Evaluation window and budget | 7 days, and a $10 account-wide Budget alert | spec 09 §Evaluation |
| Q56 | AWS tools to compare | Cost Explorer; the Athena console's query history; querying the FOCUS export with Athena SQL; Cloud Intelligence Dashboards from their documentation only, with no QuickSight spend | spec 09 §Evaluation |
| Q57 | The questions that decide "better" | What did a dbt model cost last week? What did an Airflow DAG run cost? Which queries drove a workgroup's bill? How much of the Athena bill is explained, and with what confidence? Does every allocated cent reconcile to the invoice? | spec 09 §Evaluation |
| Q58 | Who runs the deploy | The owner, with `deploy-and-verify.sh` | spec 09 §Owner-only steps |
| Q59 | Where the result goes | A site explanation page, "Compared with AWS's cost tools", with the raw evaluation record in progress.md | spec 09 §Documentation |

## Round 2

| # | Question | Decision | Recorded in |
|---|---|---|---|
| Q60 | What bill data is public | Only the demo workgroup's Athena usage lines; account IDs replaced; minimal Cost Explanations | ADR 0029 |
| Q61 | How the dashboard gets its data | A static snapshot: after each Collection Run, a publisher writes the read model's answers as JSON behind CloudFront. No public compute, no public API | ADR 0029 |
| Q62 | React or plain HTML | The existing M5 dashboard, adapted to read the static JSON. No build step | ADR 0029, spec 09 |
| Q63 | Where it lives | A new component in the same Pulumi stack, on CloudFront's own domain, linked from the README and the docs site | spec 09 |
| Q64 | The synthetic workload | A simulated orchestrator: a scheduled Lambda that runs believable DAGs whose dbt models run real SQL in a demo workgroup, with genuine dbt query comments, and writes OpenLineage events and dbt artifacts to the Inbox | ADR 0030 |
| Q65 | The demo dataset | A few hundred MB of generated orders and events data in S3, so queries scan visibly different amounts | ADR 0030, spec 09 |
| Q66 | Lifetime and cost | Runs indefinitely at about $3 a month or less, with the $10 Budget alert and the kill switch; the 7-day evaluation runs inside it | spec 09 |
| Q67 | Honesty on the page | A banner: the workload is simulated, its Athena charges are real and reconciled to the bill, and data trails by about a day | ADR 0030, spec 09 |

## Facts the decisions rest on

From the research note `docs/research/m7-aws-finops-tools.md`, checked 2026-11-07:
- No AWS-native tool attributes Athena cost below the workgroup: "You cannot tag queries."
- Cost Explorer's resource-level data covers 14 days, and Athena keeps query history for 45 days.
- MWAA cost stops at the environment.
- dbt Cloud's Cost Insights doesn't support Athena, and says its estimates are "not billing reconciliation".
