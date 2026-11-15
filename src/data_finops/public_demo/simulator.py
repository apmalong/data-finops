"""The simulated orchestrator (#226; spec 09 §The simulated orchestrator; ADR 0030): a small
Airflow and dbt that run real queries in the demo workgroup and write believable records.

`plan` decides, from a seed and the start time alone, which DAGs run, their tasks and models,
the IDs of every run, the tenant of each model and which tasks fail. `simulate` runs the plan:
each model's statements go to Athena, one at a time, each with the dbt-athena query comment as
its first line. Then, for each DAG run, it writes the dbt artifacts of each invocation to
`inbox/dbt/<invocation>/` and the OpenLineage events to `inbox/openlineage/`, as the
reference workload's Airflow and dbt-ol wrote them (`templates.json`, from
`fixtures/reference-workload/airflow/`).
"""

import copy
import json
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from importlib.resources import files
from typing import Any

from data_finops.collection.run import DBT_INBOX, OPENLINEAGE_INBOX
from data_finops.providers.aws import put_bytes
from data_finops.providers.aws.calls import call
from data_finops.public_demo.dataset import TENANTS
from data_finops.public_demo.workload import (
    DAGS,
    DATABASE,
    MODELS,
    NAMESPACE,
    PROJECT,
    WORKGROUP,
    Dag,
)

SEED = 20261107
"""The seed of every plan, with the start time: a start planned again gives the same runs."""
TASK_RETRY = 0.05
"""The share of tasks that fail one time and succeed on retry (spec 09)."""
RUN_FAILURE = 0.02
"""The share of DAG runs that fail completely: one task fails on both tries."""
POLL_SECONDS = 1.0
MAX_POLLS = 120
"""How long one query may run, in polls, before the simulator stops it. The demo's queries
take seconds; a stopped query fails its model."""
TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class PlannedModel:
    name: str
    node_id: str
    tenant: str
    statements: tuple[str, ...]
    """Each statement as it is sent: the query comment, then the SQL."""
    status: str
    """`success`; `error`, when its one statement names a column that does not exist, so that
    Athena fails it, as a real dbt failure fails a query (Athena does not charge for it); or
    `skipped`, after an error. A skipped model's statements are never sent."""


@dataclass(frozen=True)
class Attempt:
    """One try of one task: an Airflow task run, and the dbt invocation it starts."""

    task: str
    try_number: int
    run_id: str
    dbt_run_id: str
    invocation_id: str
    models: tuple[PlannedModel, ...]

    @property
    def succeeded(self) -> bool:
        return all(model.status == "success" for model in self.models)


@dataclass(frozen=True)
class DagRun:
    dag: str
    team: str
    run_id: str
    """The OpenLineage run ID of the DAG run."""
    airflow_run_id: str
    """Airflow's own run ID, `scheduled__<start>`: the `dag_run_id` of the query comment."""
    start: datetime
    attempts: tuple[Attempt, ...]

    @property
    def succeeded(self) -> bool:
        return self.attempts[-1].succeeded and len({a.task for a in self.attempts}) == len(
            _dag(self.dag).tasks
        )


def plan(start: datetime, seed: int = SEED) -> tuple[DagRun, ...]:
    """The DAG runs of a start, deterministic for the seed and the start."""
    rng = random.Random(f"{seed}:{start.isoformat()}")  # nosec B311 (a plan, not a secret)
    return tuple(_plan_dag(rng, dag, start) for dag in DAGS if dag.due(start))


def _plan_dag(rng: random.Random, dag: Dag, start: datetime) -> DagRun:
    run_id = _uuid(rng)
    airflow_run_id = f"scheduled__{start.isoformat()}"
    fails_completely = rng.random() < RUN_FAILURE
    failing_task = rng.randrange(len(dag.tasks)) if fails_completely else -1
    attempts: list[Attempt] = []
    for position, task in enumerate(dag.tasks):
        tries = 2 if position == failing_task or rng.random() < TASK_RETRY else 1
        for try_number in range(1, tries + 1):
            fails = (try_number == 1 and tries == 2) or position == failing_task
            attempts.append(
                _plan_attempt(rng, dag, task.name, task.models, try_number, fails, airflow_run_id)
            )
        if position == failing_task:
            break
    return DagRun(dag.name, dag.team, run_id, airflow_run_id, start, tuple(attempts))


def _plan_attempt(
    rng: random.Random,
    dag: Dag,
    task: str,
    models: tuple[str, ...],
    try_number: int,
    fails: bool,
    airflow_run_id: str,
) -> Attempt:
    run_id, dbt_run_id, invocation_id = _uuid(rng), _uuid(rng), _uuid(rng)
    error_at = rng.randrange(len(models)) if fails else len(models)
    planned = []
    for position, name in enumerate(models):
        tenant = rng.choice(TENANTS)
        node_id = f"model.{PROJECT}.{name}"
        comment = {
            "app": "dbt",
            "node_id": node_id,
            "invocation_id": invocation_id,
            "team": dag.team,
            "tenant_id": tenant,
            "dag_run_id": airflow_run_id,
        }
        status = (
            "success" if position < error_at else "error" if position == error_at else "skipped"
        )
        # The failing statement names a column that does not exist; `name` is one of MODELS.
        missing = f"select {name}_status from orders"  # nosec B608 (no outside value)
        sqls = MODELS[name] if status != "error" else (missing,)
        statements = tuple(
            f"-- /* {json.dumps(comment)} */\n{sql.format(tenant=tenant)}" for sql in sqls
        )
        planned.append(PlannedModel(name, node_id, tenant, statements, status))
    return Attempt(task, try_number, run_id, dbt_run_id, invocation_id, tuple(planned))


def _uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _dag(name: str) -> Dag:
    return next(dag for dag in DAGS if dag.name == name)


@dataclass
class _ModelRun:
    model: PlannedModel
    status: str
    started: datetime
    finished: datetime
    scanned: int = 0


@dataclass
class _AttemptRun:
    attempt: Attempt
    started: datetime
    finished: datetime | None = None
    models: list[_ModelRun] = field(default_factory=list)
    """The models that ran: up to and with the first that failed."""
    queries: int = 0

    @property
    def succeeded(self) -> bool:
        ran_all = len(self.models) == len(self.attempt.models)
        return ran_all and all(m.status == "success" for m in self.models)


def simulate(
    start: datetime,
    *,
    athena: Any,
    s3: Any,
    bucket: str,
    clock: Clock,
    sleep: Callable[[float], None] = time.sleep,
    seed: int = SEED,
) -> dict[str, int]:
    """Run the plan of a start, and write each DAG run's records to the Inbox of `bucket` as
    soon as the DAG run ends. A failed AWS call raises AwsSourceError with the code alone."""
    queries = failed = 0
    source = f"awsathena://athena.{athena.meta.region_name}.amazonaws.com"
    runs = plan(start, seed)
    for run in runs:
        started = clock()
        attempt_runs: list[_AttemptRun] = []
        for attempt in run.attempts:
            last = attempt_runs[-1] if attempt_runs else None
            if last and not last.succeeded and last.attempt.task != attempt.task:
                break  # a task failed with no retry left: the DAG run stops, as Airflow's would
            executed = _execute(attempt, athena, clock, sleep)
            queries += executed.queries
            attempt_runs.append(executed)
        ended = clock()
        finished = {executed.attempt.task for executed in attempt_runs if executed.succeeded}
        succeeded = finished == {task.name for task in _dag(run.dag).tasks}
        failed += not succeeded
        _write(s3, bucket, run, attempt_runs, (started, ended), succeeded, source)
    return {"dag_runs": len(runs), "failed_dag_runs": failed, "queries": queries}


def _execute(
    attempt: Attempt, athena: Any, clock: Clock, sleep: Callable[[float], None]
) -> _AttemptRun:
    executed = _AttemptRun(attempt, started=clock())
    for model in attempt.models:
        if model.status == "skipped":
            break
        began = clock()
        scanned, status = 0, "success"
        for statement in model.statements:
            state, size = _run_query(athena, statement, sleep)
            executed.queries += 1
            scanned += size
            if state != "SUCCEEDED":
                status = "error"
                break
        executed.models.append(_ModelRun(model, status, began, clock(), scanned))
        if status == "error":
            break
    executed.finished = clock()
    return executed


def _run_query(athena: Any, statement: str, sleep: Callable[[float], None]) -> tuple[str, int]:
    started = call(
        athena.start_query_execution,
        QueryString=statement,
        WorkGroup=WORKGROUP,
        QueryExecutionContext={"Database": DATABASE},
    )
    query_id = started["QueryExecutionId"]
    for _ in range(MAX_POLLS):
        execution = call(athena.get_query_execution, QueryExecutionId=query_id)["QueryExecution"]
        state = execution["Status"]["State"]
        if state in TERMINAL:
            return state, int(execution.get("Statistics", {}).get("DataScannedInBytes", 0))
        sleep(POLL_SECONDS)
    call(athena.stop_query_execution, QueryExecutionId=query_id)
    return "CANCELLED", 0


def _write(
    s3: Any,
    bucket: str,
    run: DagRun,
    attempt_runs: list[_AttemptRun],
    span: tuple[datetime, datetime],
    succeeded: bool,
    source: str,
) -> None:
    """The dbt artifacts first, then the events: the collector reads each invocation's folder
    once both files are there, and the events in any order. `source` is the OpenLineage
    namespace of the Athena datasets."""
    for executed in attempt_runs:
        folder = f"{DBT_INBOX}{executed.attempt.invocation_id}/"
        put_bytes(s3, bucket, f"{folder}manifest.json", _json(_manifest(executed)))
        put_bytes(s3, bucket, f"{folder}run_results.json", _json(_run_results(executed)))
    events = _events(run, attempt_runs, span, succeeded, source)
    body = "".join(json.dumps(event) + "\n" for event in events).encode("utf-8")
    put_bytes(s3, bucket, f"{OPENLINEAGE_INBOX}{run.dag}/{run.run_id}.jsonl", body)


def _json(document: dict[str, Any]) -> bytes:
    return json.dumps(document, indent=1).encode("utf-8")


@cache
def _templates() -> dict[str, Any]:
    text = files("data_finops.public_demo").joinpath("templates.json").read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def _template(name: str, replacements: dict[str, str]) -> dict[str, Any]:
    """A deep copy of a template, with each string (and key) having the replacements made."""
    replaced: dict[str, Any] = _replaced(copy.deepcopy(_templates()[name]), replacements)
    return replaced


def _replaced(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_replaced(item, replacements) for item in value]
    if isinstance(value, dict):
        return {_replaced(k, replacements): _replaced(v, replacements) for k, v in value.items()}
    return value


def _time(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _timed(event: dict[str, Any], phase: str, when: datetime, succeeded: bool) -> dict[str, Any]:
    """The event of a run's start (`phase` START) or end, with its time and its type: START,
    COMPLETE or FAIL."""
    end = "COMPLETE" if succeeded else "FAIL"
    event.update(eventTime=_time(when), eventType="START" if phase == "START" else end)
    return event


def _airflow_names(run: DagRun, task: str = "dbt_run") -> dict[str, str]:
    return {
        "reference-airflow": NAMESPACE,
        "reference_workload.dbt_run": f"{run.dag}.{task}",
        "reference_workload": run.dag,
        "manual__2026-11-02T19:59:20.204649+00:00": run.airflow_run_id,
        "dbt_run": task,
    }


def _events(
    run: DagRun,
    attempt_runs: list[_AttemptRun],
    span: tuple[datetime, datetime],
    succeeded: bool,
    source: str,
) -> list[dict[str, Any]]:
    """The DAG run's events in the order Airflow and dbt-ol would send them."""
    started, ended = span
    events = [_dag_event(run, "START", started, started, succeeded)]
    for executed in attempt_runs:
        events += _task_events(run, executed, source)
    end = _dag_event(run, "END", ended, started, succeeded)
    states = end["run"]["facets"]["airflowState"]
    for executed in attempt_runs:  # the last try of each task is its state
        task = executed.attempt.task
        states["tasksState"][task] = "success" if executed.succeeded else "failed"
        finished = executed.finished or executed.started
        states["tasksDuration"][task] = (finished - executed.started).total_seconds()
    events.append(end)
    return events


def _dag_event(
    run: DagRun, phase: str, when: datetime, started: datetime, succeeded: bool
) -> dict[str, Any]:
    template = "dag_start" if phase == "START" else "dag_complete"
    event = _timed(_template(template, _airflow_names(run)), phase, when, succeeded)
    event["run"]["runId"] = run.run_id
    dag_run = event["run"]["facets"]["airflowDagRun"]["dagRun"]
    dag_run.update(run_type="scheduled", triggered_by="timetable", run_after=_time(run.start))
    dag_run["start_date"] = _time(started)
    dag_run.pop("triggering_user_name", None)
    if phase != "START":
        dag_run.update(end_date=_time(when), duration=(when - started).total_seconds())
        event["run"]["facets"]["airflowState"] = {
            **event["run"]["facets"]["airflowState"],
            "dagRunState": "success" if succeeded else "failed",
            "tasksState": {},
            "tasksDuration": {},
        }
    return event


def _task_events(run: DagRun, executed: _AttemptRun, source: str) -> list[dict[str, Any]]:
    attempt = executed.attempt
    names = _airflow_names(run, attempt.task)
    finished = executed.finished or executed.started
    events = []
    for phase, when in (("START", executed.started), ("END", finished)):
        name = "task_start" if phase == "START" else "task_complete"
        event = _timed(_template(name, names), phase, when, executed.succeeded)
        event["run"]["runId"] = attempt.run_id
        facets = event["run"]["facets"]
        facets["airflow"]["taskUuid"] = attempt.run_id
        facets["airflow"]["taskInstance"]["try_number"] = attempt.try_number
        facets["airflow"]["taskInstance"].pop("log_url", None)
        facets["airflow"]["dagRun"].update(run_type="scheduled", run_after=_time(run.start))
        facets["airflow"]["dagRun"].pop("triggering_user_name", None)
        facets["parent"]["run"]["runId"] = run.run_id
        facets["parent"]["root"]["run"]["runId"] = run.run_id
        events.append(event)
    start, end = events
    return [start, *_dbt_events(run, executed, source), end]


def _dbt_names() -> dict[str, str]:
    return {"reference-airflow": NAMESPACE, "reference_workload": PROJECT}


def _parent(run: DagRun, template: dict[str, Any], job: str, namespace: str, run_id: str) -> None:
    template["job"] = {"namespace": namespace, "name": job}
    template["run"] = {"runId": run_id}
    template["root"] = {
        "job": {"namespace": NAMESPACE, "name": run.dag},
        "run": {"runId": run.run_id},
    }


def _dbt_events(run: DagRun, executed: _AttemptRun, source: str) -> list[dict[str, Any]]:
    """dbt-ol's events: the invocation run, a child of the task run, and a run per model."""
    attempt = executed.attempt
    job = f"dbt-run-{PROJECT}"
    events = []
    for phase in ("START", "END"):
        template = "dbt_start" if phase == "START" else "dbt_complete"
        when = executed.started if phase == "START" else executed.finished or executed.started
        event = _timed(_template(template, _dbt_names()), phase, when, executed.succeeded)
        event["job"]["name"] = job
        event["run"]["runId"] = attempt.dbt_run_id
        event["run"]["facets"]["dbt_run"]["invocation_id"] = attempt.invocation_id
        _parent(
            run,
            event["run"]["facets"]["parent"],
            f"{run.dag}.{attempt.task}",
            NAMESPACE,
            attempt.run_id,
        )
        events.append(event)
    models = []
    for model_run in executed.models:
        model_id = _model_run_id(attempt, model_run.model)
        for phase, when in (("START", model_run.started), ("END", model_run.finished)):
            template = "model_start" if phase == "START" else "model_complete"
            succeeded = model_run.status == "success"
            event = _timed(_template(template, _dbt_names()), phase, when, succeeded)
            _model_job(event, model_run.model, source)
            event["run"]["runId"] = model_id
            event["run"]["facets"]["dbt_run"]["invocation_id"] = attempt.invocation_id
            _parent(run, event["run"]["facets"]["parent"], job, NAMESPACE, attempt.dbt_run_id)
            models.append(event)
    start, end = events
    return [start, *models, end]


def _model_run_id(attempt: Attempt, model: PlannedModel) -> str:
    return str(uuid.uuid5(uuid.UUID(attempt.dbt_run_id), model.node_id))


def _model_job(event: dict[str, Any], model: PlannedModel, source: str) -> None:
    event["job"]["name"] = f"awsdatacatalog.{PROJECT}.{PROJECT}.{model.name}"
    facets = event["job"]["facets"]
    facets["dbt_node"].update(
        alias=model.name,
        schema=PROJECT,
        unique_id=model.node_id,
        original_file_path=f"models/{model.name}.sql",
    )
    facets["sql"]["query"] = model.statements[-1].partition("\n")[2]
    event["inputs"] = [
        {"namespace": source, "name": f"awsdatacatalog.{DATABASE}.{table}"}
        for table in ("customers", "orders", "events")
        if any(f" {table}" in statement for statement in model.statements)
    ]
    event["outputs"] = [{"namespace": source, "name": f"awsdatacatalog.{PROJECT}.{model.name}"}]


def _manifest(executed: _AttemptRun) -> dict[str, Any]:
    manifest = _template("manifest", {"reference_workload": PROJECT})
    metadata = manifest["metadata"]
    metadata.update(
        invocation_id=executed.attempt.invocation_id,
        generated_at=_time(executed.started),
        invocation_started_at=_time(executed.started),
        run_started_at=_time(executed.started),
    )
    (node,) = manifest["nodes"].values()
    manifest["nodes"] = {f"model.{PROJECT}.{name}": _node(node, name) for name in MODELS}
    return manifest


def _node(template: dict[str, Any], name: str) -> dict[str, Any]:
    node = copy.deepcopy(template)
    sql = MODELS[name][-1]
    node.update(
        unique_id=f"model.{PROJECT}.{name}",
        name=name,
        alias=name,
        fqn=[PROJECT, name],
        path=f"{name}.sql",
        original_file_path=f"models/{name}.sql",
        relation_name=f'"awsdatacatalog"."{PROJECT}"."{name}"',
        raw_code=sql,
        compiled_code=sql,
        refs=[],
        depends_on={**node.get("depends_on", {}), "nodes": []},
    )
    return node


def _run_results(executed: _AttemptRun) -> dict[str, Any]:
    document = _template("run_results", {"reference_workload": PROJECT})
    (result,) = document["results"]
    finished = executed.finished or executed.started
    document["metadata"].update(
        invocation_id=executed.attempt.invocation_id,
        generated_at=_time(finished),
        invocation_started_at=_time(executed.started),
    )
    ran = {m.model.name: m for m in executed.models}
    document["results"] = [
        _result(result, model, ran.get(model.name), executed.started)
        for model in executed.attempt.models
    ]
    document["elapsed_time"] = (finished - executed.started).total_seconds()
    return document


def _result(
    template: dict[str, Any], model: PlannedModel, ran: _ModelRun | None, fallback: datetime
) -> dict[str, Any]:
    result = copy.deepcopy(template)
    started = ran.started if ran else fallback
    finished = ran.finished if ran else fallback
    status = ran.status if ran else "skipped"
    result.update(
        unique_id=model.node_id,
        status=status,
        execution_time=(finished - started).total_seconds(),
        relation_name=f'"awsdatacatalog"."{PROJECT}"."{model.name}"',
        timing=[
            {"name": "compile", "started_at": _time(started), "completed_at": _time(started)},
            {"name": "execute", "started_at": _time(started), "completed_at": _time(finished)},
        ]
        if ran
        else [],
    )
    result["adapter_response"] = {
        **result.get("adapter_response", {}),
        "code": "OK" if status == "success" else "ERROR",
        "data_scanned_in_bytes": ran.scanned if ran else 0,
    }
    return result
