"""Lineage around a Workload Run through the `reporting` read model (#114; spec 06 seam 1;
ADR 0022).

The hand-written fixtures report three runs: the Airflow task (…001, parent and root the DAG
run …000, which never reports itself), the dbt invocation run (…002, invocation `inv-001`)
and the dbt-ol model run (…003, `model.demo.orders`). `inv-001` ran `model.demo.orders` and
`model.demo.customers`. The Athena fixtures (M3's Link Outcome cases, `link-outcomes.json`)
link `q-001` and `q-002` to `model.demo.orders`; `q-004`'s comment names both models, so it is
ambiguous and links neither. No query links to `model.demo.customers`.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fixture_database import fill

from data_finops.contracts.workload import LinkOutcome
from data_finops.reporting import NotFound, Reporting

DAG_RUN, AIRFLOW_TASK, DBT_RUN, MODEL_RUN = (
    f"00000000-0000-4000-8000-00000000000{n}" for n in range(4)
)


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("lineage") / "data-finops.sqlite"
    fill(path)
    return path


@pytest.fixture
def reporting(database: Path) -> Iterator[Reporting]:
    with Reporting(database) as opened:
        yield opened


def test_the_root_runs_are_the_topmost_runs_reported_or_named(reporting: Reporting) -> None:
    page = reporting.root_runs()
    assert (page.total, [root.run_id for root in page.items]) == (1, [DAG_RUN])
    assert page.items[0].run is None  # named only as the others' root


def test_a_run_known_only_as_a_root_shows_its_children(reporting: Reporting) -> None:
    lineage = reporting.lineage(DAG_RUN)
    assert (lineage.run, [c.run_id for c in lineage.children]) == (None, [AIRFLOW_TASK])
    assert lineage.ancestors == []


def test_the_dbt_invocation_run_shows_its_family_and_model_executions(
    reporting: Reporting,
) -> None:
    lineage = reporting.lineage(DBT_RUN)
    assert lineage.run is not None and lineage.run.status
    assert [a.run_id for a in lineage.ancestors] == [AIRFLOW_TASK, DAG_RUN]
    assert [a.run is not None for a in lineage.ancestors] == [True, False]
    assert [c.run_id for c in lineage.children] == [MODEL_RUN]
    assert lineage.invocation_id == "inv-001"
    by_node = {m.node_id: m for m in lineage.model_executions}
    assert set(by_node) == {"model.demo.orders", "model.demo.customers"}
    orders = by_node["model.demo.orders"]
    assert [(q.query_execution.query_execution_id, q.outcome) for q in orders.queries] == [
        ("q-001", LinkOutcome.LINKED),
        ("q-002", LinkOutcome.LINKED),
        ("q-004", LinkOutcome.AMBIGUOUS),
    ]
    customers = by_node["model.demo.customers"]
    assert [(q.query_execution.query_execution_id, q.outcome) for q in customers.queries] == [
        ("q-004", LinkOutcome.AMBIGUOUS)
    ]
    assert all(m.execution is not None and m.execution.status for m in by_node.values())


def test_a_model_run_links_its_model_execution_and_its_data_assets(
    reporting: Reporting,
) -> None:
    lineage = reporting.lineage(MODEL_RUN)
    assert lineage.model_run_link is not None
    assert lineage.model_run_link.outcome == LinkOutcome.LINKED
    assert [(m.invocation_id, m.node_id) for m in lineage.model_executions] == [
        ("inv-001", "model.demo.orders")
    ]
    assert lineage.invocation_id is None
    assert lineage.lineage_edges == sorted(lineage.lineage_edges, key=lambda e: e.observed_at)
    assert {e.asset for e in lineage.lineage_edges} == set(lineage.data_assets)


def test_the_airflow_task_has_no_dbt_identity(reporting: Reporting) -> None:
    lineage = reporting.lineage(AIRFLOW_TASK)
    assert (lineage.invocation_id, lineage.model_executions) == (None, [])
    assert [c.run_id for c in lineage.children] == [DBT_RUN]


def test_an_unknown_run_is_not_found(reporting: Reporting) -> None:
    with pytest.raises(NotFound):
        reporting.lineage("00000000-0000-4000-8000-0000000000ff")


def test_root_runs_are_paged(reporting: Reporting) -> None:
    assert reporting.root_runs(offset=1).items == []
    with pytest.raises(ValueError):
        reporting.root_runs(limit=0)
