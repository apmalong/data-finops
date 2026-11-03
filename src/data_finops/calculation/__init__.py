"""The Calculation Run (spec 03 steps B, E and H; M4 structure 2 and 4).

A thin orchestrator: reads the current Export Snapshot for a Snapshot Scope from the
Ledger and the Query Executions from the workload store, classifies each Billing Line
with the Athena policies, runs the pure engine and stores the Allocation Snapshot. It
lives outside `attribution` because the engine may not import the Ledger, the workload
store or provider policies (architecture-boundaries.md).
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from uuid import uuid4

from data_finops.attribution.engine import (
    ENGINE_VERSION,
    Adjustment,
    Ineligible,
    MeteredCostPool,
    MeteredUsage,
    PooledLine,
    allocate,
)
from data_finops.contracts.billing import (
    FocusBillingLine,
    FocusChargeCategory,
    SnapshotScope,
)
from data_finops.contracts.workload import LinkOutcome, QueryExecution
from data_finops.ledger import AllocationStoreOutcome, Ledger
from data_finops.providers import athena_billing
from data_finops.workload.store import WorkloadStore

POLICY_VERSION = "+".join(
    (ENGINE_VERSION, athena_billing.POLICY_VERSION, athena_billing.SCOPE_POLICY_VERSION)
)
"""Every rule that shapes an Allocation: the engine's and both Athena policies."""

_ADJUSTMENTS = frozenset({FocusChargeCategory.CREDIT, FocusChargeCategory.ADJUSTMENT})


@dataclass(frozen=True)
class CalculationResult:
    outcome: AllocationStoreOutcome
    calculation_run_id: str
    """The current snapshot: this run's, or on a replay the one it reproduced."""
    input_digest: str
    unknown_billable_usage: tuple[str, ...]
    """Query Executions in a metered line's scope that reported no scan: left out of
    known usage, so their line's Coverage falls below one (never guessed)."""


def run_calculation(
    ledger: Ledger,
    store: WorkloadStore,
    scope: SnapshotScope,
    *,
    new_run_id: Callable[[], str] = lambda: f"calc-{uuid4()}",
) -> CalculationResult:
    """Allocate the scope's current Export Snapshot and store it as an Allocation Snapshot.

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
    digest = _input_digest(pooled)
    run_id = new_run_id()
    stored = ledger.store_allocations(
        scope,
        calculation_run_id=run_id,
        policy_version=POLICY_VERSION,
        input_digest=digest,
        allocations=allocate(pooled, policy_version=POLICY_VERSION, calculation_run_id=run_id),
    )
    return CalculationResult(
        stored.outcome, stored.calculation_run_id, digest, tuple(sorted(unknown))
    )


def _pool(
    line: FocusBillingLine, store: WorkloadStore, unknown: set[str]
) -> MeteredCostPool | Ineligible | Adjustment:
    """Classify a line (spec 03 step B): Credit and Adjustment lines are Adjustments;
    Athena on-demand usage is metered; everything else, tax, purchases and corrections
    included, is Ineligible."""
    day = line.ChargePeriodStart.date().isoformat()
    if line.ChargeCategory in _ADJUSTMENTS:
        return Adjustment(f"adjustment:{line.BillingAccountId}:{day}")
    unit, account, region = line.ConsumedUnit, line.SubAccountId, line.RegionId
    if not (athena_billing.is_on_demand_usage(line) and unit and account and region):
        return Ineligible(f"ineligible:{line.BillingAccountId}:{day}")
    usages = []
    for query in store.query_executions(account, region):
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
    """The query itself, and its Trusted Query Comment when that links a Model Execution."""
    refs = [f"athena:{query.query_execution_id}"]
    link = store.model_execution_of_query(query.account_id, query.region, query.query_execution_id)
    if link is not None and link.outcome == LinkOutcome.LINKED and query.correlation:
        refs.append(
            f"dbt-query-comment:{query.correlation.invocation_id}:{query.correlation.node_id}"
        )
    return tuple(refs)


def _input_digest(pooled: list[PooledLine]) -> str:
    """SHA-256 over what the engine is given: each line's reference (which names its
    Export Snapshot, whose lines are immutable) and pool with every Query Execution's
    Billable Usage and Evidence, and the policy version. Anything that would change an
    Allocation changes the digest; nothing else does."""
    document = {
        "policy_version": POLICY_VERSION,
        "lines": [{"ref": p.ref, "pool": type(p.pool).__name__, **asdict(p.pool)} for p in pooled],
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), default=_decimal)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decimal(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"cannot digest {type(value).__name__}")
