"""The Lambda package (spec 08 §Topology; #186): one arm64 zip built from the checkout, holding
`data_finops` with its `aws` and `api` extras, both handlers at the root.

    python -m deployment.package        writes build/data-finops-lambda.zip (the deploy wizard's
                                        first step; `pulumi up` uploads it)

Building downloads the arm64 wheels from PyPI with uv; packing is stdlib only and repeatable:
the same installed tree always gives the same zip.
"""

import shutil

# subprocess runs uv with a fixed argument list, never a shell (see _uv).
import subprocess  # nosec B404
import tempfile
import zipfile
from pathlib import Path

LAMBDA_UNZIPPED_LIMIT = 250 * 1024 * 1024
"""Lambda's quota for a function's unzipped package, layers included: 250 MB, as AWS counts it
(262,144,000 bytes)."""
ARCHITECTURE = "arm64"
"""The functions' architecture (spec 08)."""
PLATFORM = "aarch64-manylinux_2_28"
"""Wheels for that architecture."""
PYTHON = "3.12"
"""The functions' Python, which the wheels are installed for."""
HANDLER_MODULE = "data_finops/lambda_handlers.py"
SKIPPED_FOLDERS = frozenset({"tests"})
"""Test folders third-party packages ship; compiled caches go with their `.pyc` files."""
SKIPPED_SUFFIXES = (".pyc",)
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
"""Every entry's time, so the zip does not depend on when files were written."""
REPO = Path(__file__).resolve().parents[4]
"""The checkout the package is built from."""
DEFAULT_PACKAGE = Path(__file__).resolve().parents[1] / "build" / "data-finops-lambda.zip"


class PackageError(RuntimeError):
    """The package cannot be built, or would not run on Lambda."""


def assemble(tree: Path, package: Path, *, limit: int = LAMBDA_UNZIPPED_LIMIT) -> Path:
    """Zip the installed `tree` into `package`, in path order with fixed times, leaving out
    caches, compiled files and test folders. PackageError when the handlers are missing or
    the files exceed `limit` unzipped."""
    files = sorted(
        (path for path in tree.rglob("*") if path.is_file() and _kept(path.relative_to(tree))),
        key=lambda path: path.relative_to(tree).as_posix(),
    )
    names = [path.relative_to(tree).as_posix() for path in files]
    if HANDLER_MODULE not in names:
        raise PackageError(f"{HANDLER_MODULE} is not in the package: the functions could not start")
    unzipped = sum(path.stat().st_size for path in files)
    if unzipped > limit:
        raise PackageError(f"the package is {unzipped} bytes unzipped, over Lambda's {limit}")
    package.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, name in zip(files, names, strict=True):
            entry = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, path.read_bytes())
    return package


def build(repo: Path, package: Path) -> Path:
    """Build the wheel from the checkout at `repo`, install it with its `aws` and `api` extras
    for arm64 Linux, and assemble the package."""
    with tempfile.TemporaryDirectory(prefix="data-finops-package-") as scratch:
        wheels, site = Path(scratch) / "wheels", Path(scratch) / "site"
        _uv("build", "--wheel", "--out-dir", str(wheels), str(repo))
        (wheel,) = wheels.glob("data_finops-*.whl")
        _uv(
            "pip", "install", "--target", str(site),
            "--python-platform", PLATFORM, "--python-version", PYTHON,
            "--only-binary", ":all:", f"{wheel}[aws,api]",
        )  # fmt: skip
        return assemble(site, package)


def _kept(relative: Path) -> bool:
    return not SKIPPED_FOLDERS.intersection(relative.parts[:-1]) and not relative.name.endswith(
        SKIPPED_SUFFIXES
    )


def _uv(*arguments: str) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise PackageError("uv is needed to build the package: https://docs.astral.sh/uv/")
    # Arguments are this module's own paths and constants: no shell, no outside input.
    result = subprocess.run(  # nosec B603  # noqa: S603
        [uv, *arguments], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise PackageError(f"uv {arguments[0]} failed:\n{result.stderr[-2000:]}")


if __name__ == "__main__":
    built = build(REPO, DEFAULT_PACKAGE)
    print(f"wrote {built} ({built.stat().st_size} bytes)")
