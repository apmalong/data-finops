"""Roll-ups over a stored Allocation Snapshot and the workload store (#89; ADR 0016, 0022).

Driven by the hand-written fixtures through a Calculation Run on a temporary SQLite file.
The chain: q-001 → Model Execution (inv-001, model.demo.orders) → invocation run 0002
(dbt-run-demo) → parent 0001 (Airflow task daily_orders.dbt_run) → parent 0000 (the DAG
run, named by the ParentRunFacet; it has no events of its own).
"""

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from data_finops.attribution.rollups import (
    ModelExecutionRef,
    RollupTotal,
    model_execution_rollups,
)
from data_finops.billing.focus import load_export
from data_finops.calculation import run_calculation, workload_run_rollups
from data_finops.contracts.allocation import Allocation
from data_finops.contracts.billing import CostBasis
from data_finops.ledger import Ledger
from data_finops.workload.store import WorkloadStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
EXPORT = load_export(FIXTURES / "focus" / "export-2026-10.json")
SCOPE = EXPORT.snapshot.scope
ACCOUNT, REGION = "123456789012", "us-east-1"
DAG_RUN, TASK_RUN, DBT_RUN, MODEL_RUN = (
    f"00000000-0000-4000-8000-00000000000{n}" for n in range(4)
)
SIX_DOLLARS = tuple(RollupTotal("USD", basis, Decimal("6.00")) for basis in sorted(CostBasis))


def _open(path: Path, *, events: Path, run_results: bool = True) -> tuple[Ledger, WorkloadStore]:
    ledger, store = Ledger(path), WorkloadStore(path)
    ledger.import_snapshot(EXPORT)
    store.ingest_query_history(
        FIXTURES / "athena" / "query-executions.json", account_id=ACCOUNT, region=REGION
    )
    store.ingest_events(events)
    if run_results:
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "run_results.json")
    return ledger, store


@pytest.fixture
def calculated(tmp_path: Path) -> Iterator[tuple[list[Allocation], WorkloadStore]]:
    ledger, store = _open(tmp_path / "db.sqlite", events=FIXTURES / "openlineage" / "events.json")
    with ledger, store:
        result = run_calculation(ledger, store, SCOPE)
        stored = ledger.allocation_snapshot(result.calculation_run_id)
        assert stored is not None
        yield stored.allocations, store


def test_a_model_execution_rolls_up_its_linked_queries_from_the_stored_snapshot(
    calculated: tuple[list[Allocation], WorkloadStore],
) -> None:
    allocations, _ = calculated
    assert model_execution_rollups(allocations) == {
        ModelExecutionRef("inv-001", "model.demo.orders"): SIX_DOLLARS
    }


def test_a_workload_run_rolls_up_through_the_invocation_to_the_airflow_runs(
    calculated: tuple[list[Allocation], WorkloadStore],
) -> None:
    allocations, store = calculated
    # The $4 Unallocated S3 line rolls up to nothing. The model run 0003 is the same work
    # as its Model Execution (ADR 0022), so it is not a Roll-up target of its own.
    assert workload_run_rollups(allocations, store) == {
        DBT_RUN: SIX_DOLLARS,
        TASK_RUN: SIX_DOLLARS,
        DAG_RUN: SIX_DOLLARS,
    }


def test_an_unlinked_query_rolls_up_to_nothing(tmp_path: Path) -> None:
    # Without run results, q-001's comment names no reported Model Execution.
    ledger, store = _open(
        tmp_path / "db.sqlite", events=FIXTURES / "openlineage" / "events.json", run_results=False
    )
    with ledger, store:
        result = run_calculation(ledger, store, SCOPE)
        stored = ledger.allocation_snapshot(result.calculation_run_id)
        assert stored is not None
        assert model_execution_rollups(stored.allocations) == {}
        assert workload_run_rollups(stored.allocations, store) == {}


def test_a_cycle_in_reported_parents_is_walked_once(tmp_path: Path) -> None:
    # Producers can report cycles (WorkloadStore.children): the task names the dbt run as
    # its parent, and the dbt run names the task.
    events = json.loads((FIXTURES / "openlineage" / "events.json").read_text())
    events[0]["run"]["facets"]["parent"]["run"]["runId"] = DBT_RUN
    cyclic = tmp_path / "cyclic-events.json"
    cyclic.write_text(json.dumps(events), encoding="utf-8")
    ledger, store = _open(tmp_path / "db.sqlite", events=cyclic)
    with ledger, store:
        result = run_calculation(ledger, store, SCOPE)
        stored = ledger.allocation_snapshot(result.calculation_run_id)
        assert stored is not None
        assert workload_run_rollups(stored.allocations, store) == {
            DBT_RUN: SIX_DOLLARS,
            TASK_RUN: SIX_DOLLARS,
        }


def test_an_invocation_no_workload_run_reports_rolls_up_to_no_run(tmp_path: Path) -> None:
    # Linked Model Execution, but no OpenLineage events: the invocation is unmatched.
    no_events = tmp_path / "no-events.json"
    no_events.write_text("[]", encoding="utf-8")
    ledger, store = _open(tmp_path / "db.sqlite", events=no_events)
    with ledger, store:
        result = run_calculation(ledger, store, SCOPE)
        stored = ledger.allocation_snapshot(result.calculation_run_id)
        assert stored is not None
        assert model_execution_rollups(stored.allocations) == {
            ModelExecutionRef("inv-001", "model.demo.orders"): SIX_DOLLARS
        }
        assert workload_run_rollups(stored.allocations, store) == {}
