"""The reference workload run under Airflow 3 (#75), anonymized (#76): the real chain.

Airflow 3.3.2 with its bundled OpenLineage provider 2.20.1 ran `dbt-ol run` as a DAG task,
handing the task run and DAG run to dbt-ol as parent and root (OQ-2's Airflow half). Unlike
the hand-written fixtures, the DAG run here is a stored record with its own events.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from data_finops.contracts.workload import LinkOutcome, LinkResolution, WorkloadRunStatus
from data_finops.workload.store import WorkloadStore

SAMPLE = Path(__file__).resolve().parents[2] / "fixtures" / "reference-workload" / "airflow"
INVOCATION = "1856232b-b384-4d23-ba84-0dc187d3795f"
DAG_RUN = "01a19dd8-4a8c-72ec-873a-a3bb810cbae6"
TASK_RUN = "01a19dd8-4a8c-7abf-9e93-25adbcb5d081"
DBT_RUN = "01a19dd8-7296-72cf-bd35-196b0a602ab5"


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[WorkloadStore]:
    with WorkloadStore(tmp_path_factory.mktemp("airflow") / "data-finops.sqlite") as opened:
        yield opened


@pytest.fixture(scope="module")
def ingested(store: WorkloadStore) -> dict[str, tuple[int, int]]:
    results = {
        name: store.ingest_events(SAMPLE / name)
        for name in ("airflow-events.jsonl", "dbt-events.jsonl")
    }
    store.ingest_dbt_artifact(SAMPLE / "run_results.json")
    return {name: (r.accepted, len(r.rejected)) for name, r in results.items()}


def _linked(resolution: LinkResolution | None) -> str:
    assert resolution is not None and resolution.outcome == LinkOutcome.LINKED
    assert resolution.link is not None
    return resolution.link.target_id


def test_every_event_is_accepted(ingested: dict[str, tuple[int, int]]) -> None:
    assert ingested == {"airflow-events.jsonl": (4, 0), "dbt-events.jsonl": (10, 0)}


def test_the_invocation_reaches_the_airflow_task_and_dag_run(
    store: WorkloadStore, ingested: dict[str, tuple[int, int]]
) -> None:
    assert _linked(store.workload_run_of_invocation(INVOCATION)) == DBT_RUN
    dbt_run = store.workload_run(DBT_RUN)
    assert dbt_run is not None
    assert (dbt_run.job_namespace, dbt_run.job_name) == (
        "reference-airflow",  # dbt-ol takes Airflow's OpenLineage namespace
        "dbt-run-reference_workload",
    )
    assert (dbt_run.parent_run_id, dbt_run.root_run_id) == (TASK_RUN, DAG_RUN)
    task = store.workload_run(TASK_RUN)
    dag = store.workload_run(DAG_RUN)
    assert task is not None and dag is not None
    assert (task.job_name, task.status, task.parent_run_id) == (
        "reference_workload.dbt_run",
        WorkloadRunStatus.COMPLETED,
        DAG_RUN,
    )
    assert (dag.job_name, dag.status, dag.parent_run_id) == (
        "reference_workload",
        WorkloadRunStatus.COMPLETED,
        None,
    )
    assert [run.run_id for run in store.children(DAG_RUN)] == [TASK_RUN]
    assert [run.run_id for run in store.children(TASK_RUN)] == [DBT_RUN]


def test_each_model_run_links_to_its_model_execution(
    store: WorkloadStore, ingested: dict[str, tuple[int, int]]
) -> None:
    targets = {_linked(store.model_execution_of_run(run.run_id)) for run in store.children(DBT_RUN)}
    assert targets == {
        f'["{INVOCATION}","model.reference_workload.{name}"]'
        for name in ("daily_orders", "revenue_by_region", "stg_customers", "stg_orders")
    }
