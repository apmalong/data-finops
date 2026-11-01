# Offline demo walkthrough

1. Install project dependencies after M0 implementation.
2. Run fixture importer against `fixtures/`.
3. Start FastAPI and open dashboard.
4. Inspect model `model.demo.orders`: exact query evidence `q-001`, allocation from $6 synthetic eligible pool under documented metering rule.
5. Inspect `model.demo.customers`: no query evidence; no invented direct allocation.
6. Inspect $4 unknown pool as unallocated.
7. Re-run import; assert $10 total unchanged.
8. Correct a source billing record and verify versioned recomputation.

**These are test inputs, not actual AWS billing records or valid provider pricing assertions.** The fixture schema is intentionally minimal and may require adaptation to real dbt/OpenLineage versions; pin and validate against official schemas before implementation.
