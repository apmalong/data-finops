"""Listing Allocations through the `reporting` read model (#109; spec 06 seam 1).

A page serves one Allocation Snapshot: the current one of the single Snapshot Scope the
filters select, or a superseded one named by its Calculation Run. Amounts come from the
golden oracle's existing-fixtures scenario ($6.00 to `q-001`, $4.00 Unallocated) and, for the
restatement, from the restated line itself ($5.00).
"""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import pytest
from fixture_database import ACCOUNT, EXPORT, fill, restate

from data_finops.contracts.allocation import AllocationCategory
from data_finops.contracts.billing import CostBasis
from data_finops.ledger import Ledger
from data_finops.reporting import AmbiguousScope, NotFound, Reporting


class Database(NamedTuple):
    path: Path
    first_run: str
    current_run: str


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Database:
    path = tmp_path_factory.mktemp("allocations") / "data-finops.sqlite"
    first = fill(path)
    return Database(path, first, restate(path))


@pytest.fixture
def reporting(database: Database) -> Iterator[Reporting]:
    with Reporting(database.path) as opened:
        yield opened


def _billed(page_items: list) -> list[tuple[str, Decimal]]:  # type: ignore[type-arg]
    return sorted(
        (item.allocation.target_id, item.allocation.amount)
        for item in page_items
        if item.allocation.cost_basis == CostBasis.BILLED_COST
    )


def test_the_current_snapshot_is_listed_with_each_allocation_addressed(
    reporting: Reporting, database: Database
) -> None:
    page = reporting.allocations()
    assert (page.calculation_run_id, page.total, page.limit, page.offset) == (
        database.current_run,
        8,  # two lines, one Allocation Target each, once per Cost Basis
        100,
        0,
    )
    assert _billed(page.items) == [("q-001", Decimal("6.00")), ("unallocated", Decimal("5.00"))]
    assert [item.ref for item in page.items[:2]] == [
        f"{database.current_run}#0",
        f"{database.current_run}#1",
    ]


def test_a_superseded_snapshot_is_listed_by_its_calculation_run(
    reporting: Reporting, database: Database
) -> None:
    page = reporting.allocations(calculation_run_id=database.first_run)
    assert page.calculation_run_id == database.first_run
    assert _billed(page.items) == [("q-001", Decimal("6.00")), ("unallocated", Decimal("4.00"))]


def test_an_unknown_calculation_run_is_not_found(reporting: Reporting) -> None:
    with pytest.raises(NotFound):
        reporting.allocations(calculation_run_id="calc-unknown")


@pytest.mark.parametrize(
    ("filters", "targets"),
    [
        ({"target_id": "q-001"}, {"q-001"}),
        ({"category": AllocationCategory.UNALLOCATED}, {"unallocated"}),
        ({"billing_line_ref": "aws-focus-123456789012-2026-10-v2#1"}, {"unallocated"}),
    ],
)
def test_filters_narrow_the_page(
    reporting: Reporting, filters: dict[str, object], targets: set[str]
) -> None:
    page = reporting.allocations(**filters)  # type: ignore[arg-type]
    assert {item.allocation.target_id for item in page.items} == targets
    assert page.total == len(page.items) == 4  # one Allocation per Cost Basis


def test_a_cost_basis_filter_keeps_that_column(reporting: Reporting) -> None:
    page = reporting.allocations(cost_basis=CostBasis.EFFECTIVE_COST)
    assert page.total == 2
    assert {item.allocation.cost_basis for item in page.items} == {"EffectiveCost"}


def test_pages_are_bounded_and_stable(reporting: Reporting) -> None:
    everything = reporting.allocations().items
    first = reporting.allocations(limit=5, offset=0)
    rest = reporting.allocations(limit=1000, offset=5)
    assert (len(first.items), first.total) == (5, 8)
    assert first.items + rest.items == everything
    assert reporting.allocations(offset=8).items == []


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (1001, 0), (10, -1)])
def test_limits_out_of_range_are_refused(reporting: Reporting, limit: int, offset: int) -> None:
    with pytest.raises(ValueError):
        reporting.allocations(limit=limit, offset=offset)


def test_scope_filters_select_the_snapshot(reporting: Reporting, database: Database) -> None:
    page = reporting.allocations(billing_account_id=ACCOUNT, billing_period=date(2026, 10, 1))
    assert page.calculation_run_id == database.current_run
    with pytest.raises(NotFound):
        reporting.allocations(billing_account_id="999999999999")


def test_several_matching_scopes_must_be_narrowed(tmp_path: Path) -> None:
    path = tmp_path / "data-finops.sqlite"
    fill(path)
    other = EXPORT.model_copy(
        update={
            "snapshot": EXPORT.snapshot.model_copy(
                update={"snapshot_id": "other-v1", "billing_account_id": "210987654321"}
            )
        }
    )
    with Ledger(path) as ledger:
        ledger.import_snapshot(other)
        ledger.store_allocations(
            other.snapshot.scope,
            calculation_run_id="calc-other",
            policy_version="test",
            input_digest="other",
            export_snapshot_id="other-v1",
            allocations=[],
        )
    with Reporting(path) as reporting:
        with pytest.raises(AmbiguousScope):
            reporting.allocations()
        assert reporting.allocations(billing_account_id=ACCOUNT).total == 8


def test_the_ledger_lists_a_scopes_allocation_snapshots_oldest_first(
    database: Database,
) -> None:
    with Ledger(database.path) as ledger:
        snapshots = ledger.allocation_snapshots(EXPORT.snapshot.scope)
    assert [(s.calculation_run_id, s.is_current) for s in snapshots] == [
        (database.first_run, False),
        (database.current_run, True),
    ]
    assert [s.export_snapshot_id for s in snapshots] == [
        EXPORT.snapshot.snapshot_id,
        "aws-focus-123456789012-2026-10-v2",
    ]
