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
from fixture_database import ACCOUNT, EXPORT, OTHER_ACCOUNT, add_other_account, fill, restate

from data_finops.contracts.allocation import AllocationCategory
from data_finops.contracts.billing import CostBasis
from data_finops.reporting import (
    AllocationEntry,
    AmbiguousScope,
    ConflictingFilters,
    NotFound,
    Reporting,
)


class Database(NamedTuple):
    path: Path
    first_run: str
    current_run: str
    other_run: str


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Database:
    path = tmp_path_factory.mktemp("allocations") / "data-finops.sqlite"
    first = fill(path)
    current = restate(path)
    return Database(path, first, current, add_other_account(path))


@pytest.fixture
def reporting(database: Database) -> Iterator[Reporting]:
    with Reporting(database.path) as opened:
        yield opened


def _billed(items: list[AllocationEntry]) -> list[tuple[str, Decimal]]:
    return sorted(
        (item.allocation.target_id, item.allocation.amount)
        for item in items
        if item.allocation.cost_basis == CostBasis.BILLED_COST
    )


def test_the_current_snapshot_is_listed_with_each_allocation_addressed(
    reporting: Reporting, database: Database
) -> None:
    page = reporting.allocations(billing_account_id=ACCOUNT)
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


def test_a_named_run_cannot_be_combined_with_scope_filters(
    reporting: Reporting, database: Database
) -> None:
    for scope_filter in ({"billing_account_id": ACCOUNT}, {"billing_period": date(2026, 10, 1)}):
        with pytest.raises(ConflictingFilters):
            reporting.allocations(calculation_run_id=database.first_run, **scope_filter)  # type: ignore[arg-type]


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
    page = reporting.allocations(billing_account_id=ACCOUNT, **filters)  # type: ignore[arg-type]
    assert {item.allocation.target_id for item in page.items} == targets
    assert page.total == len(page.items) == 4  # one Allocation per Cost Basis


def test_a_cost_basis_filter_keeps_that_column(reporting: Reporting) -> None:
    page = reporting.allocations(billing_account_id=ACCOUNT, cost_basis=CostBasis.EFFECTIVE_COST)
    assert page.total == 2
    assert {item.allocation.cost_basis for item in page.items} == {"EffectiveCost"}


def test_each_allocation_keeps_its_own_currency(reporting: Reporting) -> None:
    page = reporting.allocations(billing_account_id=OTHER_ACCOUNT)
    by_line = {(item.allocation.billing_line_ref, item.allocation.currency) for item in page.items}
    assert by_line == {
        ("other-account-v1#0", "USD"),
        ("other-account-v1#1", "EUR"),
        ("other-account-v1#2", "EUR"),
    }
    assert page.total == 12  # three lines, once per Cost Basis


def test_pages_are_bounded_and_stable(reporting: Reporting) -> None:
    everything = reporting.allocations(billing_account_id=ACCOUNT).items
    first = reporting.allocations(billing_account_id=ACCOUNT, limit=5, offset=0)
    rest = reporting.allocations(billing_account_id=ACCOUNT, limit=1000, offset=5)
    assert (len(first.items), first.total) == (5, 8)
    assert first.items + rest.items == everything
    assert reporting.allocations(billing_account_id=ACCOUNT, offset=8).items == []


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (1001, 0), (10, -1)])
def test_limits_out_of_range_are_refused(reporting: Reporting, limit: int, offset: int) -> None:
    with pytest.raises(ValueError):
        reporting.allocations(billing_account_id=ACCOUNT, limit=limit, offset=offset)


def test_scope_filters_select_one_snapshot_or_none(
    reporting: Reporting, database: Database
) -> None:
    page = reporting.allocations(
        billing_account_id=ACCOUNT, billing_period=date(2026, 10, 1), source=EXPORT.snapshot.source
    )
    assert page.calculation_run_id == database.current_run
    with pytest.raises(NotFound):
        reporting.allocations(billing_account_id="999999999999")
    with pytest.raises(NotFound):
        reporting.allocations(billing_account_id=ACCOUNT, source="another-source")


def test_filters_matching_several_scopes_must_be_narrowed(reporting: Reporting) -> None:
    with pytest.raises(AmbiguousScope):
        reporting.allocations()
    with pytest.raises(AmbiguousScope):
        reporting.allocations(billing_period=date(2026, 10, 1))
