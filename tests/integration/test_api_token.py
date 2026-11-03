"""The bearer token on every route (#108; ADR 0024; spec 06 seam 2)."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fixture_database import fill
from openapi_contract import valid_response

from data_finops.api import TOKEN_VARIABLE, create_app, read_token

TOKEN = "s3cret-token-value-0123456789"
PROBLEM = "application/problem+json"
EVENT = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "openlineage" / "events.json").read_text(
        encoding="utf-8"
    )
)[0]
ROUTES = [
    ("get", "/health"),
    ("get", "/v1/costs/summary"),
    ("get", "/v1/allocations"),
    ("get", "/openapi.json"),
    ("post", "/api/v1/lineage"),
]


@pytest.fixture(scope="module")
def database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("token") / "data-finops.sqlite"
    fill(path)
    return path


@pytest.fixture
def client(database: Path) -> Iterator[TestClient]:
    with TestClient(create_app(database, token=TOKEN), raise_server_exceptions=False) as opened:
        yield opened


def _call(client: TestClient, method: str, path: str, headers: dict[str, str]) -> object:
    if method == "post":
        return client.post(path, json=EVENT, headers=headers)
    return client.get(path, headers=headers)


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize(
    "headers",
    [{}, {"authorization": "Bearer wrong-token"}, {"authorization": TOKEN}],
    ids=["missing", "wrong", "not-bearer"],
)
def test_every_route_refuses_a_missing_or_wrong_token(
    client: TestClient, method: str, path: str, headers: dict[str, str]
) -> None:
    response = _call(client, method, path, headers)
    assert (response.status_code, response.headers["content-type"]) == (401, PROBLEM)  # type: ignore[attr-defined]
    assert response.headers["www-authenticate"] == "Bearer"  # type: ignore[attr-defined]
    assert TOKEN not in response.text  # type: ignore[attr-defined]


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_every_route_answers_with_the_token(client: TestClient, method: str, path: str) -> None:
    response = _call(client, method, path, {"authorization": f"Bearer {TOKEN}"})
    assert response.status_code in (200, 201)  # type: ignore[attr-defined]
    assert TOKEN not in response.text  # type: ignore[attr-defined]


def test_a_refusal_is_documented_and_valid(client: TestClient) -> None:
    response = client.get("/v1/costs/summary")
    valid_response("get", "/v1/costs/summary", 401, response.json())


def test_without_a_token_configured_every_route_is_open(database: Path) -> None:
    with TestClient(create_app(database), raise_server_exceptions=False) as client:
        assert client.get("/v1/costs/summary").status_code == 200


def test_the_token_never_reaches_a_log(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        client.get("/v1/costs/summary", headers={"authorization": f"Bearer {TOKEN}"})
        client.get("/v1/costs/summary", headers={"authorization": "Bearer wrong-token"})
    assert TOKEN not in caplog.text and "wrong-token" not in caplog.text


def test_the_token_comes_from_the_environment_or_a_file(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text(f"{TOKEN}\n", encoding="utf-8")
    assert read_token(environ={}, file=None) is None
    assert read_token(environ={TOKEN_VARIABLE: TOKEN}, file=None) == TOKEN
    assert read_token(environ={}, file=token_file) == TOKEN


@pytest.mark.parametrize("value", ["", "   ", "short"])
def test_an_empty_or_short_token_is_refused(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError):
        read_token(environ={TOKEN_VARIABLE: value}, file=None)


def test_a_token_in_both_places_is_refused(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN, encoding="utf-8")
    with pytest.raises(ValueError) as refused:
        read_token(environ={TOKEN_VARIABLE: TOKEN}, file=token_file)
    assert TOKEN not in str(refused.value)
