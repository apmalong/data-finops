"""Reference workload output anonymizer through its public interface (#32).

All identifiers below are invented or AWS documentation placeholders.
"""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from data_finops.dbt.artifacts import load_manifest, load_run_results
from data_finops.lineage.openlineage import load_events
from data_finops.providers.athena import load_query_executions

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))  # the script imports Pseudonyms from its sibling
_spec = importlib.util.spec_from_file_location(
    "anonymize_workload_outputs", ROOT / "scripts" / "anonymize_workload_outputs.py"
)
assert _spec and _spec.loader
anonymizer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(anonymizer)
from anonymize_focus_export import Pseudonyms  # noqa: E402

SECRET = "test-secret"
ACCOUNT = "111122223333"
BUCKET = "example-workload-bucket"
USER_ID = "4c1f6a0e-0000-4000-8000-00000000abcd"
WINDOWS_ROOT = r"C:\Users\someone\projects\workload\dbt"
POSIX_ROOT = "/home/someone/projects/workload/dbt"
INVOCATION = "6f1e0c3a-1111-4222-8333-444455556666"
NODE = "model.example.daily_orders"
QUERY_ID = "a1b2c3d4-5678-90ab-cdef-EXAMPLE11111"
COMMENT = json.dumps({"app": "dbt", "invocation_id": INVOCATION, "node_id": NODE})
WORKGROUP = "example-workgroup"


def _athena() -> dict[str, Any]:
    execution = {
        "QueryExecutionId": QUERY_ID,
        "Query": f"-- /* {COMMENT} */\ncreate table example.t with (external_location="
        f"'s3://{BUCKET}/tables/t') as select 'customer@example.com' as email",
        "StatementType": "DDL",
        "WorkGroup": WORKGROUP,
        "ResultConfiguration": {"OutputLocation": f"s3://{BUCKET}/athena-results/{QUERY_ID}.txt"},
        "Status": {"State": "SUCCEEDED", "SubmissionDateTime": "2026-11-01T21:19:15+00:00"},
        "Statistics": {
            "DataScannedInBytes": 397,
            "DataManifestLocation": f"s3://{BUCKET}/athena-results/{QUERY_ID}-manifest.csv",
        },
        "SomeFutureField": "unknown",
    }
    uncommented = {
        **execution,
        "QueryExecutionId": "a1b2c3d4-5678-90ab-cdef-EXAMPLE22222",
        "Query": "alter table `example`.`t` set tblproperties ('k'='v')",
    }
    return {"QueryExecutions": [execution, uncommented], "UnprocessedQueryExecutionIds": []}


def _metadata(kind: str) -> dict[str, Any]:
    return {
        "dbt_schema_version": f"https://schemas.getdbt.com/dbt/{kind}.json",
        "dbt_version": "1.12.6",
        "generated_at": "2026-11-01T21:19:55Z",
        "invocation_id": INVOCATION,
        "env": {"DBT_CLOUD_PROJECT": "secret-project"},
        "user_id": USER_ID,
    }


def _manifest() -> dict[str, Any]:
    node = {
        "unique_id": NODE,
        "resource_type": "model",
        "name": "daily_orders",
        "fqn": ["example", "marts", "daily_orders"],
        "root_path": POSIX_ROOT,
        "original_file_path": "models/marts/daily_orders.sql",
    }
    macro = {"unique_id": "macro.example.m", "root_path": WINDOWS_ROOT, "macro_sql": "select 1"}
    return {
        "metadata": _metadata("manifest/v12"),
        "nodes": {NODE: node},
        "macros": {"macro.example.m": macro},
    }


def _run_results() -> dict[str, Any]:
    return {
        "metadata": _metadata("run-results/v6"),
        "results": [{"unique_id": NODE, "status": "success", "execution_time": 8.1}],
        "elapsed_time": 21.0,
        "args": {
            "which": "run",
            "select": [],
            "threads": 1,
            "partial_parse": True,
            "project_dir": POSIX_ROOT,
            "invocation_command": f"dbt run --project-dir {POSIX_ROOT}",
            "vars": {"api_token": "s3cr3t"},
            "some_future_text_flag": "unknown",
        },
    }


def _events() -> list[dict[str, Any]]:
    dataset = {
        "namespace": "awsathena://athena.us-east-1.amazonaws.com",
        "name": "awsdatacatalog.example.daily_orders",
        "facets": {
            "symlinks": {
                "identifiers": [
                    {
                        "namespace": f"arn:aws:glue:us-east-1:{ACCOUNT}",
                        "name": "table/example/daily_orders",
                        "type": "TABLE",
                    }
                ]
            },
            "dataSource": {"name": f"s3://{BUCKET}", "uri": f"s3://{BUCKET}/tables/daily_orders"},
        },
    }
    event = {
        "eventType": "COMPLETE",
        "eventTime": "2026-11-01T21:19:47Z",
        "producer": "https://github.com/OpenLineage/OpenLineage/tree/1.54.0/integration/dbt",
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
        "run": {
            "runId": "0190f4a1-7a5e-7c1e-9f3a-1b2c3d4e5f60",
            "facets": {"dbt_run": {"invocation_id": INVOCATION}},
        },
        "job": {"namespace": "dbt", "name": "awsdatacatalog.example.daily_orders"},
        "outputs": [dataset],
    }
    return [event]


@pytest.fixture
def outputs(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    for relative, document in [
        ("athena/query-executions.json", _athena()),
        ("dbt/2-run/manifest.json", _manifest()),
        ("dbt/2-run/run_results.json", _run_results()),
    ]:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")
    (source / "openlineage").mkdir()
    (source / "openlineage/dbt-events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in _events()), encoding="utf-8"
    )
    (source / "notes.txt").write_text(f"bucket {BUCKET}", encoding="utf-8")
    return source, tmp_path / "anonymized"


@pytest.fixture
def anonymized(outputs: tuple[Path, Path]) -> tuple[Path, dict[str, int]]:
    source, target = outputs
    return target, anonymizer.anonymize_workload_outputs(source, target, SECRET)


def _all_text(target: Path) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(target.rglob("*")) if p.is_file()
    )


def _json(target: Path, relative: str) -> Any:
    return json.loads((target / relative).read_text(encoding="utf-8"))


def test_every_identifier_class_is_replaced(anonymized: tuple[Path, dict[str, int]]) -> None:
    text = _all_text(anonymized[0])
    for original in [ACCOUNT, BUCKET, USER_ID, "someone", "secret-project", "s3cr3t"]:
        assert original not in text
    assert "customer@example.com" not in text  # literals in query text are not kept


def test_pseudonyms_match_across_sources_and_the_focus_anonymizer(
    anonymized: tuple[Path, dict[str, int]],
) -> None:
    target, _ = anonymized
    pseudonyms = Pseudonyms(SECRET)
    bucket = pseudonyms.text(BUCKET)
    athena = _json(target, "athena/query-executions.json")["QueryExecutions"][0]
    assert athena["ResultConfiguration"]["OutputLocation"].startswith(f"s3://{bucket}/")
    event = load_events(target / "openlineage/dbt-events.jsonl")[0]
    facets = event.outputs[0].model_dump()["facets"]
    assert facets["dataSource"]["name"] == f"s3://{bucket}"
    glue = facets["symlinks"]["identifiers"][0]["namespace"]
    assert glue == f"arn:aws:glue:us-east-1:{pseudonyms.account(ACCOUNT)}"


def test_correlation_fields_still_link_the_sources(
    anonymized: tuple[Path, dict[str, int]],
) -> None:
    target, _ = anonymized
    history = load_query_executions(
        target / "athena/query-executions.json", account_id=ACCOUNT, region="us-east-1"
    )
    first = history.QueryExecutions[0]
    assert (first.QueryExecutionId, first.WorkGroup) == (QUERY_ID, WORKGROUP)
    comment = json.loads(first.Query.split("/* ", 1)[1].split(" */", 1)[0])
    assert (comment["node_id"], comment["invocation_id"]) == (NODE, INVOCATION)
    assert NODE in load_manifest(target / "dbt/2-run/manifest.json").nodes
    assert (
        load_run_results(target / "dbt/2-run/run_results.json").metadata.invocation_id == INVOCATION
    )
    event = load_events(target / "openlineage/dbt-events.jsonl")[0]
    assert event.run.facets["dbt_run"]["invocation_id"] == INVOCATION


def test_query_text_keeps_the_comment_and_leading_keywords_only(
    anonymized: tuple[Path, dict[str, int]],
) -> None:
    executions = _json(anonymized[0], "athena/query-executions.json")["QueryExecutions"]
    assert executions[0]["Query"] == f"-- /* {COMMENT} */\ncreate table …"
    assert executions[1]["Query"] == "alter table …"


def test_unknown_fields_are_dropped_and_reported(anonymized: tuple[Path, dict[str, int]]) -> None:
    target, summary = anonymized
    athena = _json(target, "athena/query-executions.json")["QueryExecutions"][0]
    args = _json(target, "dbt/2-run/run_results.json")["args"]
    assert "SomeFutureField" not in athena
    assert set(args) == {"which", "select", "threads", "partial_parse"}
    for field in ["athena.SomeFutureField", "dbt.args.vars", "dbt.args.some_future_text_flag"]:
        assert summary[f"dropped {field}"] >= 1
    assert _json(target, "dbt/2-run/manifest.json")["metadata"]["user_id"] is None


def test_other_files_are_skipped_and_reported(anonymized: tuple[Path, dict[str, int]]) -> None:
    target, summary = anonymized
    assert not (target / "notes.txt").exists()
    assert summary[anonymizer.SKIPPED] == 1


def test_pseudonyms_are_deterministic_for_the_same_secret(outputs: tuple[Path, Path]) -> None:
    source, target = outputs
    anonymizer.anonymize_workload_outputs(source, target / "a", SECRET)
    anonymizer.anonymize_workload_outputs(source, target / "b", SECRET)
    assert _all_text(target / "a") == _all_text(target / "b")


def test_refuses_to_write_inside_the_repository_outside_local(outputs: tuple[Path, Path]) -> None:
    with pytest.raises(ValueError, match=".local"):
        anonymizer.anonymize_workload_outputs(outputs[0], ROOT / "fixtures" / "anon", SECRET)


def test_requires_a_secret(outputs: tuple[Path, Path]) -> None:
    with pytest.raises(ValueError, match="secret"):
        anonymizer.anonymize_workload_outputs(*outputs, "")


def test_run_fails_rather_than_leak_a_value_found_elsewhere(outputs: tuple[Path, Path]) -> None:
    source, target = outputs
    manifest_path = source / "dbt/2-run/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["nodes"][NODE]["alias"] = "secret-project"  # the env value again, in a kept field
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="remain"):
        anonymizer.anonymize_workload_outputs(source, target, SECRET)


def _edit(path: Path, change: Any) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document)
    path.write_text(json.dumps(document), encoding="utf-8")


LITERAL = "customer@example.com"
HOSTED_BUCKET = "other-hosted-bucket"


def _plant_review_cases(source: Path) -> None:
    def manifest(document: dict[str, Any]) -> None:
        node = document["nodes"][NODE]
        node["raw_code"] = f"select '{LITERAL}' as email"
        node["compiled_code"] = f"select '{LITERAL}' as email"
        document["macros"]["macro.example.m"]["macro_sql"] = f"select '{LITERAL}'"
        document["metadata"]["project_id"] = "0123456789abcdef0123456789abcdef"

    def run_results(document: dict[str, Any]) -> None:
        result = document["results"][0]
        result["compiled_code"] = f"select '{LITERAL}'"
        result["message"] = "wrote /home/someone/exports/out.csv and ~/notes/run.txt"
        result["adapter_response"] = {"log": "file:///home/someone/logs/dbt.log"}

    def athena(document: dict[str, Any]) -> None:
        document["QueryExecutions"][0]["Statistics"]["SomeFutureStatistic"] = 1

    _edit(source / "dbt/2-run/manifest.json", manifest)
    _edit(source / "dbt/2-run/run_results.json", run_results)
    _edit(source / "athena/query-executions.json", athena)
    events = [json.loads(line) for line in (source / "openlineage/dbt-events.jsonl").open()]
    events[0]["job"]["facets"] = {"sql": {"query": f"select '{LITERAL}' as email"}}
    events[0]["outputs"][0]["facets"]["dataSource"]["uri"] = (
        f"https://{HOSTED_BUCKET}.s3.us-east-1.amazonaws.com/tables/daily_orders"
    )
    (source / "openlineage/dbt-events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )
    named = source / "athena" / BUCKET / "history.json"  # a folder named after the bucket
    named.parent.mkdir()
    named.write_text((source / "athena/query-executions.json").read_text(encoding="utf-8"))


def test_sql_paths_hosted_buckets_and_project_id_are_replaced(outputs: tuple[Path, Path]) -> None:
    source, target = outputs
    _plant_review_cases(source)
    summary = anonymizer.anonymize_workload_outputs(source, target, SECRET)
    text = _all_text(target)
    names = " ".join(p.relative_to(target).as_posix() for p in target.rglob("*"))
    for original in [LITERAL, HOSTED_BUCKET, "someone", "~/notes", "0123456789abcdef0123456789"]:
        assert original not in text
    assert BUCKET not in names
    manifest = _json(target, "dbt/2-run/manifest.json")
    assert manifest["nodes"][NODE]["raw_code"] == "select …"
    assert summary["dropped athena.Statistics.SomeFutureStatistic"] >= 1
    statistics = _json(target, "athena/query-executions.json")["QueryExecutions"][0]["Statistics"]
    assert "SomeFutureStatistic" not in statistics


def test_command_line_reads_the_secret_from_the_environment(
    outputs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = outputs
    monkeypatch.setenv("DATA_FINOPS_ANON_SECRET", SECRET)
    assert anonymizer.main([str(source), str(target)]) == 0
    monkeypatch.delenv("DATA_FINOPS_ANON_SECRET")
    with pytest.raises(ValueError, match="secret"):
        anonymizer.main([str(source), str(target / "again")])
    assert anonymizer.main([]) == 2


# Field allowlists (#36)

UNKNOWN = "zz_unknown_field"
FREE_TEXT = ["orders by day for finance", "pii-owner", "analyst@example.com", "wrote 6 rows"]


def _plant_allowlist_cases(source: Path) -> None:
    def manifest(document: dict[str, Any]) -> None:
        node = document["nodes"][NODE]
        node[UNKNOWN] = "x"
        node["description"] = FREE_TEXT[0]
        node["meta"] = {"owner": FREE_TEXT[1]}
        node["tags"] = [FREE_TEXT[1]]
        node["config"] = {"materialized": "incremental", "grants": {"select": [FREE_TEXT[1]]}}
        document["macros"]["macro.example.m"][UNKNOWN] = "x"
        document["metadata"][UNKNOWN] = "x"
        document["docs"] = {
            "doc.example.d": {"unique_id": "doc.example.d", "block_contents": FREE_TEXT[0]}
        }
        document["groups"] = {"group.example.g": {"owner": {"email": FREE_TEXT[2]}}}

    def run_results(document: dict[str, Any]) -> None:
        result = document["results"][0]
        result[UNKNOWN] = "x"
        result["message"] = FREE_TEXT[3]

    def athena(document: dict[str, Any]) -> None:
        document["QueryExecutions"][0]["Status"]["SubmissionDateTime"] = (
            "2026-11-01T15:19:15.713-06:00"
        )

    _edit(source / "dbt/2-run/manifest.json", manifest)
    _edit(source / "dbt/2-run/run_results.json", run_results)
    _edit(source / "athena/query-executions.json", athena)
    events = [json.loads(line) for line in (source / "openlineage/dbt-events.jsonl").open()]
    event = events[0]
    event[UNKNOWN] = "x"
    event["run"]["facets"][UNKNOWN] = {"secret": FREE_TEXT[1]}
    event["run"]["facets"]["tags"] = {
        "tags": [{"key": "owner", "value": FREE_TEXT[1], "source": "DBT"}]
    }
    event["job"]["facets"] = {UNKNOWN: {"x": 1}, "dbt_node": {"unique_id": NODE}}
    event["outputs"][0]["facets"][UNKNOWN] = {"x": 1}
    (source / "openlineage/dbt-events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )


@pytest.fixture
def allowlisted(outputs: tuple[Path, Path]) -> tuple[Path, dict[str, int]]:
    source, target = outputs
    _plant_allowlist_cases(source)
    return target, anonymizer.anonymize_workload_outputs(source, target, SECRET)


@pytest.mark.parametrize(
    "path",
    [
        "manifest.nodes.*",
        "manifest.macros.*",
        "manifest.metadata",
        "run_results.results[]",
        "openlineage[]",
        "openlineage[].run.facets",
        "openlineage[].job.facets",
        "openlineage[].outputs[].facets",
    ],
)
def test_unknown_fields_at_every_level_are_dropped_and_reported(
    allowlisted: tuple[Path, dict[str, int]], path: str
) -> None:
    target, summary = allowlisted
    assert summary[f"dropped {path}.{UNKNOWN}"] == 1
    assert UNKNOWN not in _all_text(target)


def test_known_fields_keep_their_values(allowlisted: tuple[Path, dict[str, int]]) -> None:
    target, _ = allowlisted
    node = _json(target, "dbt/2-run/manifest.json")["nodes"][NODE]
    assert (node["name"], node["fqn"], node["config"]["materialized"]) == (
        "daily_orders",
        ["example", "marts", "daily_orders"],
        "incremental",
    )
    result = _json(target, "dbt/2-run/run_results.json")["results"][0]
    assert (result["unique_id"], result["status"], result["execution_time"]) == (
        NODE,
        "success",
        8.1,
    )
    event = load_events(target / "openlineage/dbt-events.jsonl")[0]
    assert event.job.facets["dbt_node"]["unique_id"] == NODE


def test_free_text_and_unread_collections_do_not_survive(
    allowlisted: tuple[Path, dict[str, int]],
) -> None:
    target, summary = allowlisted
    text = _all_text(target)
    assert not any(value in text for value in FREE_TEXT)
    manifest = _json(target, "dbt/2-run/manifest.json")
    assert manifest["nodes"][NODE]["meta"] == {}
    assert manifest["nodes"][NODE]["config"]["grants"] == {}
    assert manifest["groups"] == {} and summary["emptied manifest.groups"] == 1


def test_timestamps_with_a_utc_offset_become_utc(allowlisted: tuple[Path, dict[str, int]]) -> None:
    status = _json(allowlisted[0], "athena/query-executions.json")["QueryExecutions"][0]["Status"]
    assert status["SubmissionDateTime"] == "2026-11-01T21:19:15.713000Z"
