"""Cost Dimensions: values resolved from their sources, and totals by value (ADR 0031).

Pure. A dimension groups Allocations as a Roll-up does. It never changes how a Billing Line is
partitioned, so the totals of each dimension, `unassigned` included, equal the totals of the
snapshot.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from data_finops.contracts.allocation import Allocation, DimensionValue
from data_finops.contracts.dimensions import (
    CostDimensions,
    Dimension,
    SourceKind,
)

DIMENSIONS_VERSION = "cost-dimensions/1"
"""The rule that resolves dimensions: sources in order, the first value wins. It joins every
policy version, with or without a configuration, so that a configuration alone never changes
an input digest (Q72)."""

UNASSIGNED = DimensionValue(value=None, source=None)

DimensionTotals = dict[tuple[str, str], dict[str | None, Decimal]]
"""For each (currency, Cost Basis): the amount from each source (None for unassigned)."""


def resolve(
    config: CostDimensions,
    *,
    claims: Mapping[str, str],
    tags: Sequence[Mapping[str, str]],
    workgroup: str | None,
) -> dict[str, DimensionValue]:
    """The value of each configured dimension for one Allocation Target.

    `claims` are the claims of the query, `tags` the tags of the linked Workload Run and then
    of each parent up to the root, and `workgroup` the workgroup of the query (or of the line,
    for an amount with no query). The first source with a value wins.
    """
    return {
        dimension.name: _resolve_one(dimension, claims, tags, workgroup)
        for dimension in config.dimensions
    }


def _resolve_one(
    dimension: Dimension,
    claims: Mapping[str, str],
    tags: Sequence[Mapping[str, str]],
    workgroup: str | None,
) -> DimensionValue:
    for source in dimension.sources:
        value: str | None = None
        if source.kind is SourceKind.QUERY_COMMENT and source.key is not None:
            value = claims.get(source.key)
        elif source.kind is SourceKind.OPENLINEAGE_TAG and source.key is not None:
            value = next((run[source.key] for run in tags if source.key in run), None)
        elif source.kind is SourceKind.WORKGROUP_MAP and workgroup is not None:
            value = dimension.workgroup_map.get(workgroup)
        if value:
            return DimensionValue(value=value, source=str(source), sensitive=dimension.sensitive)
    return DimensionValue(value=None, source=None, sensitive=dimension.sensitive)


def dimension_totals(
    allocations: Iterable[Allocation], name: str
) -> dict[str | None, DimensionTotals] | None:
    """What each value of the dimension `name` holds, by currency and Cost Basis, split by
    source. None when no Allocation carries the dimension."""
    totals: dict[str | None, DimensionTotals] = {}
    found = False
    for allocation in allocations:
        dimension = allocation.dimensions.get(name)
        if dimension is None:
            continue
        found = True
        by_basis = totals.setdefault(dimension.value, defaultdict(lambda: defaultdict(Decimal)))
        key = (allocation.currency, allocation.cost_basis)
        by_basis[key][dimension.source] += Decimal(allocation.amount)
    return totals if found else None
