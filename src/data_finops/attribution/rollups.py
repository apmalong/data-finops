"""Roll-ups (ADR 0016; spec 03, M4 structure 6): sums of Allocations, never Allocations.

A Roll-up groups the Allocations to Query Executions by something they belong to, a
Model Execution or a Workload Run, and sums them per currency and Cost Basis; amounts in
different currencies or bases are never added together. Synthetic targets (Idle,
Unallocated) belong to nothing. Model Executions are read from the Allocations' own
Evidence, the Identity Link the Calculation Run recorded, so a stored snapshot rolls up
the same way whenever it is read.
"""

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple

from data_finops.contracts.allocation import Allocation, TargetType
from data_finops.contracts.billing import CostBasis

_MODEL_EXECUTION_EVIDENCE = "dbt-query-comment"


class ModelExecutionRef(NamedTuple):
    invocation_id: str
    node_id: str


@dataclass(frozen=True, order=True)
class RollupTotal:
    currency: str
    cost_basis: CostBasis
    amount: Decimal


def model_execution_evidence(model_execution: ModelExecutionRef) -> str:
    """The Evidence reference an Allocation carries when its Query Execution's Trusted
    Query Comment links this Model Execution."""
    return f"{_MODEL_EXECUTION_EVIDENCE}:{model_execution.invocation_id}:{model_execution.node_id}"


def model_execution_of(allocation: Allocation) -> ModelExecutionRef | None:
    """The Model Execution an Allocation's Evidence links, or None if unlinked."""
    for ref in allocation.evidence_refs:
        kind, _, rest = ref.partition(":")
        invocation_id, _, node_id = rest.partition(":")
        if kind == _MODEL_EXECUTION_EVIDENCE and invocation_id and node_id:
            return ModelExecutionRef(invocation_id, node_id)
    return None


def model_execution_rollups(
    allocations: Iterable[Allocation],
) -> dict[ModelExecutionRef, tuple[RollupTotal, ...]]:
    """Each linked Model Execution's Roll-up."""
    return roll_up(allocations, lambda a: [me] if (me := model_execution_of(a)) else [])


def roll_up[Group: Hashable](
    allocations: Iterable[Allocation], groups: Callable[[Allocation], Iterable[Group]]
) -> dict[Group, tuple[RollupTotal, ...]]:
    """Sum Allocations to Query Executions into each group `groups` names for them, per
    currency and Cost Basis, counting an Allocation once per group."""
    sums: dict[Group, dict[tuple[str, CostBasis], Decimal]] = defaultdict(
        lambda: defaultdict(Decimal)
    )
    for allocation in allocations:
        if allocation.target_type != TargetType.QUERY_EXECUTION:
            continue
        key = (allocation.currency, CostBasis(allocation.cost_basis))
        for group in set(groups(allocation)):
            sums[group][key] += allocation.amount
    return {
        group: tuple(sorted(RollupTotal(c, b, amount) for (c, b), amount in totals.items()))
        for group, totals in sums.items()
    }
