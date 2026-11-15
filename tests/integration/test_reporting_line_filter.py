"""The read model restricted to some Billing Lines (#227, Q82): every read sees only those lines
and the Allocations of them, as the Demo Snapshot needs. The fixture export has an Athena line
of the workgroup `demo` ($6.00 billed) and an S3 line ($4.00 billed)."""

from decimal import Decimal
from pathlib import Path

import pytest
from fixture_database import fill

from data_finops.contracts.billing import CostBasis, FocusBillingLine
from data_finops.reporting import NotFound, Reporting

ATHENA = "arn:aws:athena:us-east-1:123456789012:workgroup/demo"


def _athena_only(line: FocusBillingLine) -> bool:
    return line.ResourceId == ATHENA


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("filtered") / "data-finops.sqlite"
    fill(path)
    return path


def test_the_summary_bills_only_the_kept_lines(database: Path) -> None:
    with Reporting(database, lines=_athena_only) as read_model:
        (summary,) = read_model.cost_summary().summaries
    (usd,) = summary.currencies
    assert Decimal(usd.billed) == Decimal("6.00")
    allocated = sum(Decimal(a.amount) for a in usd.allocated)
    unallocated = sum(Decimal(u.amount) for u in usd.unallocated)
    assert allocated + unallocated == Decimal("6.00")


def test_allocations_are_those_of_the_kept_lines_with_their_own_refs(database: Path) -> None:
    with Reporting(database) as read_model:
        everything = read_model.allocations(limit=1000).items
    with Reporting(database, lines=_athena_only) as read_model:
        kept = read_model.allocations(limit=1000)
        lines = {e.allocation.billing_line_ref for e in kept.items}
        (line_ref,) = lines
        (row,) = read_model.reconciliation(cost_basis=CostBasis.BILLED_COST).items

    assert row.billing_line_ref == line_ref and Decimal(row.billed) == Decimal("6.00")
    assert 0 < kept.total < len(everything)
    assert kept.items == [e for e in everything if e.allocation.billing_line_ref == line_ref]


def _s3_only(line: FocusBillingLine) -> bool:
    return line.ResourceId != ATHENA


def test_roll_ups_count_only_the_kept_lines(database: Path) -> None:
    # The Athena line is allocated to one query of `model.demo.orders`; the S3 line is
    # unallocated. So the Athena line gives every Roll-up, and the S3 line gives none.
    with Reporting(database, lines=_athena_only) as read_model:
        (model,) = read_model.rollups(level="model_execution", limit=1000).items
        runs = read_model.rollups(level="workload_run", limit=1000)
    with Reporting(database, lines=_s3_only) as read_model:
        none = read_model.rollups(level="model_execution", limit=1000)
        no_runs = read_model.rollups(level="workload_run", limit=1000)
        (summary,) = read_model.cost_summary().summaries

    billed = {t.cost_basis: Decimal(t.amount) for t in model.totals}
    assert billed[CostBasis.BILLED_COST] == Decimal("6.00")
    assert runs.total == 3
    assert (none.total, no_runs.total) == (0, 0)
    (usd,) = summary.currencies
    assert Decimal(usd.billed) == Decimal("4.00")
    assert sum(Decimal(u.amount) for u in usd.unallocated) == Decimal("4.00")


def test_the_explanation_of_a_left_out_allocation_is_not_found(database: Path) -> None:
    with Reporting(database) as read_model:
        everything = read_model.allocations(limit=1000).items
    with Reporting(database, lines=_athena_only) as read_model:
        kept = {e.ref for e in read_model.allocations(limit=1000).items}
        left_out = next(e.ref for e in everything if e.ref not in kept)
        shown = read_model.explanation(next(iter(kept)))
        with pytest.raises(NotFound):
            read_model.explanation(left_out)

    assert shown.billing_line is not None and shown.billing_line.ResourceId == ATHENA
    assert shown.rollups and all(Decimal(r.amount) <= Decimal("6.00") for r in shown.rollups)
