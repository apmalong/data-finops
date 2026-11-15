"""Cost Dimensions resolved by a Calculation Run and read back through `reporting` (spec 09
§Cost Dimensions, ADR 0031, Q72; #222). The fixture bill has one Athena line of $6.00, metered
to `q-001` (workgroup `demo`, comment `"target_name": "dev"`), and an S3 line of $4.00 that is
Unallocated."""

import json
from collections import defaultdict
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fixture_database import ACCOUNT, EXPORT, FIXTURES, REGION, RESTATED, add_other_account

from data_finops.calculation import run_calculation
from data_finops.contracts.dimensions import NO_DIMENSIONS, CostDimensions
from data_finops.ledger import AllocationStoreOutcome, Ledger
from data_finops.reporting import BillingLineDetail, NotFound, Reporting
from data_finops.workload.store import WorkloadStore

CONFIG = CostDimensions.from_mapping(
    {
        "dimensions": {
            "env": {"sources": ["query_comment.target_name"]},
            "team": {
                "sources": ["openlineage_tag.team", "workgroup_map"],
                "workgroup_map": {"demo": "analytics"},
            },
            "tenant": {"sources": ["query_comment.tenant_id"], "sensitive": True},
        }
    },
    sha256="a" * 64,
)
CHANGED = CostDimensions.from_mapping(
    {"dimensions": {"env": {"sources": ["workgroup_map"], "workgroup_map": {"demo": "prod"}}}},
    sha256="b" * 64,
)


def _fill(
    path: Path, dimensions: CostDimensions, events: Path | None = None, history: bool = True
) -> str:
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        ledger.import_snapshot(EXPORT)
        if history:
            store.ingest_query_history(
                FIXTURES / "athena" / "query-executions.json",
                account_id=ACCOUNT,
                region=REGION,
                dimensions=dimensions,
            )
        store.ingest_events(events or FIXTURES / "openlineage" / "events.json")
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "manifest.json")
        store.ingest_dbt_artifact(FIXTURES / "dbt" / "run_results.json")
        return run_calculation(
            ledger, store, EXPORT.snapshot.scope, dimensions=dimensions
        ).calculation_run_id


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "data-finops.sqlite"
    _fill(path, CONFIG)
    yield path


def _by_target(path: Path) -> dict[str, dict[str, tuple[str | None, str | None]]]:
    with Reporting(path) as read_model:
        page = read_model.allocations(limit=1000)
    seen: dict[str, dict[str, tuple[str | None, str | None]]] = {}
    for entry in page.items:
        allocation = entry.allocation
        seen[allocation.target_id] = {
            name: (value.value, value.source) for name, value in allocation.dimensions.items()
        }
    return seen


def test_the_first_source_with_a_value_wins_and_is_recorded(database: Path) -> None:
    query = _by_target(database)["q-001"]

    assert query["env"] == ("dev", "query_comment.target_name")
    assert query["team"] == ("analytics", "workgroup_map")


def test_cost_with_no_value_is_unassigned(database: Path) -> None:
    by_target = _by_target(database)

    assert by_target["q-001"]["tenant"] == (None, None)
    assert by_target["unallocated"]["team"] == (None, None)  # the S3 line names no workgroup


TAGS_FACET = {
    "_producer": "https://example.com/producer",
    "_schemaURL": "https://openlineage.io/spec/facets/1-0-0/TagsRunFacet.json#/$defs/TagsRunFacet",
    "tags": [{"key": "team", "value": "payments", "source": "USER"}],
}


def test_an_openlineage_tag_before_the_workgroup_map_wins(tmp_path: Path) -> None:
    events = json.loads((FIXTURES / "openlineage" / "events.json").read_text(encoding="utf-8"))
    tagged = [
        event
        | {"run": event["run"] | {"facets": event["run"].get("facets", {}) | {"tags": TAGS_FACET}}}
        for event in events
    ]
    path = tmp_path / "events.json"
    path.write_text(json.dumps(tagged), encoding="utf-8")
    database = tmp_path / "tagged.sqlite"
    _fill(database, CONFIG, events=path)

    assert _by_target(database)["q-001"]["team"] == ("payments", "openlineage_tag.team")


def test_each_dimensions_total_equals_the_snapshots_for_every_cost_basis(database: Path) -> None:
    with Reporting(database) as read_model:
        allocations = [e.allocation for e in read_model.allocations(limit=1000).items]
        snapshot: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for allocation in allocations:
            snapshot[(allocation.currency, allocation.cost_basis)] += Decimal(allocation.amount)
        for name in ("env", "team", "tenant"):
            page = read_model.dimension_rollups(name=name, limit=1000)
            totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
            for entry in page.items:
                for total in entry.totals:
                    totals[(total.currency, total.cost_basis)] += Decimal(total.amount)
                    assert sum(Decimal(s.amount) for s in total.sources) == Decimal(total.amount)
            assert totals == snapshot, name
            page_totals = {(t.currency, t.cost_basis): Decimal(t.amount) for t in page.totals}
            assert page_totals == snapshot, name


def test_the_dimension_route_lists_each_value_and_how_its_sources_split(database: Path) -> None:
    with Reporting(database) as read_model:
        page = read_model.dimension_rollups(name="team", limit=1000)

    values = {entry.value: entry for entry in page.items}
    assert set(values) == {"analytics", None}
    analytics = {(t.currency, t.cost_basis): t for t in values["analytics"].totals}
    billed = analytics[("USD", "BilledCost")]
    assert Decimal(billed.amount) == Decimal("6.00")
    assert [(s.source, Decimal(s.amount)) for s in billed.sources] == [
        ("workgroup_map", Decimal("6.00"))
    ]


def test_an_unknown_dimension_is_not_found(database: Path) -> None:
    with Reporting(database) as read_model, pytest.raises(NotFound):
        read_model.dimension_rollups(name="cost_center")


def test_a_changed_configuration_alone_makes_no_new_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "data-finops.sqlite"
    first = _fill(path, CONFIG)
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        again = run_calculation(ledger, store, EXPORT.snapshot.scope, dimensions=CHANGED)
        stored = ledger.allocation_snapshot(first)
        assert stored is not None

    assert (again.outcome, again.calculation_run_id) == (AllocationStoreOutcome.REPLAY, first)
    assert stored.snapshot.dimensions_sha256 == "a" * 64
    assert {a.target_id: a.dimensions["env"].value for a in stored.allocations}["q-001"] == "dev"


def test_a_changed_configuration_applies_from_the_next_snapshot_of_new_inputs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data-finops.sqlite"
    first = _fill(path, CONFIG)
    with Ledger(path) as ledger, WorkloadStore(path) as store:
        ledger.import_snapshot(RESTATED)
        new = run_calculation(ledger, store, EXPORT.snapshot.scope, dimensions=CHANGED)
        stored = ledger.allocation_snapshot(new.calculation_run_id)
        old = ledger.allocation_snapshot(first)
        assert stored is not None and old is not None

    assert new.outcome is AllocationStoreOutcome.NEW
    assert stored.snapshot.dimensions_sha256 == "b" * 64
    assert {a.target_id: a.dimensions["env"].value for a in stored.allocations}["q-001"] == "prod"
    assert {a.target_id: a.dimensions["env"].value for a in old.allocations}["q-001"] == "dev"


def test_cost_dimensions_join_the_policy_version_with_or_without_a_configuration(
    tmp_path: Path,
) -> None:
    with_config = _fill(tmp_path / "with.sqlite", CONFIG)
    without = _fill(tmp_path / "without.sqlite", NO_DIMENSIONS)
    with Ledger(tmp_path / "with.sqlite") as one, Ledger(tmp_path / "without.sqlite") as two:
        a, b = one.allocation_snapshot(with_config), two.allocation_snapshot(without)
        assert a is not None and b is not None

    assert "cost-dimensions/1" in a.snapshot.policy_version.split("+")
    assert a.snapshot.policy_version == b.snapshot.policy_version
    assert a.snapshot.input_digest == b.snapshot.input_digest
    assert all(allocation.dimensions == {} for allocation in b.allocations)


def test_the_minimal_cost_explanation_leaves_out_sensitive_dimensions(database: Path) -> None:
    with Reporting(database) as read_model:
        ref = next(
            e.ref
            for e in read_model.allocations(limit=1000).items
            if e.allocation.target_id == "q-001"
        )
        full = read_model.explanation(ref, detail=BillingLineDetail.FULL)
        minimal = read_model.explanation(ref, detail=BillingLineDetail.MINIMAL)

    assert set(full.allocation.dimensions) == {"env", "team", "tenant"}
    assert set(minimal.allocation.dimensions) == {"env", "team"}
    assert minimal.query_execution is not None and minimal.query_execution.claims == {}


def test_an_amount_with_no_query_takes_the_workgroup_of_its_line(tmp_path: Path) -> None:
    database = tmp_path / "no-history.sqlite"
    _fill(database, CONFIG, history=False)  # the Athena line has no known query: Unallocated

    with Reporting(database) as read_model:
        athena = [
            e.allocation
            for e in read_model.allocations(limit=1000).items
            if e.allocation.billing_line_ref.endswith("#0")
        ]
    assert {a.target_id for a in athena} == {"unallocated"}
    assert {(a.dimensions["team"].value, a.dimensions["team"].source) for a in athena} == {
        ("analytics", "workgroup_map")
    }


def test_a_tag_with_two_values_on_one_run_is_ambiguous_and_gives_no_value(tmp_path: Path) -> None:
    events = json.loads((FIXTURES / "openlineage" / "events.json").read_text(encoding="utf-8"))
    tagged = []
    for index, event in enumerate(events):
        facet = TAGS_FACET | {"tags": [{"key": "team", "value": f"team-{index}", "source": "USER"}]}
        tagged.append(
            event
            | {"run": event["run"] | {"facets": event["run"].get("facets", {}) | {"tags": facet}}}
        )
    path = tmp_path / "events.json"
    path.write_text(json.dumps(tagged), encoding="utf-8")
    database = tmp_path / "ambiguous.sqlite"
    _fill(database, CONFIG, events=path)

    assert _by_target(database)["q-001"]["team"] == ("analytics", "workgroup_map")


def test_each_dimensions_total_equals_the_snapshots_in_every_currency(tmp_path: Path) -> None:
    database = tmp_path / "two-currencies.sqlite"
    _fill(database, CONFIG)
    run = add_other_account(database, dimensions=CONFIG)  # S3 lines in USD and EUR, a EUR credit
    with Reporting(database) as read_model:
        page = read_model.allocations(calculation_run_id=run, limit=1000)
        allocations = [entry.allocation for entry in page.items]
        rollups = read_model.dimension_rollups(name="team", calculation_run_id=run, limit=1000)
    snapshot: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for allocation in allocations:
        snapshot[(allocation.currency, allocation.cost_basis)] += Decimal(allocation.amount)
    totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for entry in rollups.items:
        for total in entry.totals:
            totals[(total.currency, total.cost_basis)] += Decimal(total.amount)

    assert {currency for currency, _ in snapshot} == {"USD", "EUR"}
    assert totals == snapshot


def test_every_allocation_of_a_snapshot_has_the_same_dimensions(database: Path) -> None:
    # The dashboard reads the dimension names from one Allocation of the snapshot (#223).
    with Reporting(database) as read_model:
        names = {
            frozenset(entry.allocation.dimensions)
            for entry in read_model.allocations(limit=1000).items
        }

    assert names == {frozenset({"env", "team", "tenant"})}
