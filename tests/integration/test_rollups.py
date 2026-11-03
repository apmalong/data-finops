"""Roll-ups of a stored Allocation Snapshot (#89; ADR 0016, 0022).

Driven by the hand-written fixtures through a Calculation Run on a temporary SQLite file;
the Roll-ups read only the stored Allocations. The chain: q-001 → Model Execution
(inv-001, model.demo.orders) → invocation run 0002 (dbt-run-demo) → parent 0001 (Airflow
task daily_orders.dbt_run) → parent and root 0000 (the DAG run, named by the
ParentRunFacet; it has no events of its own).
"""

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from data_finops.attribution.rollups import (
    RollupTotal,
    model_execution_rollups,
    workload_run_rollups,
)
from data_finops.billing.focus import load_export
from data_finops.calculation import run_calculation
from data_finops.contracts.allocation import Allocation
from data_finops.contracts.billing import CostBasis
from data_finops.identity.links import ModelExecutionKey
from data_finops.ledger import Ledger
from data_finops.workload.store import WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
EXPORT = load_export(FIXTURES / "focus" / "export-2026-10.json")
EVENTS: list[dict[str, Any]] = json.loads((FIXTURES / "openlineage" / "events.json").read_text())
ACCOUNT, REGION = "123456789012", "us-east-1"
DAG_RUN, TASK_RUN, DBT_RUN, MODEL_RUN = (
    f"00000000-0000-4000-8000-00000000000{n}" for n in range(4)
)
ORDERS = ModelExecutionKey("inv-001", "model.demo.orders")
SIX_DOLLARS = tuple(RollupTotal("USD", basis, Decimal("6.00")) for basis in sorted(CostBasis))
ALL_THREE_RUNS = {DBT_RUN: SIX_DOLLARS, TASK_RUN: SIX_DOLLARS, DAG_RUN: SIX_DOLLARS}


def _calculated(
    tmp_path: Path, events: list[dict[str, Any]] = EVENTS, *, run_results: bool = True
) -> list[Allocation]:
    """Ingest the fixtures (with these events), run a calculation, return its Allocations."""
    events_path = tmp_path / "events.json"
    events_path.write_text(json.dumps(events), encoding="utf-8")
    path = tmp_path / "db.sqlite"
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        ledger.import_snapshot(EXPORT)
        store.ingest_query_history(
            FIXTURES / "athena" / "query-executions.json", account_id=ACCOUNT, region=REGION
        )
        store.ingest_events(events_path)
        if run_results:
            store.ingest_dbt_artifact(FIXTURES / "dbt" / "run_results.json")
        result = run_calculation(ledger, store, EXPORT.snapshot.scope)
        stored = ledger.allocation_snapshot(result.calculation_run_id)
    assert stored is not None
    return stored.allocations


def test_a_model_execution_rolls_up_its_linked_queries(tmp_path: Path) -> None:
    assert model_execution_rollups(_calculated(tmp_path)) == {ORDERS: SIX_DOLLARS}


def test_a_workload_run_rolls_up_through_the_invocation_to_the_airflow_runs(
    tmp_path: Path,
) -> None:
    # The $4 Unallocated S3 line rolls up to nothing. The model run 0003 is the same work
    # as its Model Execution (ADR 0022), so it is not a Roll-up target of its own.
    allocations = _calculated(tmp_path)
    assert workload_run_rollups(allocations) == ALL_THREE_RUNS
    # Recorded once each, nearest first: the parent chain reaches the DAG run, which is
    # also the root.
    assert next(a.evidence_refs for a in allocations if a.target_id == "q-001") == [
        "athena:q-001",
        "dbt-query-comment:inv-001:model.demo.orders",
        f"openlineage-run:{DBT_RUN}",
        f"openlineage-run:{TASK_RUN}",
        f"openlineage-run:{DAG_RUN}",
    ]


def test_a_run_naming_itself_as_root_is_recorded_once(tmp_path: Path) -> None:
    # The dbt run's ParentRunFacet names the task as parent but itself as root.
    events = copy.deepcopy(EVENTS)
    for event in events:
        if event["run"]["runId"] == DBT_RUN and "parent" in event["run"].get("facets", {}):
            event["run"]["facets"]["parent"]["root"]["run"]["runId"] = DBT_RUN
    allocations = _calculated(tmp_path, events=events)
    evidence = next(a.evidence_refs for a in allocations if a.target_id == "q-001")
    assert [r for r in evidence if r.startswith("openlineage-run:")] == [
        f"openlineage-run:{run}" for run in (DBT_RUN, TASK_RUN, DAG_RUN)
    ]


def test_an_unlinked_query_rolls_up_to_nothing(tmp_path: Path) -> None:
    # Without run results, q-001's comment names no reported Model Execution.
    allocations = _calculated(tmp_path, run_results=False)
    assert model_execution_rollups(allocations) == {}
    assert workload_run_rollups(allocations) == {}


def test_an_invocation_no_workload_run_reports_rolls_up_to_no_run(tmp_path: Path) -> None:
    allocations = _calculated(tmp_path, events=[])
    assert model_execution_rollups(allocations) == {ORDERS: SIX_DOLLARS}
    assert workload_run_rollups(allocations) == {}


def test_an_invocation_two_runs_report_is_ambiguous_and_rolls_up_to_no_run(
    tmp_path: Path,
) -> None:
    second = copy.deepcopy(EVENTS[1])  # dbt-run-demo's START, under another run ID
    second["run"]["runId"] = "00000000-0000-4000-8000-000000000009"
    allocations = _calculated(tmp_path, events=[*EVENTS, second])
    assert model_execution_rollups(allocations) == {ORDERS: SIX_DOLLARS}
    assert workload_run_rollups(allocations) == {}


def test_the_root_is_reached_when_a_run_in_between_reported_nothing(tmp_path: Path) -> None:
    # Without the Airflow task's own events, the walk stops at its ID; the dbt run's
    # ParentRunFacet still names the DAG run as root.
    without_task = [e for e in EVENTS if e["run"]["runId"] != TASK_RUN]
    assert workload_run_rollups(_calculated(tmp_path, events=without_task)) == ALL_THREE_RUNS


def test_a_cycle_in_reported_parents_is_walked_once(tmp_path: Path) -> None:
    # Producers can report cycles (WorkloadStore.children): the task names the dbt run as
    # its parent, and the dbt run names the task. The root is still the DAG run.
    cyclic = copy.deepcopy(EVENTS)
    cyclic[0]["run"]["facets"]["parent"]["run"]["runId"] = DBT_RUN
    allocations = _calculated(tmp_path, events=cyclic)
    assert workload_run_rollups(allocations) == ALL_THREE_RUNS
    evidence = next(a.evidence_refs for a in allocations if a.target_id == "q-001")
    assert len(evidence) == len(set(evidence))  # each run recorded once
