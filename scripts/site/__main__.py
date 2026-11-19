"""`python -m scripts.site build|check`: stamp the site's shared regions, or report its problems.

`build --check` writes nothing: it names each page that `build` would rewrite, and exits 1 if
there is one. It is the site's drift check outside pytest, for a docs-only change (#321).

See spec 07, "The site tool".
"""

import argparse
import sys
from pathlib import Path

from scripts.site.build import SiteError, build
from scripts.site.check import check_site

REPO = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None, *, repo: Path = REPO) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.site", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser(
        "build", help="rewrite every page whose stamped regions are stale"
    )
    builder.add_argument(
        "--check", action="store_true", help="write nothing; name each stale page and exit 1"
    )
    check = commands.add_parser("check", help="report every problem in the site")
    check.add_argument("root", nargs="?", type=Path, default=repo / "site")
    args = parser.parse_args(argv)
    if args.command == "build":
        return _build(repo, check_only=args.check)
    return _check(args.root)


def _build(repo: Path, *, check_only: bool) -> int:
    try:
        built = build(repo)
    except SiteError as error:
        print(f"build: {error}", file=sys.stderr)
        return 1
    stale = [
        path
        for path, text in built.items()
        if not (repo / "site" / path).is_file()
        or (repo / "site" / path).read_text(encoding="utf-8") != text
    ]
    if check_only:
        for path in stale:
            print(f"stale: site/{path}")
        if stale:
            print("run `python -m scripts.site build`", file=sys.stderr)
        return 1 if stale else 0
    for path in stale:
        target = repo / "site" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(built[path], encoding="utf-8", newline="\n")
        print(f"wrote {_shown(target)}")
    print(f"{len(stale)} page(s) written, {len(built) - len(stale)} unchanged", file=sys.stderr)
    return 0


def _check(root: Path) -> int:
    problems = check_site(root)
    for p in problems:
        print(f"{_shown(root / p.path)}:{p.line}: {p.rule}: {p.message}")
    print(f"{len(problems)} problem(s) in {root}", file=sys.stderr)
    return 1 if problems else 0


def _shown(path: Path) -> str:
    """The path relative to the working directory when it is under it, so editors can open it."""
    try:
        return path.resolve().relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    sys.exit(main())
