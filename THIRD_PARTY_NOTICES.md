# Third-party notices

No third-party source code has been copied into this package. Links and project names are references only. Add required license text and notices here **before** any source extraction or redistribution.

## OpenLineage specification schemas

`src/data_finops/lineage/schemas/OpenLineage-2-0-2.json` and `ParentRunFacet-1-1-0.json` are unmodified copies of the OpenLineage specification JSON Schemas (https://openlineage.io/spec/2-0-2/OpenLineage.json and https://openlineage.io/spec/facets/1-1-0/ParentRunFacet.json), redistributed to validate events offline (#42). Their sha256 values are pinned in `scripts/verify_upstream.py` and checked by a test.

Copyright 2018-2026 contributors to the OpenLineage project. Licensed under the Apache License, Version 2.0 (https://www.apache.org/licenses/LICENSE-2.0). Distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND.
