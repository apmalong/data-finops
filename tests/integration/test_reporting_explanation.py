"""The Cost Explanation through the `reporting` read model (#110; spec 06 seam 1; GLOSSARY).

The expected chain comes from the golden oracle's existing-fixtures scenario: $6.00 on
Billing Line `…-v1#0` to `q-001`, Evidence `athena:q-001`, the Model Execution
`inv-001`/`model.demo.orders`, then the dbt run, Airflow task and DAG run, nearest first.
"""

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from fixture_database import ACCOUNT, EXPORT, FIXTURES, fill

from data_finops.contracts.billing import CostBasis
from data_finops.reporting import NotFound, Reporting

ORACLE = next(
    s
    for s in json.loads((FIXTURES / "golden" / "scenarios.json").read_text(encoding="utf-8"))
    if s["id"] == "existing-fixtures-complete-coverage"
)
METERED: dict[str, Any] = next(
    a
    for a in ORACLE["allocations"]
    if a["cost_basis"] == "BilledCost" and a["target_id"] == "q-001"
)
RUNS = [ref.removeprefix("openlineage-run:") for ref in METERED["evidence_refs"][2:]]


class Database(NamedTuple):
    path: Path
    run: str


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Database:
    path = tmp_path_factory.mktemp("explanation") / "data-finops.sqlite"
    return Database(path, fill(path))


@pytest.fixture
def reporting(database: Database) -> Iterator[Reporting]:
    with Reporting(database.path) as opened:
        yield opened


def _ref(reporting: Reporting, target_id: str, basis: CostBasis = CostBasis.BILLED_COST) -> str:
    page = reporting.allocations(billing_account_id=ACCOUNT, target_id=target_id, cost_basis=basis)
    (entry,) = page.items
    return entry.ref


def test_a_metered_share_is_explained_from_billing_line_to_workload_runs(
    reporting: Reporting,
) -> None:
    ref = _ref(reporting, "q-001")
    explanation = reporting.explanation(ref)
    assert explanation.ref == ref
    line = explanation.billing_line
    assert (explanation.billing_line_ref, line.BilledCost, line.ServiceName) == (
        METERED["billing_line_ref"],
        Decimal("6.00"),
        "Amazon Athena",
    )
    allocation = explanation.allocation
    assert (allocation.amount, allocation.method, allocation.confidence, allocation.coverage) == (
        Decimal(METERED["amount"]),
        METERED["method"],
        METERED["confidence"],
        Decimal(METERED["coverage"]),
    )
    query = explanation.query_execution
    assert query is not None
    assert (query.query_execution_id, query.account_id, query.region) == (
        "q-001",
        ACCOUNT,
        "us-east-1",
    )
    model = explanation.model_execution
    assert model is not None
    assert (model.invocation_id, model.node_id) == ("inv-001", "model.demo.orders")
    assert model.execution is not None and model.execution.node_id == "model.demo.orders"
    assert model.execution.status
    assert model.dbt_node is not None
    assert (model.dbt_node.node_id, model.dbt_node.resource_type) == ("model.demo.orders", "model")
    assert explanation.unallocated_reason is None
    assert [run.run_id for run in explanation.workload_runs] == RUNS
    # The dbt run and Airflow task are reported; the DAG run is only ever named as their root
    # (fixtures/openlineage/events.json), so the Evidence names a run the store never saw.
    assert [run.run is not None for run in explanation.workload_runs] == [True, True, False]


def test_the_roll_ups_it_contributes_to_are_in_its_currency_and_cost_basis(
    reporting: Reporting,
) -> None:
    explanation = reporting.explanation(_ref(reporting, "q-001", CostBasis.EFFECTIVE_COST))
    assert [(r.level, r.key) for r in explanation.rollups] == [
        ("model_execution", '["inv-001","model.demo.orders"]'),
        *(("workload_run", run) for run in RUNS),
    ]
    assert {(r.currency, r.cost_basis, r.amount) for r in explanation.rollups} == {
        ("USD", CostBasis.EFFECTIVE_COST, Decimal("6.00"))
    }


def test_an_unallocated_amount_is_explained_by_its_reason_alone(reporting: Reporting) -> None:
    explanation = reporting.explanation(_ref(reporting, "unallocated"))
    assert (explanation.allocation.confidence, explanation.unallocated_reason) == (
        None,
        "ineligible",
    )
    assert explanation.billing_line.ServiceName == EXPORT.lines[1].ServiceName
    assert (explanation.query_execution, explanation.model_execution) == (None, None)
    assert (explanation.workload_runs, explanation.rollups) == ([], [])


@pytest.mark.parametrize(
    "ref",
    ["calc-unknown#0", "no-position", "{run}#99", "{run}#-1", "{run}#x", "#0"],
)
def test_an_unknown_or_malformed_reference_is_not_found(
    reporting: Reporting, database: Database, ref: str
) -> None:
    with pytest.raises(NotFound):
        reporting.explanation(ref.format(run=database.run))
