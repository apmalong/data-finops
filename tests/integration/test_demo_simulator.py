"""The simulated orchestrator (#226; spec 09 §The simulated orchestrator, seam 1): with botocore's
Stubber for Athena and S3 and a fixed seed, everything it writes passes the current readers,
each query starts with a Trusted Query Comment, and once ingested each Query Execution links to
its Model Execution and on to the DAG run."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aws_stubs import ACCOUNT, REGION, athena_client, s3_client, stubbed
from botocore.stub import ANY, Stubber

from data_finops.contracts.workload import LinkOutcome, WorkloadRunStatus
from data_finops.identity.links import query_comment, trusted_query_comment
from data_finops.lambda_handlers import (
    DEPLOYMENT_BUCKET,
    SimulatorMisconfigured,
    make_simulator_handler,
)
from data_finops.public_demo.simulator import DagRun, plan, simulate
from data_finops.workload.store import ArtifactOutcome, WorkloadStore

BUCKET = "example-deployment-bucket"
MONDAY = datetime(2026, 11, 16, 0, 25, tzinfo=UTC)
"""A Monday's first start: all three DAGs run."""


class _Clock:
    """A clock that moves two seconds at each reading."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=2)
        return self.now


def _statements(runs: tuple[DagRun, ...]) -> list[tuple[str, str]]:
    """Each statement sent, with the state Athena gives it: a failing model's fails."""
    return [
        (statement, "SUCCEEDED" if model.status == "success" else "FAILED")
        for run in runs
        for attempt in run.attempts
        for model in attempt.models
        if model.status != "skipped"
        for statement in model.statements
    ]


def _attempts(runs: tuple[DagRun, ...]) -> int:
    return sum(len(run.attempts) for run in runs)


def _expect_queries(stub: Stubber, statements: list[tuple[str, str]]) -> None:
    for number, (statement, state) in enumerate(statements):
        stub.add_response(
            "start_query_execution",
            {"QueryExecutionId": f"q-{number:03d}"},
            {
                "QueryString": statement,
                "WorkGroup": "data-finops-demo",
                "QueryExecutionContext": {"Database": "data_finops_demo"},
            },
        )
        stub.add_response(
            "get_query_execution",
            {
                "QueryExecution": {
                    "QueryExecutionId": f"q-{number:03d}",
                    "Status": {"State": state},
                    "Statistics": {"DataScannedInBytes": 1_000_000 * (number + 1)},
                }
            },
            {"QueryExecutionId": f"q-{number:03d}"},
        )


Simulated = tuple[tuple[DagRun, ...], dict[str, bytes], list[tuple[str, str]]]


def _simulate(start: datetime) -> Simulated:
    """Simulate one start against stubbed clients; what it wrote, by key."""
    runs = plan(start)
    statements = _statements(runs)
    written: dict[str, bytes] = {}
    s3, athena = s3_client(), athena_client()
    s3.meta.events.register(
        "provide-client-params.s3.PutObject",
        lambda params, **_: written.__setitem__(params["Key"], params["Body"]),
    )
    with stubbed(s3) as s3_stub, stubbed(athena) as athena_stub:
        _expect_queries(athena_stub, statements)
        for _ in range(len(runs) + 2 * _attempts(runs)):  # events, then two artifacts each
            s3_stub.add_response(
                "put_object", {"ETag": '"e"'}, {"Bucket": BUCKET, "Key": ANY, "Body": ANY}
            )
        summary = simulate(
            start, athena=athena, s3=s3, bucket=BUCKET, clock=_Clock(start), sleep=lambda _: None
        )
    assert summary["queries"] == len(statements)
    assert summary["failed_dag_runs"] == sum(not run.succeeded for run in runs)
    return runs, written, statements


def _ingest(store: WorkloadStore, folder: Path, simulated: Simulated) -> None:
    """Ingest every file through the current readers, asserting that none is rejected."""
    _, written, statements = simulated
    for number, (key, body) in enumerate(sorted(written.items())):
        path = folder / f"{number}-{Path(key).name}"
        path.write_bytes(body)
        if key.startswith("inbox/openlineage/"):
            result = store.ingest_events(path)
            assert not result.rejected and not result.unsupported, key
        else:
            assert store.ingest_dbt_artifact(path).outcome is not ArtifactOutcome.REJECTED, key
    history = folder / "history.json"
    history.write_text(json.dumps(_history(statements)), encoding="utf-8")
    store.ingest_query_history(history, account_id=ACCOUNT, region=REGION)


@pytest.fixture(scope="module")
def simulated() -> Simulated:
    return _simulate(MONDAY)


@pytest.fixture(scope="module")
def store(
    tmp_path_factory: pytest.TempPathFactory, simulated: Simulated
) -> Iterator[WorkloadStore]:
    folder = tmp_path_factory.mktemp("simulated")
    with WorkloadStore(folder / "data-finops.sqlite") as opened:
        _ingest(opened, folder, simulated)
        yield opened


def _first_start(condition: Any) -> datetime:
    return next(
        start
        for start in (MONDAY + timedelta(hours=6 * n) for n in range(1000))
        if any(condition(run) for run in plan(start))
    )


def test_failed_and_retried_tasks_are_recorded_as_failed(tmp_path: Path) -> None:
    retried = _first_start(lambda run: any(a.try_number == 2 for a in run.attempts))
    broken = _first_start(lambda run: not run.succeeded)
    with WorkloadStore(tmp_path / "data-finops.sqlite") as store:
        for number, start in enumerate((retried, broken)):
            (tmp_path / str(number)).mkdir()
            runs, written, statements = _simulate(start)
            _ingest(store, tmp_path / str(number), (runs, written, statements))
            for run in runs:
                dag = store.workload_run(run.run_id)
                expected = (
                    WorkloadRunStatus.COMPLETED if run.succeeded else WorkloadRunStatus.FAILED
                )
                assert dag is not None and dag.status is expected
                for attempt in run.attempts:
                    task = store.workload_run(attempt.run_id)
                    want = (
                        WorkloadRunStatus.COMPLETED
                        if attempt.succeeded
                        else WorkloadRunStatus.FAILED
                    )
                    assert task is not None and task.status is want
                    reported = {
                        e.node_id: e.status for e in store.model_executions(attempt.invocation_id)
                    }
                    assert reported == {m.node_id: m.status for m in attempt.models}
            assert any(not run.succeeded for run in runs) or number == 0


def _history(statements: list[tuple[str, str]]) -> dict[str, Any]:
    """What BatchGetQueryExecution would answer for the stubbed queries."""
    return {
        "QueryExecutions": [
            {
                "QueryExecutionId": f"q-{number:03d}",
                "Query": statement,
                "StatementType": "DML",
                "WorkGroup": "data-finops-demo",
                "Status": {
                    "State": "SUCCEEDED",
                    "SubmissionDateTime": "2026-11-16T00:26:00Z",
                    "CompletionDateTime": "2026-11-16T00:26:05Z",
                },
                "Statistics": {"DataScannedInBytes": 1_000_000 * (number + 1)},
            }
            for number, (statement, _) in enumerate(statements)
        ],
        "UnprocessedQueryExecutionIds": [],
    }


def test_a_monday_first_start_runs_every_dag() -> None:
    assert [run.dag for run in plan(MONDAY)] == ["daily_orders", "hourly_events", "weekly_finance"]
    assert [run.dag for run in plan(MONDAY + timedelta(days=1, hours=6))] == ["hourly_events"]


def test_the_same_seed_and_start_give_the_same_plan() -> None:
    assert plan(MONDAY) == plan(MONDAY)
    assert plan(MONDAY) != plan(MONDAY, seed=1)


def test_some_tasks_fail_and_succeed_on_retry_and_some_runs_fail() -> None:
    starts = [MONDAY + timedelta(hours=6 * n) for n in range(400)]
    runs = [run for start in starts for run in plan(start)]
    tasks = [(run, attempt) for run in runs for attempt in run.attempts]
    retried = [a for _, a in tasks if a.try_number == 2]
    assert 0.02 < len(retried) / len(tasks) < 0.10
    for run in runs:  # a retry follows a failed first try of the same task
        for before, after in zip(run.attempts, run.attempts[1:], strict=False):
            if after.try_number == 2:
                assert (before.task, before.try_number, before.succeeded) == (after.task, 1, False)
    failed = [run for run in runs if not run.succeeded]
    assert 0 < len(failed) / len(runs) < 0.05


def test_a_failing_model_sends_one_query_that_names_a_missing_column() -> None:
    from data_finops.public_demo.dataset import SCHEMAS

    runs = [run for n in range(400) for run in plan(MONDAY + timedelta(hours=6 * n))]
    failing = [m for r in runs for a in r.attempts for m in a.models if m.status == "error"]
    assert failing
    for model in failing:
        (statement,) = model.statements
        sql = statement.partition("\n")[2]
        column, _, table = sql.removeprefix("select ").partition(" from ")
        assert column not in SCHEMAS[table].names


def test_every_query_starts_with_a_trusted_query_comment(
    simulated: Simulated,
) -> None:
    runs, _, statements = simulated
    invocations = {attempt.invocation_id for run in runs for attempt in run.attempts}
    for statement, _ in statements:
        key = trusted_query_comment(statement)
        assert key is not None and key.invocation_id in invocations
        assert key.node_id.startswith("model.demo_shop.")
        claims = query_comment(statement)
        assert claims is not None and {"team", "tenant_id", "dag_run_id"} <= set(claims)


def test_every_record_passes_the_readers(store: WorkloadStore) -> None:
    # The `store` fixture ingests each file and asserts that nothing was rejected.
    assert store.root_runs()


def test_each_query_links_to_its_model_execution_and_dag_run(
    simulated: Simulated, store: WorkloadStore
) -> None:
    runs, _, statements = simulated
    dag_runs = {run.run_id for run in runs}
    for number in range(len(statements)):
        link = store.model_execution_of_query(ACCOUNT, REGION, f"q-{number:03d}")
        assert link is not None and link.outcome is LinkOutcome.LINKED, number
        invocation, node = json.loads(link.link.target_id)  # type: ignore[union-attr]
        run = store.workload_run_of_invocation(invocation)
        assert run is not None and run.outcome is LinkOutcome.LINKED
        dbt_run = store.workload_run(run.link.target_id)  # type: ignore[union-attr]
        assert dbt_run is not None and dbt_run.root_run_id in dag_runs
        assert node.startswith("model.demo_shop.")


def test_each_model_run_links_to_its_model_execution(
    simulated: Simulated, store: WorkloadStore
) -> None:
    runs, _, _ = simulated
    for run in runs:
        for attempt in run.attempts:
            model_runs = store.children(attempt.dbt_run_id)
            ran = [m for m in attempt.models if m.status != "skipped"]
            assert len(model_runs) == len(ran)
            for model_run in model_runs:
                link = store.model_execution_of_run(model_run.run_id)
                assert link is not None and link.outcome is LinkOutcome.LINKED


def test_the_dag_run_task_runs_and_invocations_form_one_tree(
    simulated: Simulated, store: WorkloadStore
) -> None:
    runs, _, _ = simulated
    for run in runs:
        assert {c.run_id for c in store.children(run.run_id)} == {a.run_id for a in run.attempts}
        for attempt in run.attempts:
            assert [c.run_id for c in store.children(attempt.run_id)] == [attempt.dbt_run_id]


def test_the_handler_plans_the_scheduled_time_of_its_event() -> None:
    tuesday = MONDAY + timedelta(days=1, hours=6)
    runs = plan(tuesday)
    written: dict[str, bytes] = {}
    s3, athena = s3_client(), athena_client()
    s3.meta.events.register(
        "provide-client-params.s3.PutObject",
        lambda params, **_: written.__setitem__(params["Key"], params["Body"]),
    )
    with stubbed(s3) as s3_stub, stubbed(athena) as athena_stub:
        _expect_queries(athena_stub, _statements(runs))
        for _ in range(len(runs) + 2 * _attempts(runs)):
            s3_stub.add_response(
                "put_object", {"ETag": '"e"'}, {"Bucket": BUCKET, "Key": ANY, "Body": ANY}
            )
        handler = make_simulator_handler(
            lambda: (athena, s3),
            {DEPLOYMENT_BUCKET: BUCKET},
            clock=_Clock(tuesday),
            sleep=lambda _: None,
        )
        result = handler({"scheduled_time": "2026-11-17T06:25:00Z"}, None)

    assert result["dag_runs"] == 1
    assert [k for k in written if k.startswith("inbox/openlineage/")] == [
        f"inbox/openlineage/hourly_events/{runs[0].run_id}.jsonl"
    ]


@pytest.mark.parametrize(
    ("environ", "event", "named"),
    [
        ({}, {"scheduled_time": "2026-11-17T06:25:00Z"}, DEPLOYMENT_BUCKET),
        ({DEPLOYMENT_BUCKET: BUCKET}, {"scheduled_time": "secret-not-a-time"}, "scheduled_time"),
    ],
)
def test_a_wrong_environment_or_event_is_named(
    environ: dict[str, str], event: dict[str, str], named: str
) -> None:
    handler = make_simulator_handler(
        lambda: (athena_client(), s3_client()), environ, clock=_Clock(MONDAY), sleep=lambda _: None
    )
    with pytest.raises(SimulatorMisconfigured, match=named) as raised:
        handler(event, None)
    assert "secret" not in str(raised.value)


def test_a_query_that_runs_too_long_is_stopped_and_fails_its_model() -> None:
    from data_finops.public_demo import simulator

    athena = athena_client()
    with stubbed(athena) as stub:
        stub.add_response("start_query_execution", {"QueryExecutionId": "q-slow"})
        for _ in range(simulator.MAX_POLLS):
            running = {"QueryExecutionId": "q-slow", "Status": {"State": "RUNNING"}}
            stub.add_response(
                "get_query_execution", {"QueryExecution": running}, {"QueryExecutionId": "q-slow"}
            )
        stub.add_response("stop_query_execution", {}, {"QueryExecutionId": "q-slow"})
        state = simulator._run_query(athena, "select 1", sleep=lambda _: None)

    assert state == ("CANCELLED", 0)
