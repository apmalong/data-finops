"""Run the reference workload's dbt project under Airflow (#66, #74).

One task runs `dbt-ol run`. The OpenLineage provider's macros hand the task's run to dbt-ol
as its parent and the DAG run as its root, through the variables openlineage-dbt 1.54.0
reads (`namespace/job/run_id`). Observing what arrives answers OQ-2's Airflow half.
"""

from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

DBT_PROJECT = "/workspace/examples/reference-workload/dbt"
DBT_BIN = "/home/airflow/dbt-venv/bin"
DBT_SHELL = f'unset PYTHONPATH && export PATH="{DBT_BIN}:$PATH"'
"""The task's own shell: dbt-ol starts `dbt` by name, so its virtualenv comes first on PATH;
the image's PYTHONPATH would put Airflow's packages ahead of dbt's pinned ones."""


def _ol_macro(name: str) -> str:
    """A Jinja call to one of the OpenLineage provider's macros for this task instance."""
    return f"{{{{ macros.OpenLineageProviderPlugin.{name}(task_instance) }}}}"


with DAG(
    dag_id="reference_workload",
    schedule=None,
    start_date=datetime(2026, 11, 1),
    catchup=False,
    tags=["data-finops"],
):
    BashOperator(
        task_id="dbt_run",
        bash_command=f"{DBT_SHELL} && cd {DBT_PROJECT} && dbt-ol run --profiles-dir .",
        env={
            "OPENLINEAGE_PARENT_ID": _ol_macro("lineage_parent_id"),
            "OPENLINEAGE_ROOT_PARENT_ID": _ol_macro("lineage_root_parent_id"),
        },
        append_env=True,
    )
