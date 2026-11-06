"""Tiny sites for the site tool's tests: a nav, pages with stamped regions, a repository."""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

NAV = [
    {"title": "Home", "path": "index.html"},
    {
        "title": "How-to guides",
        "path": "how-to/index.html",
        "pages": [{"title": "Calculate", "path": "how-to/calculate.html"}],
    },
]


ALL = ("head", "nav", "onpage", "pager", "footer")
NO_NAV = tuple(r for r in ALL if r != "nav")


def page(name: str, *, regions: tuple[str, ...] = ALL, body: str = "") -> str:
    def region(r: str) -> str:
        return f"<!-- {r}:start -->\nstale\n<!-- {r}:end -->\n" if r in regions else ""

    return (
        f'<!doctype html>\n<html lang="en">\n<head>\n{region("head")}<title>{name}</title>\n'
        f"</head>\n<body>\n{region('nav')}<main>\n<h1>{name}</h1>\n{region('onpage')}{body}"
        f"{region('pager')}</main>\n{region('footer')}</body>\n</html>\n"
    )


def make_repo(root: Path, nav: list[dict[str, object]], pages: dict[str, str]) -> Path:
    site = root / "site"
    site.mkdir(parents=True)
    (site / "nav.json").write_text(json.dumps(nav), encoding="utf-8")
    for name, text in pages.items():
        (site / name).parent.mkdir(parents=True, exist_ok=True)
        (site / name).write_text(text, encoding="utf-8")
    return root


def region(html: str, name: str) -> str:
    start, end = f"<!-- {name}:start -->\n", f"<!-- {name}:end -->"
    return html[html.index(start) + len(start) : html.index(end)]
