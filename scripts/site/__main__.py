"""`python -m scripts.site check [ROOT]`: report every problem in the site (spec 07)."""

import argparse
import sys
from pathlib import Path

from scripts.site.check import check_site

REPO = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.site", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="report every problem in the site")
    check.add_argument("root", nargs="?", type=Path, default=REPO / "site")
    args = parser.parse_args(argv)

    problems = check_site(args.root)
    for p in problems:
        print(f"{p.path}:{p.line}: {p.rule}: {p.message}")
    print(f"{len(problems)} problem(s) in {args.root}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
