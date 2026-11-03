# Offline demo walkthrough

1. Install project dependencies after M0 implementation.
2. Run fixture importer against `fixtures/`.
3. Start FastAPI and open dashboard.
4. Inspect query `q-001`: $6 metered estimate from the eligible Athena line at Coverage = 1 (ADR 0017), rolled up to `model.demo.orders` and the `daily_orders` run.
5. Inspect `model.demo.customers`: no linked query (only an ambiguous claim); no invented direct allocation.
6. Inspect the $4 ineligible line as Unallocated.
7. Re-run import; assert $10 total unchanged.
8. Restate the billing Export Snapshot and verify a new current Allocation Snapshot with the previous one retained.

**These are test inputs, not actual AWS billing records or valid provider pricing assertions.** The fixture schema is intentionally minimal and may require adaptation to real dbt/OpenLineage versions; pin and validate against official schemas before implementation.
