"""The CLI's reads through `main(argv)` (#288, #289; spec 10 §Targets and §Command catalog).

The oracle is the HTTP API: each read with `--format json` must give the same JSON as its
route on the same Database (Q163). No business numbers are asserted here.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from fixture_database import fill

from data_finops.api import create_app
from data_finops.cli import main
from data_finops.contracts.dimensions import CostDimensions


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("cli-reads") / "data-finops.sqlite"
    fill(path)
    return path


@pytest.fixture(scope="module")
def client(database: Path) -> TestClient:
    return TestClient(create_app(database))


def _json_of(argv: list[str], capsys: pytest.CaptureFixture[str]) -> object:
    assert main([*argv, "--format", "json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_summary_against_a_database_gives_the_json_of_its_route(
    database: Path, client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    expected = client.get("/v1/costs/summary").json()
    assert expected["summaries"]  # the fixture is calculated: not an empty comparison
    assert _json_of(["summary", "--db", str(database)], capsys) == expected


class _Endpoint:
    """A stubbed Deployment API on loopback: it records each request and answers `body`."""

    def __init__(self, body: object, status: int = 200) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                endpoint.requests.append((self.path, dict(self.headers.items())))
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home folder with an AWS profile `operator` whose keys are fake."""
    for variable in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(variable, str(tmp_path))
    for variable in (
        "AWS_PROFILE",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.delenv(variable, raising=False)
    credentials = tmp_path / "aws-credentials"
    credentials.write_text(
        "[operator]\naws_access_key_id = AKIDEXAMPLE\n"
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    return tmp_path


def _deployment(home: Path, api_url: str) -> None:
    folder = home / ".data-finops"
    folder.mkdir(exist_ok=True)
    (folder / "deployments.toml").write_text(
        f'[prod]\napi_url = "{api_url}"\nregion = "us-east-1"\nprofile = "operator"\n',
        encoding="utf-8",
    )


def test_summary_against_a_deployment_is_signed_and_prints_the_response(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = {"summaries": [], "uncalculated_scopes": []}
    endpoint = _Endpoint(body)
    try:
        _deployment(home, endpoint.url)
        argv = ["summary", "--deployment", "prod", "--billing-account-id", "123456789012"]
        assert _json_of(argv, capsys) == body
    finally:
        endpoint.close()
    ((path, headers),) = endpoint.requests
    assert path == "/v1/costs/summary?cost_basis=BilledCost&billing_account_id=123456789012"
    # SigV4 for the Deployment's API Gateway (spec 10 §The API): service `execute-api`.
    authorization = headers["Authorization"]
    assert authorization.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "/us-east-1/execute-api/aws4_request" in authorization
    assert "SignedHeaders=" in authorization and "Signature=" in authorization
    assert "X-Amz-Date" in headers


@pytest.mark.parametrize(
    "targets", [[], ["--db", "data-finops.sqlite", "--deployment", "prod"]], ids=["neither", "both"]
)
def test_a_read_needs_exactly_one_target(
    targets: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["summary", *targets]) == 2
    assert "--db" in capsys.readouterr().err


DIMENSIONS = CostDimensions.from_mapping(
    {"dimensions": {"team": {"sources": ["workgroup_map"], "workgroup_map": {"demo": "analytics"}}}}
)


@pytest.fixture(scope="module")
def dimensioned(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("cli-reads-dimensions") / "data-finops.sqlite"
    fill(path, dimensions=DIMENSIONS)
    return path


def _reads(client: TestClient) -> list[tuple[list[str], str, dict[str, str]]]:
    """Each read as (argv, route, query): the route is the oracle (#289)."""
    ref = client.get("/v1/allocations").json()["items"][0]["ref"]
    run_id = client.get("/v1/lineage").json()["items"][0]["run_id"]
    return [
        (["allocations"], "/v1/allocations", {}),
        (
            ["allocations", "--category", "direct", "--limit", "2", "--offset", "1"],
            "/v1/allocations",
            {"category": "direct", "limit": "2", "offset": "1"},
        ),
        (["rollups", "--level", "workload_run"], "/v1/rollups", {"level": "workload_run"}),
        (
            ["reconciliation", "--cost-basis", "BilledCost"],
            "/v1/reconciliation",
            {"cost_basis": "BilledCost"},
        ),
        (["provenance"], "/v1/provenance", {}),
        (["lineage"], "/v1/lineage", {}),
        (["lineage", "--run-id", run_id], "/v1/lineage", {"run_id": run_id}),
        (["explain", ref], f"/v1/allocations/{quote(ref, safe='')}/explanation", {}),
    ]


def test_each_read_against_a_database_gives_the_json_of_its_route(
    database: Path, client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    for argv, route, query in _reads(client):
        response = client.get(route, params=query)
        assert response.status_code == 200, argv
        assert _json_of([*argv, "--db", str(database)], capsys) == response.json(), argv


def test_rollups_of_a_dimension_give_the_json_of_its_route(
    dimensioned: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    expected = TestClient(create_app(dimensioned)).get("/v1/rollups/dimensions/team").json()
    assert expected["items"]
    argv = ["rollups", "--dimension", "team", "--db", str(dimensioned)]
    assert _json_of(argv, capsys) == expected


def test_a_read_prints_a_table_by_default(
    database: Path, client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    # One smoke case (Q163): the layout is not tested beyond this.
    run_id = client.get("/v1/allocations").json()["calculation_run_id"]
    assert main(["allocations", "--db", str(database), "--limit", "3"]) == 0
    out = capsys.readouterr().out
    assert not out.lstrip().startswith("{")
    assert run_id in out
    assert len([line for line in out.splitlines() if f"{run_id}#" in line]) == 3


@pytest.mark.parametrize("page", [["--limit", "0"], ["--limit", "1001"], ["--offset", "-1"]])
def test_a_page_outside_the_route_limits_exits_with_2(
    database: Path, page: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["allocations", "--db", str(database), *page]) == 2
    assert page[0] in capsys.readouterr().err


@pytest.mark.parametrize(
    "api_url", ["http://example.com/", "http://localhost/", "ftp://127.0.0.1/"]
)
def test_a_deployment_must_use_https_off_loopback(
    home: Path, api_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # `localhost` is a name, not a loopback address: it could resolve anywhere (ADR 0024).
    _deployment(home, api_url)
    assert main(["summary", "--deployment", "prod"]) == 2
    assert "api_url must use https" in capsys.readouterr().err
