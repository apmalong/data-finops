"""M4 exit criterion: all accounting invariants and negative adjustment tests pass; tests
call the engine and match the golden scenarios (#90; plan, M4).

The `existing-fixtures` scenario runs end to end: every hand-written fixture is ingested
through the public interfaces, a Calculation Run allocates the stored Export Snapshot, and
the stored Allocation Snapshot is compared with the oracle and rolled up. The synthetic
and negative-adjustment scenarios have no fixture files: their lines are built from the
oracle's own `billing_lines` and given to the engine directly, as callers classify them.
Every scenario's Allocations are held to the accounting invariants.
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
from data_finops.contracts.allocation import Allocation, AllocationCategory, Confidence
from data_finops.contracts.billing import CostBasis, FocusBillingLine, FocusChargeCategory
from data_finops.identity.links import ModelExecutionKey
from data_finops.ledger import AllocationStoreOutcome, Ledger
from data_finops.reporting import Reporting
from data_finops.workload.store import WorkloadStore

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
SCENARIOS = {
    s["id"]: s
    for s in json.loads((FIXTURES / "golden" / "scenarios.json").read_text(encoding="utf-8"))
}
SCHEMA = Draft202012Validator(
    json.loads((ROOT / "schemas" / "allocation.schema.json").read_text(encoding="utf-8")),
    format_checker=Draft202012Validator.FORMAT_CHECKER,
)
EXPORT = load_export(FIXTURES / "focus" / "export-2026-10.json")
ACCOUNT, REGION = "123456789012", "us-east-1"
DAG_RUN = "00000000-0000-4000-8000-000000000000"
AIRFLOW_TASK = "00000000-0000-4000-8000-000000000001"
DBT_RUN = "00000000-0000-4000-8000-000000000002"


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


def _assert_invariants(allocations: list[Allocation], lines: dict[str, FocusBillingLine]) -> None:
    """Every line partitioned exactly, in its own currency and sign, for every Cost Basis;
    every Allocation valid against the published schema; no Confidence on Unallocated."""
    allocated: dict[tuple[str, CostBasis], Decimal] = defaultdict(Decimal)
    for allocation in allocations:
        SCHEMA.validate(allocation.model_dump(mode="json"))
        line = lines[allocation.billing_line_ref]
        assert allocation.currency == line.BillingCurrency
        assert allocation.amount * line.cost(CostBasis(allocation.cost_basis)) >= 0  # sign kept
        if allocation.category == AllocationCategory.UNALLOCATED:
            assert allocation.confidence is None
        allocated[(allocation.billing_line_ref, CostBasis(allocation.cost_basis))] += (
            allocation.amount
        )
    assert allocated == {
        (ref, basis): line.cost(basis) for ref, line in lines.items() for basis in CostBasis
    }


def _oracle_lines(scenario_id: str, category: FocusChargeCategory) -> dict[str, FocusBillingLine]:
    """The scenario's billing lines: the fixture's Athena line carrying each oracle amount
    and currency in every cost column."""
    return {
        line["ref"]: FocusBillingLine.model_validate(
            EXPORT.lines[0].model_dump()
            | {basis.value: Decimal(line["amount"]) for basis in CostBasis}
            | {"BillingCurrency": line["currency"], "ChargeCategory": category}
        )
        for line in SCENARIOS[scenario_id]["billing_lines"]
    }


def _engine(lines: dict[str, FocusBillingLine], pools: dict[str, Any]) -> list[Allocation]:
    pooled = [PooledLine(ref, line, pools[ref]) for ref, line in lines.items()]
    return allocate(pooled, policy_version="golden-v1", calculation_run_id="golden")


@pytest.fixture(scope="module")
def end_to_end_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
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
    return path


@pytest.fixture(scope="module")
def end_to_end(end_to_end_database: Path) -> Iterator[list[Allocation]]:
    with Ledger(end_to_end_database) as ledger:
        stored = ledger.current_allocations(EXPORT.snapshot.scope)
        assert stored is not None
        yield stored.allocations


def test_the_existing_fixtures_scenario_runs_end_to_end(end_to_end: list[Allocation]) -> None:
    assert _comparable(end_to_end) == _oracle("existing-fixtures-complete-coverage")
    _assert_invariants(
        end_to_end, {EXPORT.line_ref(i): line for i, line in enumerate(EXPORT.lines)}
    )


def test_the_end_to_end_run_reconciles_every_line_through_the_read_model(
    end_to_end_database: Path,
) -> None:
    # The same partition check as `_assert_invariants`, made by `reporting` (#112) over the
    # stored snapshot and the Export Snapshot its run read.
    with Reporting(end_to_end_database) as reporting:
        report = reporting.reconciliation(billing_account_id=ACCOUNT)
    assert report.total == len(EXPORT.lines) * len(CostBasis)
    assert all(line.delta == 0 for line in report.lines)
    assert all(total.delta == 0 for total in report.totals)


def test_the_synthetic_cost_pools_scenario_is_computed_by_the_engine() -> None:
    lines = _oracle_lines("explicit-synthetic-cost-pools", FocusChargeCategory.USAGE)
    allocations = _engine(
        lines,
        {
            "synthetic#0": DirectCostPool(
                "synthetic-direct",
                "q-synthetic-orders",
                Confidence.DIRECT_VERIFIED,
                ("explicit-demo-price-001",),
            ),
            "synthetic#1": SharedCostPool(
                "synthetic-shared",
                (
                    SharedWeight("q-synthetic-orders", Decimal(5)),
                    SharedWeight("q-synthetic-customers", Decimal(3)),
                ),
                Confidence.METERED_ESTIMATE,
                ("synthetic-weight-001",),
            ),
            "synthetic#2": IdleCostPool("synthetic-idle"),
            "synthetic#3": SharedCostPool("synthetic-unknown", (), Confidence.METERED_ESTIMATE),
        },
    )
    assert _comparable(allocations) == _oracle("explicit-synthetic-cost-pools")
    _assert_invariants(allocations, lines)


def test_the_negative_adjustment_scenario_is_computed_by_the_engine() -> None:
    lines = _oracle_lines("negative-adjustment", FocusChargeCategory.CREDIT)
    allocations = _engine(lines, {"credit#0": Adjustment("adjustments")})
    assert _comparable(allocations) == _oracle("negative-adjustment")
    _assert_invariants(allocations, lines)  # -1.00 in every Cost Basis, never clamped


def test_the_allocations_roll_up_to_the_model_and_the_airflow_runs(
    end_to_end: list[Allocation],
) -> None:
    six = tuple(RollupTotal("USD", basis, Decimal("6.00")) for basis in sorted(CostBasis))
    assert model_execution_rollups(end_to_end) == {
        ModelExecutionKey("inv-001", "model.demo.orders"): six
    }
    assert workload_run_rollups(end_to_end) == {DBT_RUN: six, AIRFLOW_TASK: six, DAG_RUN: six}
