"""`python -m scripts.site build|check`: stamp the site's shared regions, or report its problems.

See spec 07, "The site tool".
"""

import argparse
import sys
from pathlib import Path

from scripts.site.build import SiteError, build
from scripts.site.check import check_site

REPO = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.site", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="rewrite every page whose stamped regions are stale")
    check = commands.add_parser("check", help="report every problem in the site")
    check.add_argument("root", nargs="?", type=Path, default=REPO / "site")
    args = parser.parse_args(argv)
    return _build() if args.command == "build" else _check(args.root)


def _build() -> int:
    try:
        built = build(REPO)
    except SiteError as error:
        print(f"build: {error}", file=sys.stderr)
        return 1
    written = 0
    for path, text in built.items():
        target = REPO / "site" / path
        if target.read_text(encoding="utf-8") != text:
            target.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {_shown(target)}")
            written += 1
    print(f"{written} page(s) written, {len(built) - written} unchanged", file=sys.stderr)
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
