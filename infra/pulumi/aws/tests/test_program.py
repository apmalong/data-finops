"""The program's guard on its state backend (spec 08 §Layout; ADR 0001)."""

from pathlib import Path

import pulumi
import pytest
from deployment.program import build, require_local_state, require_package
from deployment.settings import Settings

from tests.mocks import MOCKS


@pytest.mark.parametrize("url", ["", "https://api.pulumi.com", "s3://secret-state-bucket"])
def test_any_backend_other_than_a_local_file_is_refused(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PULUMI_BACKEND_URL", url)
    with pytest.raises(RuntimeError, match="file://") as raised:
        require_local_state()
    assert "secret" not in str(raised.value)


def test_a_local_file_backend_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PULUMI_BACKEND_URL", "file://C:/data-finops/.local/pulumi")
    require_local_state()


@pulumi.runtime.test
def test_the_program_outputs_the_bucket(tmp_path: Path) -> pulumi.Output[None]:
    MOCKS.resources.clear()
    settings = Settings(
        region="us-east-1", export_bucket="b", export_name="e", workgroups=("demo",)
    )
    outputs = build(settings, package=_stand_in(tmp_path)).outputs()
    return pulumi.Output.all(**outputs).apply(
        lambda values: assert_equal(
            {k: values[k] for k in ("bucket", "apiUrl")},
            {
                "bucket": "mock-deployment-bucket",
                "apiUrl": "https://mock.lambda-url.us-east-1.on.aws/",
            },
        )
    )


def assert_equal(actual: object, expected: object) -> None:
    assert actual == expected


def test_a_missing_package_is_refused_with_how_to_build_it(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="python -m deployment.package"):
        require_package(tmp_path / "data-finops-lambda.zip")
    built = tmp_path / "built.zip"
    built.write_bytes(b"PK")
    require_package(built)


def _stand_in(folder: Path) -> Path:
    """A stand-in for the built package: the program hashes it."""
    package = folder / "data-finops-lambda.zip"
    package.write_bytes(b"PK\x05\x06" + b"\x00" * 18)  # an empty zip
    return package
