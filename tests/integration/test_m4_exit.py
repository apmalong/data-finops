"""M4 exit criterion: all accounting invariants and negative adjustment tests pass; tests
call the engine and match the golden scenarios (#90; plan, M4).

The `existing-fixtures` scenario runs end to end: every hand-written fixture is ingested
through the public interfaces, a Calculation Run allocates the stored Export Snapshot, and
the stored Allocation Snapshot is compared with the oracle and rolled up. The synthetic
and negative-adjustment scenarios have no fixture files, so their lines are built here
and given to the engine directly, as callers classify them.
"""

import json
from collections import defaultdict
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from data_finops.attribution.engine import (
    Adjustment,
    DirectCostPool,
    IdleCostPool,
    PooledLine,
    SharedCostPool,
    SharedWeight,
    allocate,
)
from data_finops.attribution.rollups import (
    RollupTotal,
    model_execution_rollups,
    workload_run_rollups,
)
from data_finops.billing.focus import load_export
from data_finops.calculation import run_calculation
from data_finops.contracts.allocation import Allocation, Confidence
from data_finops.contracts.billing import CostBasis, FocusBillingLine, FocusChargeCategory
from data_finops.identity.links import ModelExecutionKey
from data_finops.ledger import AllocationStoreOutcome, Ledger
from data_finops.workload.store import WorkloadStore

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
SCENARIOS = {s["id"]: s for s in json.loads((FIXTURES / "golden" / "scenarios.json").read_text())}
SCHEMA = Draft202012Validator(
    json.loads((ROOT / "schemas" / "allocation.schema.json").read_text()),
    format_checker=Draft202012Validator.FORMAT_CHECKER,
)
EXPORT = load_export(FIXTURES / "focus" / "export-2026-10.json")
ACCOUNT, REGION = "123456789012", "us-east-1"
DAG_RUN, TASK_RUN, DBT_RUN = (f"00000000-0000-4000-8000-00000000000{n}" for n in range(3))


def _comparable(allocations: list[Allocation]) -> list[dict[str, Any]]:
    """BilledCost Allocations (what the oracle states) without run and policy identifiers."""
    return sorted(
        (
            a.model_dump(mode="json", exclude={"calculation_run_id", "policy_version"})
            for a in allocations
            if a.cost_basis == CostBasis.BILLED_COST
        ),
        key=lambda a: (a["billing_line_ref"], a["target_id"]),
    )


def _oracle(scenario_id: str) -> list[dict[str, Any]]:
    return _comparable(
        [Allocation.model_validate(a) for a in SCENARIOS[scenario_id]["allocations"]]
    )


@pytest.fixture(scope="module")
def end_to_end(tmp_path_factory: pytest.TempPathFactory) -> Iterator[list[Allocation]]:
    path = tmp_path_factory.mktemp("m4") / "data-finops.sqlite"
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        ledger.import_snapshot(EXPORT)
        for name in ("query-executions.json", "link-outcomes.json", "link-outcomes-restated.json"):
            store.ingest_query_history(
                FIXTURES / "athena" / name, account_id=ACCOUNT, region=REGION
            )
        store.ingest_events(FIXTURES / "openlineage" / "events.json")
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "manifest.json")
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "run_results.json")
        first = run_calculation(ledger, store, EXPORT.snapshot.scope)
        again = run_calculation(ledger, store, EXPORT.snapshot.scope)
        assert (first.outcome, again.outcome) == (
            AllocationStoreOutcome.NEW,
            AllocationStoreOutcome.REPLAY,
        )
        stored = ledger.current_allocations(EXPORT.snapshot.scope)
        assert stored is not None and stored.snapshot.calculation_run_id == first.calculation_run_id
        yield stored.allocations


def _line(ref: str, amount: str, category: FocusChargeCategory) -> PooledLine:
    """A synthetic line: the fixture's Athena line with `amount` in every cost column."""
    line = FocusBillingLine.model_validate(
        EXPORT.lines[0].model_dump()
        | {basis.value: Decimal(amount) for basis in CostBasis}
        | {"ChargeCategory": category}
    )
    return PooledLine(ref, line, IdleCostPool("unused"))


def _engine(lines: list[PooledLine]) -> list[Allocation]:
    return allocate(lines, policy_version="golden-v1", calculation_run_id="golden")


def test_the_existing_fixtures_scenario_runs_end_to_end(end_to_end: list[Allocation]) -> None:
    assert _comparable(end_to_end) == _oracle("existing-fixtures-complete-coverage")


def test_the_synthetic_cost_pools_scenario_is_computed_by_the_engine() -> None:
    usage = FocusChargeCategory.USAGE
    lines = [
        _line("synthetic#0", "3.00", usage),
        _line("synthetic#1", "2.00", usage),
        _line("synthetic#2", "1.00", usage),
        _line("synthetic#3", "4.00", usage),
    ]
    pools = [
        DirectCostPool(
            "synthetic-direct",
            "q-synthetic-orders",
            Confidence.DIRECT_VERIFIED,
            ("explicit-demo-price-001",),
        ),
        SharedCostPool(
            "synthetic-shared",
            (
                SharedWeight("q-synthetic-orders", Decimal(5)),
                SharedWeight("q-synthetic-customers", Decimal(3)),
            ),
            Confidence.METERED_ESTIMATE,
            ("synthetic-weight-001",),
        ),
        IdleCostPool("synthetic-idle"),
        SharedCostPool("synthetic-unknown", (), Confidence.METERED_ESTIMATE),
    ]
    pooled = [
        PooledLine(line.ref, line.line, pool) for line, pool in zip(lines, pools, strict=True)
    ]
    assert _comparable(_engine(pooled)) == _oracle("explicit-synthetic-cost-pools")


def test_the_negative_adjustment_scenario_is_computed_by_the_engine() -> None:
    credit = _line("credit#0", "-1.00", FocusChargeCategory.CREDIT)
    pooled = [PooledLine(credit.ref, credit.line, Adjustment("adjustments"))]
    assert _comparable(_engine(pooled)) == _oracle("negative-adjustment")


def test_the_accounting_invariants_hold_on_the_stored_snapshot(
    end_to_end: list[Allocation],
) -> None:
    # Every Billing Line is partitioned exactly, in its own currency, for every Cost Basis.
    allocated: dict[tuple[str, CostBasis], Decimal] = defaultdict(Decimal)
    for allocation in end_to_end:
        SCHEMA.validate(allocation.model_dump(mode="json"))
        position = int(allocation.billing_line_ref.rsplit("#", 1)[1])
        assert allocation.currency == EXPORT.lines[position].BillingCurrency
        allocated[(allocation.billing_line_ref, CostBasis(allocation.cost_basis))] += (
            allocation.amount
        )
        if allocation.category == "unallocated":
            assert allocation.confidence is None
    assert allocated == {
        (EXPORT.line_ref(position), basis): line.cost(basis)
        for position, line in enumerate(EXPORT.lines)
        for basis in CostBasis
    }


def test_the_allocations_roll_up_to_the_model_and_the_airflow_runs(
    end_to_end: list[Allocation],
) -> None:
    six = tuple(RollupTotal("USD", basis, Decimal("6.00")) for basis in sorted(CostBasis))
    assert model_execution_rollups(end_to_end) == {
        ModelExecutionKey("inv-001", "model.demo.orders"): six
    }
    assert workload_run_rollups(end_to_end) == {DBT_RUN: six, TASK_RUN: six, DAG_RUN: six}
