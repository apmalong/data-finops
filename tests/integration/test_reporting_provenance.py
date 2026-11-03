"""Provenance of a Calculation Run through the `reporting` read model (#113; spec 06 seam 1).

The restated fixture export (`fixtures/focus/export-2026-10-restated.json`) makes a second
Allocation Snapshot for the same Snapshot Scope, which supersedes the first.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from fixture_database import ACCOUNT, EXPORT, RESTATED, fill, restate

from data_finops.calculation import POLICY_VERSION
from data_finops.ledger import Ledger
from data_finops.reporting import ConflictingFilters, NotFound, Reporting


class Database(NamedTuple):
    path: Path
    first_run: str
    current_run: str


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Database:
    path = tmp_path_factory.mktemp("provenance") / "data-finops.sqlite"
    first = fill(path)
    return Database(path, first, restate(path))


@pytest.fixture
def reporting(database: Database) -> Iterator[Reporting]:
    with Reporting(database.path) as opened:
        yield opened


def test_the_current_run_names_what_it_read_and_how(
    reporting: Reporting, database: Database
) -> None:
    provenance = reporting.provenance(billing_account_id=ACCOUNT)
    assert (provenance.calculation_run_id, provenance.is_current, provenance.superseded_by) == (
        database.current_run,
        True,
        None,
    )
    assert provenance.policy_version == POLICY_VERSION
    assert provenance.policy_components == POLICY_VERSION.split("+")
    assert provenance.policy_components[0].startswith("allocation-engine/")
    assert len(provenance.input_digest) == 64
    read = provenance.export_snapshot
    assert read is not None
    assert (read.snapshot_id, read.published_at) == (
        RESTATED.snapshot.snapshot_id,
        RESTATED.snapshot.published_at,
    )
    assert len(read.content_digest) == 64
    assert provenance.scope == EXPORT.snapshot.scope


def test_a_superseded_run_names_the_run_that_superseded_it(
    reporting: Reporting, database: Database
) -> None:
    first = reporting.provenance(calculation_run_id=database.first_run)
    assert (first.is_current, first.superseded_by) == (False, database.current_run)
    assert first.export_snapshot is not None
    assert first.export_snapshot.snapshot_id == EXPORT.snapshot.snapshot_id
    current = reporting.provenance(billing_account_id=ACCOUNT)
    assert current.export_snapshot is not None
    assert first.created_at < current.created_at
    assert first.export_snapshot.content_digest != current.export_snapshot.content_digest


def test_selection_is_as_for_the_other_reads(reporting: Reporting, database: Database) -> None:
    with pytest.raises(NotFound):
        reporting.provenance(calculation_run_id="calc-unknown")
    with pytest.raises(ConflictingFilters):
        reporting.provenance(calculation_run_id=database.first_run, billing_account_id=ACCOUNT)


def _store_copy(path: Path, of_run: str, as_run: str, *, keep_allocations: bool = True) -> None:
    """Store `of_run`'s snapshot again as `as_run` with a new digest, through the Ledger."""
    with Ledger(path) as ledger:
        stored = ledger.allocation_snapshot(of_run)
        assert stored is not None
        ledger.store_allocations(
            EXPORT.snapshot.scope,
            calculation_run_id=as_run,
            policy_version=stored.snapshot.policy_version,
            input_digest=as_run,
            export_snapshot_id=stored.snapshot.export_snapshot_id,
            allocations=[
                a.model_copy(update={"calculation_run_id": as_run})
                for a in (stored.allocations if keep_allocations else [])
            ],
        )


def test_each_run_is_superseded_by_the_next_not_the_latest(tmp_path: Path) -> None:
    path = tmp_path / "data-finops.sqlite"
    first = fill(path)
    second = restate(path)
    _store_copy(path, second, "calc-third")
    with Reporting(path) as reporting:
        assert reporting.provenance(calculation_run_id=first).superseded_by == second
        assert reporting.provenance(calculation_run_id=second).superseded_by == "calc-third"
        assert reporting.provenance(calculation_run_id="calc-third").superseded_by is None


def test_a_snapshot_without_allocations_still_names_the_export_it_read(tmp_path: Path) -> None:
    path = tmp_path / "data-finops.sqlite"
    first = fill(path)
    _store_copy(path, first, "calc-empty", keep_allocations=False)
    with Reporting(path) as reporting:
        provenance = reporting.provenance(calculation_run_id="calc-empty")
    assert provenance.export_snapshot is not None
    assert provenance.export_snapshot.snapshot_id == EXPORT.snapshot.snapshot_id
    assert provenance.scope == EXPORT.snapshot.scope


def test_a_pre_m5_snapshot_with_no_allocations_keeps_its_scope_and_successor(
    tmp_path: Path,
) -> None:
    # Before M5 no Export Snapshot was recorded; with no Allocations nothing names it, but the
    # Allocation Snapshot's own row still gives its scope, so its successor is still found.
    path = tmp_path / "data-finops.sqlite"
    fill(path)
    with Ledger(path) as ledger:
        ledger.store_allocations(
            EXPORT.snapshot.scope,
            calculation_run_id="calc-pre-m5",
            policy_version=POLICY_VERSION,
            input_digest="pre-m5",
            allocations=[],
        )
    later = restate(path)
    with Reporting(path) as reporting:
        provenance = reporting.provenance(calculation_run_id="calc-pre-m5")
    assert (provenance.export_snapshot, provenance.scope, provenance.superseded_by) == (
        None,
        EXPORT.snapshot.scope,
        later,
    )
