"""Roll-ups (ADR 0016; spec 03, M4 structure 6): sums of Allocations, never Allocations.

A Roll-up groups the Allocations to Query Executions by something they belong to, a
Model Execution or a Workload Run, and sums them per currency and Cost Basis; amounts in
different currencies or bases are never added together. Synthetic targets (Idle,
Unallocated) belong to nothing. What an Allocation belongs to is read from its own
Evidence: the Identity Links the Calculation Run resolved and recorded (M3), so a stored
snapshot rolls up the same way whenever it is read.
"""

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from decimal import Decimal

from data_finops.contracts.allocation import Allocation, TargetType
from data_finops.contracts.billing import CostBasis
from data_finops.identity.links import ModelExecutionKey

_TRUSTED_QUERY_COMMENT = "dbt-query-comment"
_OPENLINEAGE_RUN = "openlineage-run"
_ATHENA_QUERY = "athena"


@dataclass(frozen=True, order=True)
class RollupTotal:
    currency: str
    cost_basis: CostBasis
    amount: Decimal


def query_execution_evidence(query_execution_id: str) -> str:
    """The Evidence an Allocation to an Athena Query Execution carries for the query itself."""
    return f"{_ATHENA_QUERY}:{query_execution_id}"


def query_execution_of(allocation: Allocation) -> str | None:
    """The Athena Query Execution an Allocation's Evidence names, or None."""
    for ref in allocation.evidence_refs:
        kind, _, query_execution_id = ref.partition(":")
        if kind == _ATHENA_QUERY and query_execution_id:
            return query_execution_id
    return None


def model_execution_evidence(model_execution: ModelExecutionKey) -> str:
    """The Evidence an Allocation carries when its Query Execution's Trusted Query Comment
    links this Model Execution."""
    invocation_id, node_id = model_execution
    return f"{_TRUSTED_QUERY_COMMENT}:{invocation_id}:{node_id}"


def workload_run_evidence(run_id: str) -> str:
    """The Evidence an Allocation carries for each Workload Run its Model Execution's
    invocation was reported under: the invocation's run, its parents and its root."""
    return f"{_OPENLINEAGE_RUN}:{run_id}"


def model_execution_of(allocation: Allocation) -> ModelExecutionKey | None:
    """The Model Execution an Allocation's Evidence links, or None if unlinked."""
    for ref in allocation.evidence_refs:
        kind, _, rest = ref.partition(":")
        invocation_id, _, node_id = rest.partition(":")
        if kind == _TRUSTED_QUERY_COMMENT and invocation_id and node_id:
            return ModelExecutionKey(invocation_id, node_id)
    return None


def workload_runs_of(allocation: Allocation) -> list[str]:
    """The Workload Runs an Allocation's Evidence names, in recorded order."""
    runs = []
    for ref in allocation.evidence_refs:
        kind, _, run_id = ref.partition(":")
        if kind == _OPENLINEAGE_RUN and run_id:
            runs.append(run_id)
    return runs


def model_execution_rollups(
    allocations: Iterable[Allocation],
) -> dict[ModelExecutionKey, tuple[RollupTotal, ...]]:
    """Each linked Model Execution's Roll-up."""
    return roll_up(allocations, lambda a: [me] if (me := model_execution_of(a)) else [])


def workload_run_rollups(allocations: Iterable[Allocation]) -> dict[str, tuple[RollupTotal, ...]]:
    """Each Workload Run's Roll-up: the Allocations whose Model Execution's invocation was
    reported under that run or below it. dbt-ol's per-model runs are the same work as
    their Model Execution (ADR 0022) and are not Roll-up targets."""
    return roll_up(allocations, workload_runs_of)


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
