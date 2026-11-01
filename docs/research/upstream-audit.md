# Upstream source audit — evidence and required follow-up

**Status: PRELIMINARY ONLY.** Prior discussion identified repository-level candidates and likely source areas but did NOT establish exact commit SHAs, inspect full transitive dependency trees, execute upstream tests, or verify every file's license. Do not claim otherwise.

| Project | URL | Likely reuse | Decision for MVP | License check required |
|---|---|---|---|---|
| FOCUS Converters | https://github.com/finopsfoundation/focus_converters | Python normalization rules and converter | Optional dependency for legacy exports | Exact files, package version, notices |
| OpenLineage | https://github.com/OpenLineage/OpenLineage | Spec, Python SDK, Airflow integration | Protocol + SDK; implement small receiver | SDK release and spec |
| Elementary dbt reliability | https://github.com/elementary-data/dbt-data-reliability | dbt hooks and artifact persistence | Reference/optional existing tables | Per-file license |
| Elementary | https://github.com/elementary-data/elementary | dbt metadata parsing | Reference; direct artifacts preferred | Per-file license |
| OpenCost | https://github.com/opencost/opencost | shared/idle allocation concepts | Future API/reference | Exact Go code before extraction |
| Cloud Custodian | https://github.com/cloud-custodian/cloud-custodian | filters and policies | Future optional optimization | Exact Python modules |
| CloudQuery | https://github.com/cloudquery/cloudquery | inventory schemas | Future optional integration | Mixed licensing: file-level review |
| SQLGlot | https://github.com/tobymao/sqlglot | SQL parsing | Future pinned dependency | Version/license |
| Marquez | https://github.com/MarquezProject/marquez | lineage graph concepts | No default deployment | File-level review |

## Required audit procedure before copying code
1. Pin immutable upstream commit SHA and source URL.
2. Inspect `LICENSE`, `NOTICE`, headers, submodules and nested licenses for **each copied file**.
3. Enumerate imported modules and transitive dependencies, including native and generated code.
4. Run upstream tests relevant to extracted files before and after adaptation.
5. Preserve copyright/license notices, identify modifications, update `THIRD_PARTY_NOTICES.md` and `third_party/provenance.yaml`.
6. Run license/SBOM scans in CI; review copyleft incompatibilities before copying.
7. Record upstream issue/patch tracking plan and why dependency use was rejected.

## Extraction record template
```
name:
upstream_url:
commit_sha:
source_paths: []
local_paths: []
spdx_license:
notice_files: []
modifications: []
dependencies: []
upstream_tests:
local_tests:
reviewed_by:
```

Do not copy source simply because repository is public. Attribution alone does not satisfy every open-source license.
