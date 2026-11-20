"""A Calculation Run's Cost Dimensions, for any configuration (#242; ADR 0031).

`run_calculation` attaches each configured dimension to every Allocation, from the query's
claims, the linked Workload Runs' tags and the workgroup. For any configuration that the
fixture data can resolve, the totals of each dimension, over every value and source, sum to
the snapshot's, per currency and Cost Basis.

Each example fills a new database, so this property runs fewer examples than the unit ones:
25 in the `ci` profile.
"""

import tempfile
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from fixture_database import ACCOUNT, EXPORT, FIXTURES, REGION
from hypothesis import given, settings
from hypothesis import strategies as st

from data_finops.attribution.dimensions import dimension_totals
from data_finops.calculation import run_calculation
from data_finops.contracts.allocation import Allocation
from data_finops.contracts.dimensions import CostDimensions
from data_finops.ledger import Ledger
from data_finops.reporting import Reporting
from data_finops.workload.store import WorkloadStore

SOURCES = [
    "query_comment.target_name",
    "query_comment.tenant_id",
    "query_comment.absent",
    "openlineage_tag.team",
    "openlineage_tag.absent",
    "workgroup_map",
]
"""Sources the fixture data can resolve, and two that it never can."""


@st.composite
def configurations(draw: st.DrawFn) -> CostDimensions:
    names = draw(st.lists(st.sampled_from(["env", "team", "tenant"]), unique=True, min_size=1))
    tables: dict[str, dict[str, Any]] = {}
    for name in names:
        sources = draw(st.lists(st.sampled_from(SOURCES), unique=True, min_size=1, max_size=3))
        table: dict[str, Any] = {"sources": sources}
        if "workgroup_map" in sources:
            table["workgroup_map"] = draw(
                st.dictionaries(st.sampled_from(["demo", "other"]), st.sampled_from(["a", "b"]))
            )
        tables[name] = table
    return CostDimensions.from_mapping({"dimensions": tables}, sha256="c" * 64)


def _calculated(path: Path, dimensions: CostDimensions) -> list[Allocation]:
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        ledger.import_snapshot(EXPORT)
        store.ingest_query_history(
            FIXTURES / "athena" / "query-executions.json",
            account_id=ACCOUNT,
            region=REGION,
            dimensions=dimensions,
        )
        store.ingest_events(FIXTURES / "openlineage" / "events.json")
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "manifest.json")
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "run_results.json")
        run_calculation(ledger, store, EXPORT.snapshot.scope, dimensions=dimensions)
    with Reporting(path) as read_model:
        return [entry.allocation for entry in read_model.allocations(limit=1000).items]


def _totals(allocations: list[Allocation]) -> dict[tuple[str, str], Decimal]:
    totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for allocation in allocations:
        totals[(allocation.currency, allocation.cost_basis)] += allocation.amount
    return dict(totals)


@settings(max_examples=max(25, settings().max_examples // 80))
@given(configurations())
def test_each_dimensions_totals_sum_to_the_calculated_snapshots(
    dimensions: CostDimensions,
) -> None:
    with tempfile.TemporaryDirectory() as folder:
        allocations = _calculated(Path(folder) / "data-finops.sqlite", dimensions)
    assert allocations
    snapshot = _totals(allocations)
    for dimension in dimensions.dimensions:
        assert all(dimension.name in a.dimensions for a in allocations)
        totals = dimension_totals(allocations, dimension.name)
        assert totals is not None
        summed: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for by_basis in totals.values():
            for key, by_source in by_basis.items():
                summed[key] += sum(by_source.values(), Decimal(0))
        assert dict(summed) == snapshot
