"""`data-finops collect` through `main(argv)` (spec 08 seam 4). The AWS client is injected:
a Stubber-backed one, so no command reaches AWS."""

import builtins
import json
from pathlib import Path
from typing import Any

import pytest
from aws_stubs import BUCKET, expect_get, expect_listing, s3_client, stubbed
from fixture_database import FIXTURES

from data_finops.cli import main
from data_finops.reporting import Reporting

EVENTS = (FIXTURES / "openlineage" / "events.json").read_bytes()
KEY = "inbox/openlineage/run.json"


def _config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "collect.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_collect_runs_a_collection_and_prints_its_summary_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "data-finops.sqlite"
    config = _config(tmp_path, f'bucket = "{BUCKET}"\nregion = "us-east-1"\n')
    s3 = s3_client()
    made: list[Any] = []

    def factory(settings: Any) -> Any:
        made.append(settings)
        return s3

    with stubbed(s3) as stubber:
        expect_listing(stubber, "inbox/dbt/", {})
        expect_listing(stubber, "inbox/openlineage/", {KEY: EVENTS})
        expect_get(stubber, KEY, EVENTS)
        code = main(["collect", "--db", str(db), "--config", str(config)], s3_client=factory)

    assert code == 0
    summary = json.loads(capsys.readouterr().out.splitlines()[0])
    assert (summary["exit_code"], summary["sources"]["openlineage"]["rejected"]) == (0, [])
    assert [(s.bucket, s.region, s.profile) for s in made] == [(BUCKET, "us-east-1", None)]
    with Reporting(db) as reporting:
        assert reporting.root_runs().total > 0


def _never(settings: Any) -> Any:
    raise AssertionError("no client should be made")


@pytest.mark.parametrize(
    ("text", "names"),
    [
        (f'bucket = "{BUCKET}"\nregion = "us-east-1"\nbukket = "secret-typo"\n', "bukket"),
        ('region = "us-east-1"\n', "bucket"),
        (f'bucket = "{BUCKET}"\n', "region"),
        ("bucket = [not toml\n", "TOML"),
        ('bucket = 3\nregion = "us-east-1"\n', "bucket"),
    ],
)
def test_collect_refuses_a_bad_settings_file_without_echoing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, names: str
) -> None:
    config = _config(tmp_path, text)
    db = tmp_path / "data-finops.sqlite"
    assert main(["collect", "--db", str(db), "--config", str(config)], s3_client=_never) == 2
    err = capsys.readouterr().err
    assert names in err
    assert "secret" not in err


def test_collect_lists_each_rejection_by_key_and_position(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path, f'bucket = "{BUCKET}"\nregion = "us-east-1"\n')
    data = b'{"secret": 1}'
    s3 = s3_client()
    with stubbed(s3) as stubber:
        expect_listing(stubber, "inbox/dbt/", {})
        expect_listing(stubber, "inbox/openlineage/", {KEY: data})
        expect_get(stubber, KEY, data)
        argv = ["collect", "--db", str(tmp_path / "x.sqlite"), "--config", str(config)]
        assert main(argv, s3_client=lambda settings: s3) == 1
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
    config = _config(tmp_path, f'bucket = "{BUCKET}"\nregion = "us-east-1"\n')
    argv = ["collect", "--db", str(tmp_path / "x.sqlite"), "--config", str(config)]
    assert main(argv) == 2
    assert "--extra aws" in capsys.readouterr().err
