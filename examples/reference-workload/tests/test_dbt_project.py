"""The dbt project offline (seam B agreed in #21): no AWS access, no Athena run."""

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from dbt.adapters.base.impl import BaseAdapter
from dbt.adapters.factory import FACTORY
from dbt.cli.main import dbtRunner
from dbt.context.query_header import generate_query_header_context
from dbt.contracts.graph.manifest import Manifest
from openlineage.client import OpenLineageClient
from openlineage.client.transport.file import FileTransport

import infra

WORKLOAD = Path(__file__).resolve().parents[1]
PROJECT = WORKLOAD / "dbt"
REPO_LOCAL = WORKLOAD.parents[1] / ".local"


@pytest.fixture(scope="module")
def manifest(tmp_path_factory: pytest.TempPathFactory) -> Manifest:
    with pytest.MonkeyPatch.context() as env:
        env.setenv("REFERENCE_WORKLOAD_BUCKET", "example-bucket")
        result = dbtRunner().invoke(
            [
                "parse",
                "--project-dir", str(PROJECT),
                "--profiles-dir", str(PROJECT),
                "--target-path", str(tmp_path_factory.mktemp("target")),
                "--quiet",
            ]
        )  # fmt: skip
    assert result.success, result.exception
    assert isinstance(result.result, Manifest)
    return result.result


def _as_sent_to_athena(manifest: Manifest, node_id: str, sql: str) -> str:
    """The SQL text the Athena adapter would send for a node, comment included.

    Uses dbt's own query-header machinery (internal API, pinned dbt-core version) so the
    test sees exactly what Athena would receive.
    """
    adapter = cast(BaseAdapter, FACTORY.adapters["athena"])
    connections = adapter.connections
    connections.set_query_header(generate_query_header_context(adapter.config, manifest))
    assert connections.query_header is not None
    connections.query_header.set(node_id, manifest.nodes[node_id])
    return connections._add_query_comment(sql)


def _query_comment(manifest: Manifest, node_id: str) -> dict[str, Any]:
    sql = _as_sent_to_athena(manifest, node_id, "select 1")
    found = re.match(r"-- /\* (?P<json>.*) \*/\nselect 1$", sql)
    assert found, sql
    comment: dict[str, Any] = json.loads(found["json"])
    return comment


def test_parses_offline_to_a_v12_manifest(manifest: Manifest) -> None:
    assert manifest.metadata.dbt_schema_version.endswith("/manifest/v12.json")


@pytest.mark.parametrize(
    "node_id",
    ["model.reference_workload.daily_orders", "seed.reference_workload.raw_orders"],
)
def test_query_comment_names_the_node_and_invocation(manifest: Manifest, node_id: str) -> None:
    comment = _query_comment(manifest, node_id)
    assert comment["node_id"] == node_id
    assert comment["invocation_id"] == manifest.metadata.invocation_id


@pytest.mark.parametrize(
    "statement",
    ["drop table t", "alter table t add columns (c int)", "optimize t", "msck repair table t"],
)
def test_adapter_sends_some_statements_without_a_comment(
    manifest: Manifest, statement: str
) -> None:
    """Known gap (OQ-22): not correlatable by comment. Fails if dbt-athena changes this."""
    sent = _as_sent_to_athena(manifest, "model.reference_workload.daily_orders", statement)
    assert sent == statement


def test_daily_orders_is_incremental(manifest: Manifest) -> None:
    """Incremental runs after the first issue several queries for one model; #24 observes it."""
    incremental = [n for n in manifest.nodes.values() if n.config.materialized == "incremental"]
    assert [n.unique_id for n in incremental] == ["model.reference_workload.daily_orders"]


def test_profile_targets_the_stacks_workgroup_database_and_region() -> None:
    profile = yaml.safe_load((PROJECT / "profiles.yml").read_text(encoding="utf-8"))
    target = profile["reference_workload"]["outputs"]["reference"]
    assert (target["work_group"], target["schema"], target["region_name"]) == (
        infra.WORKGROUP,
        infra.DATABASE,
        infra.REGION,
    )


def test_openlineage_transport_writes_under_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(PROJECT)  # dbt-ol runs from the project folder
    for name in ("OPENLINEAGE_CONFIG", "OPENLINEAGE_URL", "OPENLINEAGE_DISABLED"):
        monkeypatch.delenv(name, raising=False)
    transport = OpenLineageClient().transport
    assert isinstance(transport, FileTransport)
    assert Path(transport.config.log_file_path).resolve().is_relative_to(REPO_LOCAL.resolve())
