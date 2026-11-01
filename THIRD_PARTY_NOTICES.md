# Third-party notices

No third-party source code has been copied into this package. Links and project names are references only. Add required license text and notices here **before** any source extraction or redistribution.

## OpenLineage specification schemas

- **Files:** `src/data_finops/lineage/schemas/OpenLineage-2-0-2.json` and `ParentRunFacet-1-1-0.json`, redistributed to validate events offline (#42).
- **Source:** https://openlineage.io/spec/2-0-2/OpenLineage.json and https://openlineage.io/spec/facets/1-1-0/ParentRunFacet.json, published by the OpenLineage project (https://github.com/OpenLineage/OpenLineage, `spec/`). The served files are versioned by URL rather than a repository commit; their sha256 values are pinned in `scripts/verify_upstream.py` and checked by a test.
- **Modifications:** none; byte-identical copies.
- **License:** Apache License 2.0; full text in `LICENSES/OpenLineage-Apache-2.0.txt` (copied from the OpenLineage repository's LICENSE). The OpenLineage repository has no NOTICE file (checked 2026-11-01).
- **Copyright:** contributors to the OpenLineage project, as stated in OpenLineage's source file headers ("Copyright 2018-2026 contributors to the OpenLineage project").
