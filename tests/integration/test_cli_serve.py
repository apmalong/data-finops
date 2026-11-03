"""`data-finops serve` and `demo` through `main(argv)` (#116; spec 05; ADR 0024).

The server start is injected, so no test binds a socket: each asserts what would be served,
where, and what the command printed.
"""

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fixture_database import fill

from data_finops.api import TOKEN_VARIABLE
from data_finops.cli import main

TOKEN = "s3cret-token-value-0123456789"


class Recorder:
    """Stands in for the server: records the app and address it was asked to serve, and
    while "serving" GETs `probes`, as a client would before the server stops."""

    def __init__(self, *probes: str) -> None:
        self.calls: list[tuple[Any, str, int]] = []
        self.probes = probes
        self.responses: dict[str, tuple[int, Any]] = {}

    def __call__(self, app: Any, host: str, port: int) -> None:
        self.calls.append((app, host, port))
        with TestClient(app) as client:
            for path in self.probes:
                response = client.get(path)
                self.responses[path] = (response.status_code, response.json())


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "data-finops.sqlite"
    fill(path)
    return path


def test_serve_binds_loopback_by_default_and_serves_the_database(
    database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = Recorder()
    assert main(["serve", "--db", str(database)], start_server=server) == 0
    ((app, host, port),) = server.calls
    assert (host, port) == ("127.0.0.1", 8000)
    with TestClient(app) as client:
        assert client.get("/v1/costs/summary").status_code == 200
    assert "http://127.0.0.1:8000/" in capsys.readouterr().out


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.invalid"])
def test_serve_refuses_any_other_address_without_a_token(
    database: Path, host: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(TOKEN_VARIABLE, raising=False)
    server = Recorder()
    assert main(["serve", "--db", str(database), "--host", host], start_server=server) == 2
    assert server.calls == []
    assert "token" in capsys.readouterr().err


def test_serve_with_a_token_guards_every_route(
    database: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN, encoding="utf-8")
    server = Recorder()
    argv = ["serve", "--db", str(database), "--host", "0.0.0.0", "--port", "9000"]
    assert main([*argv, "--token-file", str(token_file)], start_server=server) == 0
    ((app, host, port),) = server.calls
    assert (host, port) == ("0.0.0.0", 9000)
    with TestClient(app) as client:
        assert client.get("/v1/costs/summary").status_code == 401
        authorised = {"authorization": f"Bearer {TOKEN}"}
        assert client.get("/v1/costs/summary", headers=authorised).status_code == 200
    assert TOKEN not in capsys.readouterr().out


def test_the_token_can_come_from_the_environment(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    server = Recorder()
    assert main(["serve", "--db", str(database), "--host", "0.0.0.0"], start_server=server) == 0
    with TestClient(server.calls[0][0]) as client:
        assert client.get("/health").status_code == 401


def test_a_bad_token_is_refused_without_echoing_it(
    database: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, "short-secret")
    server = Recorder()
    assert main(["serve", "--db", str(database)], start_server=server) == 2
    assert server.calls == [] and "short-secret" not in capsys.readouterr().err


def test_serve_refuses_a_database_that_does_not_exist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = Recorder()
    missing = tmp_path / "typo.sqlite"
    assert main(["serve", "--db", str(missing)], start_server=server) == 2
    assert server.calls == [] and not missing.exists()
    assert capsys.readouterr().err == f"data-finops: no database at {missing}\n"


def test_demo_loads_the_fixtures_calculates_and_serves_on_loopback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = Recorder("/v1/costs/summary")
    assert main(["demo", "--port", "8123"], start_server=server) == 0
    ((_, host, port),) = server.calls
    assert (host, port) == ("127.0.0.1", 8123)
    status, summary = server.responses["/v1/costs/summary"]
    assert status == 200
    (usd,) = summary["summaries"][0]["currencies"]
    assert usd["billed"] == "10.00"
    out = capsys.readouterr().out
    assert "http://127.0.0.1:8123/" in out and "calculate:" in out


def test_without_the_api_extra_serve_and_demo_say_how_to_install_it(
    database: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for module in [name for name in sys.modules if name.split(".")[0] in ("fastapi", "uvicorn")]:
        monkeypatch.delitem(sys.modules, module)
    monkeypatch.setitem(sys.modules, "fastapi", None)
    monkeypatch.delitem(sys.modules, "data_finops.api", raising=False)
    monkeypatch.delitem(sys.modules, "data_finops.api.app", raising=False)
    server = Recorder()
    assert main(["serve", "--db", str(database)], start_server=server) == 2
    assert main(["demo"], start_server=server) == 2
    assert server.calls == []
    out, err = capsys.readouterr()
    assert err.count("pip install 'data-finops[api]'") == 2
    assert "demo: database" not in out  # nothing written before the extra is found missing


def test_the_demo_ignores_a_configured_token_and_removes_its_database_afterwards(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    server = Recorder("/health")
    assert main(["demo"], start_server=server) == 0
    assert server.responses["/health"][0] == 200  # the local demo needs no secret
    printed = next(
        line for line in capsys.readouterr().out.splitlines() if line.startswith("demo: database ")
    )
    assert not Path(printed.removeprefix("demo: database ")).exists()


def test_the_demo_needs_the_fixtures_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server = Recorder()
    assert main(["demo", "--fixtures", str(tmp_path / "none")], start_server=server) == 2
    assert server.calls == [] and "source checkout" in capsys.readouterr().err


def test_localhost_by_name_is_not_treated_as_loopback(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(TOKEN_VARIABLE, raising=False)
    server = Recorder()
    assert main(["serve", "--db", str(database), "--host", "localhost"], start_server=server) == 2
    assert main(["serve", "--db", str(database), "--host", "::1"], start_server=server) == 0


def test_without_uvicorn_serve_says_how_to_install_the_extra(
    database: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    server = Recorder()
    assert main(["serve", "--db", str(database)], start_server=server) == 2
    assert "pip install 'data-finops[api]'" in capsys.readouterr().err
