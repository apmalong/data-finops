"""The Calculation Run (spec 03 steps B, E and H; M4 structure 2 and 4).

A thin orchestrator: reads the current Export Snapshot for a Snapshot Scope from the
Ledger and the Query Executions from the workload store, classifies each Billing Line,
applies the Athena policies, runs the pure engine and stores the Allocation Snapshot. It
lives outside `attribution` because the engine may not import the Ledger, the workload
store or provider policies (architecture-boundaries.md).
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from uuid import uuid4

from data_finops.attribution.dimensions import DIMENSIONS_VERSION, resolve
from data_finops.attribution.engine import (
    ENGINE_VERSION,
    Adjustment,
    Ineligible,
    MeteredCostPool,
    MeteredUsage,
    PooledLine,
    allocate,
)
from data_finops.attribution.rollups import (
    model_execution_evidence,
    query_execution_evidence,
    workload_run_evidence,
    workload_runs_of,
)
from data_finops.contracts.allocation import POLICY_SEPARATOR, Allocation, TargetType
from data_finops.contracts.billing import (
    FocusBillingLine,
    FocusChargeCategory,
    SnapshotScope,
)
from data_finops.contracts.dimensions import NO_DIMENSIONS, CostDimensions
from data_finops.contracts.workload import QueryExecution
from data_finops.identity.links import ModelExecutionKey
from data_finops.ledger import AllocationStoreOutcome, Ledger
from data_finops.providers import athena_billing
from data_finops.workload.store import WorkloadStore

CLASSIFICATION_VERSION = "line-classification/1"
"""Step B, FOCUS-generic: Credit and Adjustment lines are Adjustments; only usage lines
that are not corrections can be metered; Ineligible and Adjustment pool IDs."""

POLICY_VERSION = POLICY_SEPARATOR.join(
    (
        ENGINE_VERSION,
        CLASSIFICATION_VERSION,
        athena_billing.POLICY_VERSION,
        athena_billing.SCOPE_POLICY_VERSION,
        DIMENSIONS_VERSION,
    )
)
"""Every rule that shapes an Allocation: the engine's, line classification, both Athena
policies and Cost Dimension resolution."""

_ADJUSTMENTS = frozenset({FocusChargeCategory.CREDIT, FocusChargeCategory.ADJUSTMENT})
# Stable names for the input digest, independent of Python class names.
_POOL_KINDS = {MeteredCostPool: "metered", Ineligible: "ineligible", Adjustment: "adjustment"}


@dataclass(frozen=True)
class CalculationResult:
    outcome: AllocationStoreOutcome
    calculation_run_id: str
    """The current snapshot: this run's, or on a replay the one it reproduced."""
    input_digest: str
    ineligible_lines: tuple[str, ...]
    """Billing Lines no MVP policy allocates (tax, purchases, corrections, other services,
    lines without a positive ConsumedQuantity in a known unit): wholly Unallocated."""
    unknown_billable_usage: tuple[str, ...]
    """Query Executions in a metered line's scope that reported no scan: left out of
    known usage, so their line's Coverage falls below one (never guessed)."""


def run_calculation(
    ledger: Ledger,
    store: WorkloadStore,
    scope: SnapshotScope,
    *,
    new_run_id: Callable[[], str] = lambda: f"calc-{uuid4()}",
    dimensions: CostDimensions = NO_DIMENSIONS,
) -> CalculationResult:
    """Allocate the scope's current Export Snapshot and store it as an Allocation Snapshot.

    Each Allocation records the value of each Cost Dimension in `dimensions` (ADR 0031). The
    configuration is not part of the input digest: its SHA-256 is recorded on the snapshot,
    so a changed configuration alone replays the current snapshot (Q72).

    Raises LookupError if the Ledger holds no Export Snapshot for the scope.
    """
    export = ledger.current_snapshot(scope)
    if export is None:
        raise LookupError(f"no Export Snapshot is stored for {scope}")
    unknown: set[str] = set()
    pooled = [
        PooledLine(export.line_ref(position), line, _pool(line, store, unknown))
        for position, line in enumerate(export.lines)
    ]
    unknown_usage = tuple(sorted(unknown))
    digest = _input_digest(export.snapshot.snapshot_id, pooled, unknown_usage)
    run_id = new_run_id()
    stored = ledger.store_allocations(
        scope,
        calculation_run_id=run_id,
        policy_version=POLICY_VERSION,
        input_digest=digest,
        export_snapshot_id=export.snapshot.snapshot_id,
        allocations=_with_dimensions(
            allocate(pooled, policy_version=POLICY_VERSION, calculation_run_id=run_id),
            {p.ref: p.line for p in pooled},
            store,
            dimensions,
        ),
        dimensions_sha256=dimensions.sha256,
    )
    return CalculationResult(
        stored.outcome,
        stored.calculation_run_id,
        digest,
        ineligible_lines=tuple(p.ref for p in pooled if isinstance(p.pool, Ineligible)),
        unknown_billable_usage=unknown_usage,
    )


def _with_dimensions(
    allocations: list[Allocation],
    lines: dict[str, FocusBillingLine],
    store: WorkloadStore,
    dimensions: CostDimensions,
) -> list[Allocation]:
    """The Allocations, each with the value of every configured dimension (ADR 0031).

    For an Allocation to a Query Execution, the sources are its claims, the tags of the
    Workload Runs its Evidence names (nearest first) and its workgroup. For Idle and
    Unallocated, only the workgroup of the line.
    """
    if not dimensions.dimensions:
        return allocations
    tags: dict[str, dict[str, str]] = {}
    queries: dict[tuple[str, str, str], QueryExecution | None] = {}
    resolved = []
    for allocation in allocations:
        line = lines[allocation.billing_line_ref]
        claims: dict[str, str] = {}
        workgroup = athena_billing.line_workgroup(line)
        runs: list[str] = []
        if allocation.target_type is TargetType.QUERY_EXECUTION:
            key = (line.SubAccountId or "", line.RegionId or "", allocation.target_id)
            if key not in queries:
                queries[key] = store.query_execution(*key)
            query = queries[key]
            if query is not None:
                claims, workgroup = query.claims, query.workgroup
            runs = workload_runs_of(allocation)
        for run in runs:
            if run not in tags:
                tags[run] = store.run_tags(run)
        values = resolve(
            dimensions, claims=claims, tags=[tags[run] for run in runs], workgroup=workgroup
        )
        resolved.append(allocation.model_copy(update={"dimensions": values}))
    return resolved


def _pool(
    line: FocusBillingLine, store: WorkloadStore, unknown: set[str]
) -> MeteredCostPool | Ineligible | Adjustment:
    """Classify a line (spec 03 step B, `CLASSIFICATION_VERSION`) and, if it is metered,
    gather the Billable Usage of the Query Executions in its scope.

    A metered line without a usage account or region has nothing in scope: Coverage 0.
    """
    day = line.ChargePeriodStart.date().isoformat()
    if line.ChargeCategory in _ADJUSTMENTS:
        return Adjustment(f"adjustment:{line.BillingAccountId}:{day}")
    unit = line.ConsumedUnit
    meterable = line.ChargeCategory == FocusChargeCategory.USAGE and line.ChargeClass is None
    if not (meterable and unit and athena_billing.is_on_demand_usage(line)):
        return Ineligible(f"ineligible:{line.BillingAccountId}:{day}")
    usages = []
    queries = (
        store.query_executions(line.SubAccountId, line.RegionId)
        if line.SubAccountId and line.RegionId
        else []
    )
    for query in queries:
        if not athena_billing.in_billing_scope(query, line):
            continue
        billable = athena_billing.billable_bytes(query)
        if billable is None:
            unknown.add(query.query_execution_id)
            continue
        quantity = athena_billing.bytes_in_consumed_unit(billable, unit)
        usages.append(MeteredUsage(query.query_execution_id, quantity, _evidence(query, store)))
    return MeteredCostPool(athena_billing.pool_id(line), tuple(usages))


def _evidence(query: QueryExecution, store: WorkloadStore) -> tuple[str, ...]:
    """The query itself and, through M3's Identity Links, the Model Execution it belongs to
    and every Workload Run that invocation was reported under (the invocation's run, its
    parents and its root), so Roll-ups read them from the stored snapshot."""
    # A LinkResolution carries a link exactly when its outcome is `linked` (contract).
    refs = [query_execution_evidence(query.query_execution_id)]
    resolution = store.model_execution_of_query(
        query.account_id, query.region, query.query_execution_id
    )
    if resolution is None or resolution.link is None:
        return tuple(refs)
    model_execution = ModelExecutionKey.from_target_id(resolution.link.target_id)
    refs.append(model_execution_evidence(model_execution))
    run = store.workload_run_of_invocation(model_execution.invocation_id)
    if run is not None and run.link is not None:
        runs = [run.link.target_id, *store.ancestors(run.link.target_id)]
        refs += [workload_run_evidence(run_id) for run_id in runs]
    return tuple(refs)


def _input_digest(
    snapshot_id: str, pooled: list[PooledLine], unknown_usage: tuple[str, ...]
) -> str:
    """SHA-256 over everything the run depends on: the Export Snapshot (its lines are
    immutable under its ID), each line's pool with every Query Execution's Billable Usage
    and Evidence, the queries whose usage is unknown, and the policy version."""
    document = {
        "export_snapshot_id": snapshot_id,
        "policy_version": POLICY_VERSION,
        "unknown_billable_usage": list(unknown_usage),
        "lines": [
            {"ref": p.ref, "kind": _POOL_KINDS[type(p.pool)], **asdict(p.pool)} for p in pooled
        ],
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), default=_decimal)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decimal(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"cannot digest {type(value).__name__}")
