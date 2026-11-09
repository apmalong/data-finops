# M7: how other systems carry attribution across the data lifecycle

**Status:** research note (facts, not decisions). Checked **2026-11-09**. Sources marked *(community)* or *(third-party)* are not vendor documentation. CloudZero and Finout were not reviewed.

**Finding:** the pattern is established, but each system covers one part. None found carries an organization's own attributes from the orchestrator through the query to the bill and reconciles the result.

| Job | System | How |
|---|---|---|
| Put attributes on the query | [dbt `query-comment`](https://docs.getdbt.com/reference/project-configs/query-comment) | A macro with `node`, `target`, `invocation_id` and `env_var`. `append` moves it to the end, needed on Snowflake, which strips leading comments. `job-label` makes BigQuery job labels |
| | [dbt-query-tags](https://github.ddaodan.workers.dev/get-select/dbt-query-tags) | Per-model tags (`query_tag = {'team': 'data'}`); its extension options are Snowflake-only |
| | [SQLCommenter](https://google.github.io/sqlcommenter/spec/), [merged into OpenTelemetry](https://cloud.google.com/blog/products/databases/sqlcommenter-merges-with-opentelemetry) | A comment after the statement: sorted, URL-encoded `key='value'` pairs |
| Put attributes on the run | [OpenLineage tags facets](https://openlineage.io/docs/spec/facets/job-facets/tag-facet) | Key, value and source on jobs, runs and datasets |
| Require them | [Airflow cluster policies](https://airflow.apache.org/docs/apache-airflow/3.2.1/administration-and-deployment/cluster-policies.html) | `dag_policy` can reject a DAG without required tags |
| | [Databricks budget policies](https://docs.databricks.com/admin/account-settings/usage-detail-tags) | Tag serverless usage. Policy tags win on conflict *([community](https://community.databricks.com/t5/data-governance/is-the-serverless-budget-usage-feature-officially-broken-for/m-p/155762))* |
| Map cost afterwards | [AWS Cost Categories](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/manage-cost-categories.html) | Ordered rules, first match wins; [split charges](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/splitcharge-cost-categories.html) appear only on the category's details page |
| | [Vantage virtual tags](https://docs.vantage.sh/virtual_tagging/) | Rules label and split costs without changing cloud resources |
| | Kubecost label mapping *([third-party](https://oneuptime.com/blog/post/2026-01-06-kubernetes-cost-monitoring-kubecost/view))* | Each organization names its owner and department labels. Unlabelled cost shows as `__unallocated__` |
| Join tags to cost | [Snowflake `QUERY_ATTRIBUTION_HISTORY`](https://docs.snowflake.com/en/sql-reference/account-usage/query_attribution_history) | Per-query compute cost with `QUERY_TAG`, 365 days; excludes very short queries and, *[per a community write-up](https://dev.classmethod.jp/articles/snowflake-query-attribution-history-view/)*, warehouse idle time |
| | [Dagster+ Insights](https://docs.dagster.io/dagster-plus/insights/integrating-bigquery-and-dbt) | A dbt comment ties cost to jobs and assets; on BigQuery, bytes billed × a unit price, so an estimate |

**Patterns adopted (ADR 0031):** ordered sources with the first match winning (Cost Categories); an organization naming its own attribute keys (Kubecost); a visible bucket for what is unlabelled (Kubecost); recording where a value came from (OpenLineage's `source`).
