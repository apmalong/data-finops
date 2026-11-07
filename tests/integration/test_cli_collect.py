"""`data-finops collect` through `main(argv)` (spec 08 seam 4). The AWS client is injected:
a Stubber-backed one, so no command reaches AWS."""

import builtins
import json
from pathlib import Path
from typing import Any

import pytest
from aws_stubs import (
    BUCKET,
    METADATA,
    athena_client,
    expect_get,
    expect_history,
    expect_listing,
    expect_prefixes,
    s3_client,
    stubbed,
)
from botocore.stub import Stubber
from fixture_database import FIXTURES

from data_finops.cli import main, make_aws_clients
from data_finops.collection import AwsClients, CollectionSettings
from data_finops.reporting import Reporting

EVENTS = (FIXTURES / "openlineage" / "events.json").read_bytes()
KEY = "inbox/openlineage/run.json"
SETTINGS = (
    f'bucket = "{BUCKET}"\nregion = "us-east-1"\naccount_id = "123456789012"\n'
    'workgroups = ["demo"]\nexport_bucket = "example-export-bucket"\nexport_prefix = "focus"\n'
    'export_name = "example-focus-1-2"\n'
)


def _clients(s3: Any) -> AwsClients:
    """`s3` and an Athena client whose history is empty, already stubbed and active."""
    athena = athena_client()
    stubber = Stubber(athena)
    expect_history(stubber, [])
    stubber.activate()
    return AwsClients(s3=s3, athena=athena)


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "collect.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_collect_runs_a_collection_and_prints_its_summary_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "data-finops.sqlite"
    config = _config(tmp_path, SETTINGS)
    s3 = s3_client()
    made: list[Any] = []

    def factory(settings: Any) -> Any:
        made.append(settings)
        return _clients(s3)

    with stubbed(s3) as stubber:
        expect_listing(stubber, "inbox/dbt/", {})
        expect_listing(stubber, "inbox/openlineage/", {KEY: EVENTS})
        expect_get(stubber, KEY, EVENTS)
        expect_prefixes(stubber, METADATA, [])
        code = main(["collect", "--db", str(db), "--config", str(config)], aws_clients=factory)

    assert code == 0
    summary = json.loads(capsys.readouterr().out.splitlines()[0])
    assert (summary["exit_code"], summary["sources"]["openlineage"]["rejected"]) == (0, [])
    assert [(s.bucket, s.region, s.account_id, s.workgroups, s.profile) for s in made] == [
        (BUCKET, "us-east-1", "123456789012", ("demo",), None)
    ]
    with Reporting(db) as reporting:
        assert reporting.root_runs().total > 0


def _never(settings: Any) -> Any:
    raise AssertionError("no client should be made")


@pytest.mark.parametrize(
    ("text", "names"),
    [
        (SETTINGS + 'bukket = "secret-typo"\n', "bukket"),
        ('region = "us-east-1"\n', "bucket"),
        (f'bucket = "{BUCKET}"\n', "region"),
        ("bucket = [not toml\n", "TOML"),
        (SETTINGS.replace(f'"{BUCKET}"', "3"), "bucket"),
        (SETTINGS.replace('account_id = "123456789012"\n', ""), "account_id"),
        (SETTINGS.replace('"123456789012"', '"secret-12345"'), "account_id"),
        (SETTINGS.replace('workgroups = ["demo"]', "workgroups = []"), "workgroups"),
        (SETTINGS.replace('workgroups = ["demo"]', 'workgroups = "secret"'), "workgroups"),
        (SETTINGS + "max_history_pages = 0\n", "max_history_pages"),
        (SETTINGS + "max_history_pages = 1001\n", "max_history_pages"),
        (SETTINGS.replace('export_bucket = "example-export-bucket"\n', ""), "export_bucket"),
        (SETTINGS.replace('export_name = "example-focus-1-2"\n', ""), "export_name"),
        (SETTINGS.replace('export_name = "example-focus-1-2"', "export_name = 3"), "export_name"),
        (SETTINGS + "billing_lookback_periods = 0\n", "billing_lookback_periods"),
        (SETTINGS + "billing_lookback_periods = 25\n", "billing_lookback_periods"),
        (SETTINGS + 'billing_lookback_periods = "secret"\n', "billing_lookback_periods"),
        (SETTINGS + "max_history_pages = true\n", "max_history_pages"),
        (SETTINGS + 'max_history_pages = "secret"\n', "max_history_pages"),
    ],
)
def test_collect_refuses_a_bad_settings_file_without_echoing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, names: str
) -> None:
    config = _config(tmp_path, text)
    db = tmp_path / "data-finops.sqlite"
    assert main(["collect", "--db", str(db), "--config", str(config)], aws_clients=_never) == 2
    err = capsys.readouterr().err
    assert names in err
    assert "secret" not in err


def test_collect_lists_each_rejection_by_key_and_position(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path, SETTINGS)
    data = b'{"secret": 1}'
    s3 = s3_client()
    with stubbed(s3) as stubber:
        expect_listing(stubber, "inbox/dbt/", {})
        expect_listing(stubber, "inbox/openlineage/", {KEY: data})
        expect_get(stubber, KEY, data)
        expect_prefixes(stubber, METADATA, [])
        argv = ["collect", "--db", str(tmp_path / "x.sqlite"), "--config", str(config)]
        assert main(argv, aws_clients=lambda settings: _clients(s3)) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith(f"  {KEY}#1: invalid RunEvent")
    assert "secret" not in "\n".join(lines)


def test_without_the_aws_extra_collect_says_how_to_install_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_import = builtins.__import__

    def without_boto3(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "boto3":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_boto3)
    config = _config(tmp_path, SETTINGS)
    argv = ["collect", "--db", str(tmp_path / "x.sqlite"), "--config", str(config)]
    assert main(argv) == 2
    assert "--extra aws" in capsys.readouterr().err


def test_collect_with_an_unknown_profile_says_so_without_naming_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        empty = tmp_path / variable
        empty.write_text("", encoding="utf-8")
        monkeypatch.setenv(variable, str(empty))
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    text = SETTINGS + 'profile = "secret-profile"\n'
    config = _config(tmp_path, text)
    argv = ["collect", "--db", str(tmp_path / "x.sqlite"), "--config", str(config)]
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert "profile" in err
    assert "secret" not in err


def test_the_aws_clients_retry_throttled_calls_a_bounded_number_of_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        empty = tmp_path / variable
        empty.write_text("", encoding="utf-8")
        monkeypatch.setenv(variable, str(empty))
    settings = CollectionSettings(
        bucket=BUCKET, region="us-east-1", account_id="123456789012", workgroups=("demo",)
    )
    clients = make_aws_clients(settings)  # makes no request
    for client in (clients.s3, clients.athena):
        assert client.meta.region_name == "us-east-1"
        assert client.meta.config.retries == {"mode": "standard", "total_max_attempts": 5}
