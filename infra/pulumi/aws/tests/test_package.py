"""The Lambda package (spec 08 seam 8): one arm64 zip from the checkout, both handlers at its
root, nothing that is not run, and within Lambda's 250 MB unzipped limit.

The packing rules are tested on a small installed tree, offline. The real build downloads the
arm64 wheels from PyPI, so it runs only where `DATA_FINOPS_BUILD_PACKAGE=1` (the `infra` CI job).
"""

import os
import zipfile
from pathlib import Path

import pytest
from deployment.package import (
    LAMBDA_UNZIPPED_LIMIT,
    PackageError,
    assemble,
    build,
)

REPO = Path(__file__).resolve().parents[4]


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


INSTALLED = {
    "data_finops/__init__.py": b"",
    "data_finops/lambda_handlers.py": b"def collector(event, context): ...\n",
    "data_finops/__pycache__/lambda_handlers.cpython-312.pyc": b"\x00",
    "data_finops-0.0.0.dist-info/METADATA": b"Name: data-finops\n",
    "somelib/__init__.py": b"",
    "somelib/core.py": b"x = 1\n",
    "somelib/tests/test_core.py": b"def test(): ...\n",
    "somelib/stale.pyc": b"\x00",
}


def test_the_zip_holds_what_runs_and_nothing_else(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "site", INSTALLED)
    package = assemble(tree, tmp_path / "out.zip")
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
    assert names == [
        "data_finops-0.0.0.dist-info/METADATA",
        "data_finops/__init__.py",
        "data_finops/lambda_handlers.py",
        "somelib/__init__.py",
        "somelib/core.py",
    ]


def test_the_same_tree_gives_the_same_zip(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "site", INSTALLED)
    first = assemble(tree, tmp_path / "first.zip").read_bytes()
    for path in tree.rglob("*"):
        os.utime(path, (0, 1_800_000_000))  # other file times
    assert assemble(tree, tmp_path / "second.zip").read_bytes() == first


def test_a_tree_without_the_handlers_is_refused(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "site", {"somelib/core.py": b"x = 1\n"})
    with pytest.raises(PackageError, match="data_finops/lambda_handlers.py"):
        assemble(tree, tmp_path / "out.zip")


def test_a_tree_over_the_unzipped_limit_is_refused(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "site", INSTALLED | {"big/blob.bin": b"x" * 2048})
    with pytest.raises(PackageError, match="unzipped"):
        assemble(tree, tmp_path / "out.zip", limit=1024)


def test_lambdas_limit_is_250_mb() -> None:
    assert LAMBDA_UNZIPPED_LIMIT == 262_144_000  # 250 MiB, as Lambda's quota counts it


@pytest.mark.skipif(
    os.environ.get("DATA_FINOPS_BUILD_PACKAGE") != "1",
    reason="downloads the arm64 wheels; the infra CI job sets DATA_FINOPS_BUILD_PACKAGE=1",
)
def test_the_real_package_builds_from_the_checkout(tmp_path: Path) -> None:
    package = build(REPO, tmp_path / "data-finops-lambda.zip")
    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        unzipped = sum(info.file_size for info in archive.infolist())
    assert "data_finops/lambda_handlers.py" in names
    assert {"mangum/__init__.py", "boto3/__init__.py", "fastapi/__init__.py"} <= names
    assert any(n.startswith("pyarrow/") and n.endswith(".so") for n in names)  # arm64 wheels
    assert not any("__pycache__" in n for n in names)
    assert unzipped <= LAMBDA_UNZIPPED_LIMIT
