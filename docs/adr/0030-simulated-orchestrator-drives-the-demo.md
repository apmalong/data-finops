# ADR 0030: A simulated orchestrator drives the demo with real Athena queries

Status: Accepted (design, 2026-11-07). Decides M7's demo workload (Q54, Q64, Q65, Q67). Complements ADR 0021, whose reference workload ran real dbt by hand and is torn down (#26).

## Context
The public demo (ADR 0029) needs fresh workload every day: Airflow DAG runs, dbt Model Executions and the Athena queries they issue. MWAA costs hundreds of dollars a month for one environment, and the owner ruled it out. Running real dbt-athena in a Lambda container is possible but heavy. What data-finops needs to prove is that a real bill line splits exactly among the real queries in its scope, and that those queries link to the runs that issued them. For that, the queries and their Trusted Query Comments must be real; the orchestrator around them need not be.

## Decision
1. **A simulated orchestrator.** A scheduled Lambda acts as a small Airflow and dbt. It runs a fixed set of believable DAGs (for example `daily_orders`, `hourly_events`, `weekly_finance`). Each DAG has tasks, and each dbt task runs a few models.
2. **Real queries, real comments.** Each model runs real SQL in a dedicated demo Athena workgroup, against a generated dataset of a few hundred MB in S3. The first line of the SQL is the dbt-athena query comment, with the model's `node_id` and the run's `invocation_id`, so Identity Links are made exactly as for real dbt (spec 03, ADR 0006).
3. **Believable records.**
   - **OpenLineage events:** the orchestrator writes events shaped like the Airflow OpenLineage provider's and dbt-ol's: a DAG run, task runs whose ParentRunFacet names it, and model runs under each task. They are templated from the real, anonymized events in `fixtures/reference-workload/`.
   - **dbt artifacts:** a manifest from the reference dbt project, renamed to the demo's models, and a `run_results.json` for each invocation.
   - **Delivery:** everything goes into the Deployment's Inbox, as a real producer would put it (ADR 0027).
4. **Variety, not randomness for its own sake.** Different models scan different amounts, a small share of tasks fail and are retried, and runs are scheduled away from the hour boundary (OQ-25). That way costs differ visibly, and the demo's lines are fully covered.
5. **Said plainly.** The page states that the workload is simulated, its Athena charges are real and reconciled to the bill, and the data trails by about a day.

## Consequences
- **Cost:** the demo's Athena cost is real but small, about $1–2 a month at the planned scans.
- **Coverage 1 is expected, and OQ-25 still applies:** the demo workgroup runs only the orchestrator's queries, so its lines should reach Coverage 1. A query finishing near an hour boundary can still show OQ-25.
- **dbt shapes drift:** the simulated events and artifacts must stay valid against the pinned schemas (OpenLineage 2-0-2, manifest v12, run results v6). Tests run them through the same readers as real input.
- **No evidence about real-world shapes:** the demo shows how data-finops works. ADR 0021's real runs remain the evidence that real Airflow and dbt emit these shapes.

## Alternatives rejected
- **MWAA:** too expensive for a demo.
- **Real dbt in a container Lambda:** heavier to build and run, for no gain in what the demo shows.
- **Entirely fake bill lines:** the point is that a real bill reconciles.
- **Re-running the reference workload by hand:** not daily, not automatic.

## Revisit triggers
- AWS changes Athena's billing so that the demo's lines no longer reconcile.
- The pinned OpenLineage or dbt schemas change.
- The demo's cost exceeds about $3 a month.
