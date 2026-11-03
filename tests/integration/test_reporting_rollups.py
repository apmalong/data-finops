"""Roll-ups through the `reporting` read model (#111; spec 06 seam 1; ADR 0016).

Expected amounts come from the golden oracle's existing-fixtures scenario: $6.00 to `q-001`,
whose Evidence links the Model Execution `inv-001`/`model.demo.orders` and three Workload
Runs; the $4.00 Unallocated rolls up to nothing.
"""

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from fixture_database import (
    ACCOUNT,
    EXPORT,
    FIXTURES,
    OTHER_ACCOUNT,
    add_other_account,
    fill,
    restate,
)

from data_finops.contracts.billing import CostBasis
from data_finops.ledger import Ledger
from data_finops.reporting import (
    AmbiguousScope,
    ConflictingFilters,
    NotFound,
    Reporting,
    RollupAmount,
)

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
RUNS = sorted(ref.removeprefix("openlineage-run:") for ref in METERED["evidence_refs"][2:])
ORDERS = '["inv-001","model.demo.orders"]'
EVERY_BASIS = {("USD", basis, Decimal(METERED["amount"])) for basis in CostBasis}


class Database(NamedTuple):
    path: Path
    first_run: str


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Database:
    path = tmp_path_factory.mktemp("rollups") / "data-finops.sqlite"
    first = fill(path)
    restate(path)
    add_other_account(path)
    return Database(path, first)


@pytest.fixture
def reporting(database: Database) -> Iterator[Reporting]:
    with Reporting(database.path) as opened:
        yield opened


def _amounts(totals: list[RollupAmount]) -> set[tuple[str, CostBasis, Decimal]]:
    return {(t.currency, t.cost_basis, t.amount) for t in totals}


def test_a_model_execution_rolls_up_its_linked_query_per_currency_and_basis(
    reporting: Reporting,
) -> None:
    page = reporting.rollups(level="model_execution", billing_account_id=ACCOUNT)
    assert (page.level, page.total, [item.key for item in page.items]) == (
        "model_execution",
        1,
        [ORDERS],
    )
    assert _amounts(page.items[0].totals) == EVERY_BASIS


def test_each_workload_run_named_by_the_evidence_rolls_up_the_same_amount(
    reporting: Reporting,
) -> None:
    page = reporting.rollups(level="workload_run", billing_account_id=ACCOUNT)
    assert [item.key for item in page.items] == RUNS
    assert all(_amounts(item.totals) == EVERY_BASIS for item in page.items)


def test_a_superseded_snapshot_rolls_up_by_its_calculation_run(
    reporting: Reporting, database: Database
) -> None:
    page = reporting.rollups(level="model_execution", calculation_run_id=database.first_run)
    assert page.calculation_run_id == database.first_run
    assert [item.key for item in page.items] == [ORDERS]


def test_a_scope_with_nothing_linked_has_no_rollups(reporting: Reporting) -> None:
    page = reporting.rollups(level="workload_run", billing_account_id=OTHER_ACCOUNT)
    assert (page.total, page.items) == (0, [])


def test_pages_are_bounded(reporting: Reporting) -> None:
    first = reporting.rollups(level="workload_run", billing_account_id=ACCOUNT, limit=2)
    rest = reporting.rollups(level="workload_run", billing_account_id=ACCOUNT, offset=2)
    assert ([i.key for i in first.items + rest.items], first.total) == (RUNS, 3)
    with pytest.raises(ValueError):
        reporting.rollups(level="workload_run", billing_account_id=ACCOUNT, limit=0)


def test_snapshot_selection_is_refused_as_for_allocations(
    reporting: Reporting, database: Database
) -> None:
    with pytest.raises(AmbiguousScope):
        reporting.rollups(level="model_execution")
    with pytest.raises(NotFound):
        reporting.rollups(level="model_execution", calculation_run_id="calc-unknown")
    with pytest.raises(ConflictingFilters):
        reporting.rollups(
            level="model_execution",
            calculation_run_id=database.first_run,
            billing_account_id=ACCOUNT,
        )


def test_an_unknown_level_is_refused(reporting: Reporting) -> None:
    with pytest.raises(ValueError):
        reporting.rollups(level="data_product", billing_account_id=ACCOUNT)  # type: ignore[arg-type]


def test_an_unlinked_query_executions_allocation_rolls_up_to_nothing(tmp_path: Path) -> None:
    # The fixture's one metered query is linked, so its Allocations are stored again with only
    # the query's own Evidence, as the engine records an unlinked Query Execution.
    path = tmp_path / "data-finops.sqlite"
    first = fill(path)
    with Ledger(path) as ledger:
        stored = ledger.allocation_snapshot(first)
        assert stored is not None
        ledger.store_allocations(
            EXPORT.snapshot.scope,
            calculation_run_id="calc-unlinked",
            policy_version=stored.snapshot.policy_version,
            input_digest="unlinked",
            export_snapshot_id=EXPORT.snapshot.snapshot_id,
            allocations=[
                a.model_copy(
                    update={
                        "calculation_run_id": "calc-unlinked",
                        "evidence_refs": a.evidence_refs[:1],
                    }
                )
                for a in stored.allocations
            ],
        )
    with Reporting(path) as reporting:
        for level in ("model_execution", "workload_run"):
            page = reporting.rollups(level=level, billing_account_id=ACCOUNT)  # type: ignore[arg-type]
            assert (page.calculation_run_id, page.items) == ("calc-unlinked", [])
