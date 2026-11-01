# Decisions requiring validation

1. Which real AWS CUR 2.0 / FOCUS export shape and cost basis should production ingestion support first? Test against anonymized samples.
2. How will Athena query execution IDs be propagated through the chosen dbt Athena adapter and Airflow OpenLineage provider? Confirm actual hooks and query comments.
3. What provider-specific method will reconcile aggregated Athena billing to query-level metering, including minimum charges, discounts and adjustments?
4. Should local storage default to DuckDB only, or SQLite metadata + DuckDB analytics? Benchmark before choosing.
5. Which deployment topology (Lambda + S3 vs Cloud Run-equivalent patterns) minimizes AWS operational cost at target scale?
6. What authentication and network ingress patterns will customers require for OpenLineage events?
7. Validate current upstream versions, licenses, APIs, exact commits, test suites and FOCUS conversion coverage before integration.
8. Choose official project name and license after dependency compatibility review.
