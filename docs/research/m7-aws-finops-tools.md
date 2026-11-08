# M7: what AWS's own FinOps tools can attribute Athena cost to

**Status:** research note (facts, not decisions). All sources checked **2026-11-07**. Anything not confirmed from a primary source is marked **UNVERIFIED**.

**Question:** can AWS's tools answer "what did each dbt model, Airflow DAG run or Athena query cost, reconciled to the bill?" This feeds the comparison M7 publishes (spec 09 §Evaluation, Q56, Q57).

**Bottom line:** no AWS-native tool attributes Athena cost below the **workgroup**, or to dbt models or Airflow runs. Athena tags only workgroups, data catalogs and capacity reservations, and its documentation says "You cannot tag queries." Anything finer has to be built from the bill export plus query metadata.

| Tool | Finest unit for Athena cost | Reconciles to the bill | Lineage to dbt or Airflow | Lag | Cost | Setup |
|---|---|---|---|---|---|---|
| Cost Explorer, with Cost Categories and tags | the workgroup: by a workgroup tag, or by daily resource-level data (opt-in, last 14 days only). Hourly data has no resource detail, also 14 days | yes: it is billing data | no | UNVERIFIED (commonly within 24 hours); settings changes take up to 48 hours | console free; API $0.01 a request; hourly and resource data about $0.01 per 1,000 usage records a month | low. History 13–14 months by default, 38 months monthly if enabled |
| Athena workgroup tags, and CloudWatch | the workgroup. CloudWatch's `ProcessedBytes` per DML query has only the WorkGroup, QueryState, QueryType and CapacityReservation dimensions, and is bytes, not cost | tags: yes, through the bill. CloudWatch: no | no | CloudWatch near real time. Query history kept 45 days | CloudWatch standard | low |
| Data Exports: CUR 2.0 and FOCUS 1.2 | an hourly line per usage type and resource. The Athena ResourceId is the workgroup ARN (confirmed on a real export, #91, OQ-3) | yes; FOCUS 1.2 adds `InvoiceId` | no: anything finer is built by the user (Athena, QuickSight) | refreshed up to 3 times a day | export free; S3 storage charged | medium |
| Cloud Intelligence Dashboards (CUDOS, CID, KPI) | service, usage type and tags. Athena-specific visuals UNVERIFIED | yes: built on CUR | no | daily SPICE refresh | QuickSight seats: Reader $3, Author $24, Reader Pro $20, Author Pro $40 a user a month | medium: 15–30 minutes automated, first data in about 24 hours |
| Split Cost Allocation Data | not available for Athena: ECS (with Fargate), EKS (with Kubernetes labels), AWS Batch per a secondary source | yes | no | CUR cadence | free | medium; not in FOCUS 1.2 |
| Newer features (Amazon Q cost analysis, Cost Comparison, Billing View, the Billing MCP server, user attributes) | Cost Explorer's dimensions. User attributes cover only Q Business, Q Developer and QuickSight | yes | no | as Cost Explorer | mostly free | low |
| MWAA | the environment ARN, by tags | yes | no per-DAG cost | CUR cadence | per environment hour | low |
| dbt Cloud Cost Insights | per model, per job | no: its documentation says the estimates are "not billing reconciliation" | dbt only | daily, 7-day window | Enterprise plans | low. Snowflake, BigQuery, Databricks, Redshift (preview); **not Athena** |
| SELECT (select.dev) | per query and per dbt model, on Snowflake | Snowflake billing components | dbt | n/a | commercial | **not Athena** |

## Fair comparison points

- **Query and run level:** AWS tools stop at the workgroup. data-finops goes to the Athena Query Execution, then rolls up to dbt Model Executions and Airflow DAG and task runs.
- **Reconciliation:** the closest AWS-published approach (a 2020 Big Data blog post) uses bytes scanned as a proxy, without reconciling. data-finops partitions each Billing Line exactly and states its Coverage and Confidence.
- **Retention:** anything finer than the workgroup needs a persisted store of its own, given the 45-day query history and 14-day resource data.
- **Where AWS is better: breadth.** AWS covers every service: S3, Glue, MWAA, compute. data-finops covers Athena on-demand query cost only.
- **Where AWS is better: maturity.** AWS has commitments and discounts (RIs, Savings Plans), forecasting, anomaly detection, budgets, Cost Optimization Hub and multi-org Billing Views. data-finops has none of these.
- **Where AWS is better: zero build.** If one workgroup per team answers "cost per team", Cost Explorer with workgroup tags may be enough.
- **Neither covers:** per-query allocation of Athena provisioned capacity (DPU-hours) or Athena Spark.

## Sources (checked 2026-11-07)

- **Athena:**
  - https://docs.aws.amazon.com/athena/latest/ug/tags.html
  - https://docs.aws.amazon.com/athena/latest/ug/queries-viewing-history.html
  - https://docs.aws.amazon.com/athena/latest/ug/query-metrics-viewing.html
  - https://docs.aws.amazon.com/athena/latest/ug/workgroups-access.html
- **Cost Explorer and Billing and Cost Management:**
  - https://docs.aws.amazon.com/cost-management/latest/userguide/ce-configuring-data.html
  - https://docs.aws.amazon.com/cost-management/latest/userguide/ce-multi-year-data.html
  - https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/
  - https://aws.amazon.com/about-aws/whats-new/2023/11/aws-cost-explorer-provides-historical-granular-data
  - https://docs.aws.amazon.com/cost-management/latest/userguide/doc-history.html
- **Data Exports, CUR and Split Cost Allocation Data:**
  - https://docs.aws.amazon.com/cur/latest/userguide/table-dictionary-focus-1-2-aws.html
  - https://aws.amazon.com/about-aws/whats-new/2025/11/aws-data-exports-focus-1-2-available
  - https://aws.amazon.com/aws-cost-management/aws-data-exports/faqs/
  - https://docs.aws.amazon.com/cur/latest/userguide/split-cost-allocation-data.html
- **Cloud Intelligence Dashboards and QuickSight:**
  - https://docs.aws.amazon.com/guidance/latest/cloud-intelligence-dashboards/cudos-cid-kpi.html
  - https://aws.amazon.com/quicksight/pricing/
- **Newer Cost Management features:**
  - https://aws.amazon.com/blogs/aws-cloud-financial-management/aws-cloud-financial-management-key-reinvent-2025-launches-to-transform-your-finops-practice
  - https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/user-attributes-cost-allocation.html
- **The 2020 Athena cost blog post:** https://aws.amazon.com/blogs/big-data/auditing-inspecting-and-visualizing-amazon-athena-usage-and-cost/
- **MWAA:** https://docs.aws.amazon.com/mwaa/latest/userguide/cost-allocation-tags.html
- **dbt Cloud and SELECT:**
  - https://docs.getdbt.com/docs/explore/cost-insights
  - https://select.dev/docs/resources/dbt-snowflake-monitoring/dbt-snowflake-monitoring
