"""The site checker (spec 07, "The site tool"), tested only through `check_site`."""

import json
from pathlib import Path

from scripts.site.check import Problem, check_site

REPO = Path(__file__).resolve().parents[2]
BODY = 9  # the line on which `page()` puts the body


def page(body: str, *, title: str = "A page", head: str = "", lang: str = ' lang="en"') -> str:
    return (
        f'<!doctype html>\n<html{lang}>\n<head>\n<meta charset="utf-8">\n'
        f"<title>{title}</title>\n{head}</head>\n<body>\n<h1>Heading</h1>\n{body}\n</body>\n</html>\n"
    )


def make_site(root: Path, files: dict[str, str]) -> Path:
    """A site of `files`; unless they include one, a nav.json that lists each page."""
    if "nav.json" not in files:
        pages = [n for n in files if n.endswith(".html")]
        home = ["index.html"] if "index.html" in pages else []
        entries = [{"title": n, "path": n} for n in home + [p for p in pages if p not in home]]
        files = {**files, "nav.json": json.dumps(entries)}
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def rules(problems: list[Problem]) -> list[tuple[str, str, int]]:
    return [(p.path, p.rule, p.line) for p in problems]


def test_a_well_formed_site_has_no_problems(tmp_path: Path) -> None:
    site = make_site(
        tmp_path,
        {
            "index.html": page('<p id="top"><a href="how-to/a.html#step">A</a></p>'),
            "how-to/a.html": page('<p id="step"><a href="../index.html">Home</a></p>'),
        },
    )
    assert check_site(site) == []


def test_a_link_to_a_missing_page_is_reported(tmp_path: Path) -> None:
    site = make_site(tmp_path, {"index.html": page('<p><a href="nowhere.html">x</a></p>')})
    assert rules(check_site(site)) == [("index.html", "broken-link", BODY)]


def test_an_anchor_no_id_matches_is_reported_on_this_page_and_others(tmp_path: Path) -> None:
    site = make_site(
        tmp_path,
        {
            "index.html": page(
                '<p id="here"><a href="#gone">x</a> <a href="b.html#lost">y</a></p>'
            ),
            "b.html": page('<p id="found"><a href="index.html#here">z</a></p>'),
        },
    )
    assert rules(check_site(site)) == [
        ("index.html", "missing-anchor", BODY),
        ("index.html", "missing-anchor", BODY),
    ]


def test_anchors_are_checked_through_parent_paths_and_links_may_not_leave_the_site(
    tmp_path: Path,
) -> None:
    site = make_site(
        tmp_path / "site",
        {
            "index.html": page('<p id="top">Home</p>'),
            "how-to/a.html": page(
                '<p><a href="../index.html#nope">x</a> <a href="../../x.html">y</a></p>'
            ),
        },
    )
    (tmp_path / "x.html").write_text("outside", encoding="utf-8")
    assert rules(check_site(site)) == [
        ("how-to/a.html", "broken-link", BODY),
        ("how-to/a.html", "missing-anchor", BODY),
    ]


def test_root_absolute_links_are_reported_even_when_the_file_exists(tmp_path: Path) -> None:
    site = make_site(tmp_path, {"index.html": page('<p><a href="/index.html">x</a></p>')})
    assert rules(check_site(site)) == [("index.html", "root-absolute-link", BODY)]


def test_pages_may_link_out_but_never_load_an_asset_from_outside(tmp_path: Path) -> None:
    site = make_site(
        tmp_path,
        {
            "index.html": page(
                '<p><a href="https://diataxis.fr/">out</a> <a href="//example.org/">out</a>\n'
                '<img src="https://x.example/a.png" alt="A">\n'
                '<link rel="stylesheet" href="//x.example/b.css">\n'
                '<script src="HTTPS://x.example/c.js"></script>\n'
                '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="Dot"></p>'
            ),
        },
    )
    assert rules(check_site(site)) == [
        ("index.html", "external-url", BODY + 1),
        ("index.html", "external-url", BODY + 2),
        ("index.html", "external-url", BODY + 3),
    ]


def test_an_id_used_twice_on_a_page_is_reported_where_it_repeats(tmp_path: Path) -> None:
    site = make_site(tmp_path, {"index.html": page('<p id="a">x</p>\n<p id="a">y</p>')})
    assert rules(check_site(site)) == [("index.html", "duplicate-id", BODY + 1)]


def test_a_page_needs_lang_a_title_and_exactly_one_h1(tmp_path: Path) -> None:
    site = make_site(
        tmp_path,
        {
            "index.html": page("<p>Home</p>"),
            "no-lang.html": page("<p>x</p>", lang=""),
            "no-title.html": page("<p>x</p>", title=""),
            "two-h1.html": page("<h1>Again</h1>"),
            "no-h1.html": page("<p>x</p>").replace("<h1>Heading</h1>", "<h2>Heading</h2>"),
        },
    )
    assert [(p.path, p.rule) for p in check_site(site)] == [
        ("no-h1.html", "h1-count"),
        ("no-lang.html", "missing-lang"),
        ("no-title.html", "missing-title"),
        ("two-h1.html", "h1-count"),
    ]


def test_images_need_alt_text_and_must_exist_and_scripts_must_exist(tmp_path: Path) -> None:
    site = make_site(
        tmp_path,
        {
            "index.html": page(
                '<p><img src="assets/a.svg" alt="The flow">\n'
                '<img src="assets/a.svg">\n'
                '<img src="assets/a.svg" alt=" ">\n'
                '<img src="assets/gone.svg" alt="Gone">\n'
                '<script src="assets/gone.js"></script></p>'
            ),
            "assets/a.svg": "<svg/>",
        },
    )
    assert rules(check_site(site)) == [
        ("index.html", "missing-alt", BODY + 1),
        ("index.html", "missing-alt", BODY + 2),
        ("index.html", "missing-image", BODY + 3),
        ("index.html", "broken-link", BODY + 4),
    ]


def test_misnested_stray_and_unclosed_tags_are_reported(tmp_path: Path) -> None:
    site = make_site(
        tmp_path,
        {
            "index.html": page(
                "<p>A<br>line <em>closed late</p>\n"
                "</span>\n"
                "<div><svg/><p>Fine</p>\n"
                "<ul><li>Never closed</ul>"
            ),
        },
    )
    assert rules(check_site(site)) == [
        ("index.html", "unbalanced-tag", BODY),
        ("index.html", "unbalanced-tag", BODY + 1),
        ("index.html", "unbalanced-tag", BODY + 2),
        ("index.html", "unbalanced-tag", BODY + 3),
    ]


def test_twelve_digit_numbers_other_than_the_placeholder_account_are_reported(
    tmp_path: Path,
) -> None:
    site = make_site(
        tmp_path,
        {
            "index.html": page(
                "<p>Account 123456789012 is the placeholder.</p>\n<p>Not 210987654321.</p>"
            ),
            "notes.json": '{\n  "note": "acct-555555555555"\n}\n',
            "assets/site.css": "/* 9876543210987 has thirteen digits */\n",
        },
    )
    problems = check_site(site)
    assert rules(problems) == [
        ("index.html", "account-id", BODY + 1),
        ("notes.json", "account-id", 2),
    ]
    assert not any("210987654321" in p.message or "555555555555" in p.message for p in problems)


def test_external_urls_are_refused_in_css_and_js_but_allowed_in_page_text(tmp_path: Path) -> None:
    site = make_site(
        tmp_path,
        {
            "index.html": page('<p><a href="https://diataxis.fr/">Diátaxis</a></p>'),
            "assets/site.css": 'body { color: black; }\n@import url("https://fonts.example/f.css");\n',
            "assets/site.js": "// fetch('HTTP://cdn.example/x.js')\n",
        },
    )
    assert rules(check_site(site)) == [
        ("assets/site.css", "external-url", 2),
        ("assets/site.js", "external-url", 1),
    ]


def test_a_site_without_a_home_page_is_reported(tmp_path: Path) -> None:
    assert rules(check_site(tmp_path / "missing")) == [
        ("index.html", "missing-index", 1),
        ("nav.json", "missing-nav", 1),
    ]


def test_the_real_site_has_no_problems() -> None:
    assert check_site(REPO / "site") == []


def test_an_element_still_open_at_the_end_of_the_file_is_reported(tmp_path: Path) -> None:
    truncated = page("<p>x</p>").removesuffix("</html>\n")
    site = make_site(tmp_path, {"index.html": truncated})
    assert rules(check_site(site)) == [("index.html", "unbalanced-tag", 2)]


def test_external_urls_are_refused_inside_a_page_s_styles_and_scripts(tmp_path: Path) -> None:
    site = make_site(
        tmp_path,
        {
            "index.html": page(
                '<p style="background: url(http://x.example/a.png)">x</p>\n'
                "<style>p { background: url(https://x.example/b.png); }</style>\n"
                '<script>fetch("https://x.example/");</script>'
            ),
        },
    )
    assert rules(check_site(site)) == [
        ("index.html", "external-url", BODY),
        ("index.html", "external-url", BODY + 1),
        ("index.html", "external-url", BODY + 2),
    ]


def test_css_urls_must_be_relative_and_resolve(tmp_path: Path) -> None:
    site = make_site(
        tmp_path,
        {
            "index.html": page("<p>x</p>"),
            "assets/site.css": (
                "a { background: url('mark.svg'); }\n"
                'b { background: url("/mark.svg"); }\n'
                "i { background: url(gone.svg); }\n"
                "u { background: url(data:image/svg+xml;base64,PHN2Zy8+); }\n"
            ),
            "assets/mark.svg": "<svg/>",
        },
    )
    assert rules(check_site(site)) == [
        ("assets/site.css", "root-absolute-link", 2),
        ("assets/site.css", "broken-link", 3),
    ]


def test_encoded_paths_encoded_anchors_and_directory_links_resolve(tmp_path: Path) -> None:
    site = make_site(
        tmp_path,
        {
            "index.html": page(
                '<p><a href="a%20b.html#caf%C3%A9">x</a> <a href="ref/">y</a> '
                '<a href="empty/">z</a></p>'
            ),
            "a b.html": page('<p id="café">x</p>'),
            "ref/index.html": page("<p>x</p>"),
            "empty/.keep": "",
        },
    )
    assert rules(check_site(site)) == [("index.html", "broken-link", BODY)]


def test_links_resolve_case_sensitively_as_on_github_pages(tmp_path: Path) -> None:
    site = make_site(
        tmp_path,
        {"index.html": page('<p id="a"><a href="Index.HTML#a">x</a></p>')},
    )
    assert rules(check_site(site)) == [("index.html", "broken-link", BODY)]


def test_a_self_closed_title_does_not_swallow_the_page(tmp_path: Path) -> None:
    site = make_site(
        tmp_path,
        {"index.html": page("<p>x</p>").replace("<title>A page</title>", "<title/>")},
    )
    assert rules(check_site(site)) == [("index.html", "missing-title", 1)]


def test_a_file_that_is_not_utf8_is_reported_not_fatal(tmp_path: Path) -> None:
    site = make_site(tmp_path, {"index.html": page("<p>x</p>")})
    (site / "assets").mkdir()
    (site / "assets" / "LOGO.SVG").write_bytes(b"<svg>\xff 210987654321</svg>")
    (site / "notes.MD").write_text("210987654321", encoding="utf-8")
    assert rules(check_site(site)) == [
        ("assets/LOGO.SVG", "not-utf8", 1),
        ("notes.MD", "account-id", 1),
    ]


def test_every_page_is_in_the_nav_and_every_nav_entry_is_a_page(tmp_path: Path) -> None:
    nav = (
        "[\n"
        '  {"title": "Home", "path": "index.html"},\n'
        '  {"title": "How-to", "path": "how-to/index.html", "pages": [\n'
        '    {"title": "Gone", "path": "how-to/gone.html"}\n'
        "  ]}\n"
        "]\n"
    )
    site = make_site(
        tmp_path,
        {
            "index.html": page("<p>x</p>"),
            "how-to/index.html": page("<p>x</p>"),
            "orphan.html": page("<p>x</p>"),
            "nav.json": nav,
        },
    )
    assert rules(check_site(site)) == [
        ("nav.json", "nav-entry-missing", 4),
        ("orphan.html", "not-in-nav", 1),
    ]


def test_a_site_without_a_nav_is_reported(tmp_path: Path) -> None:
    site = make_site(tmp_path, {"index.html": page("<p>x</p>")})
    (site / "nav.json").unlink()
    assert rules(check_site(site)) == [("nav.json", "missing-nav", 1)]


def test_a_malformed_nav_is_reported_not_fatal(tmp_path: Path) -> None:
    site = make_site(tmp_path, {"index.html": page("<p>x</p>"), "nav.json": "[{"})
    assert rules(check_site(site)) == [("nav.json", "bad-nav", 1)]


def test_text_tokens_need_4_5_to_1_contrast_on_every_background_in_every_theme(
    tmp_path: Path,
) -> None:
    css = (
        ":root {\n"
        "  --bg-page: #ffffff;\n"
        "  --bg-surface: #fff;\n"
        "  --text: #000000;\n"
        "  --link: #767676;\n"  # 4.54:1 on white: passes
        "}\n"
        "@media (prefers-color-scheme: dark) {\n"
        "  :root {\n"
        "    --bg-page: #000000;\n"
        "    --text: #777777;\n"  # 4.69:1 on black, but the light surface is inherited
        "  }\n"
        "}\n"
        ":root[data-theme=dark] {\n"
        "  --bg-page: #000;\n"
        "  --bg-surface: #000;\n"
        "  --text: #fff;\n"
        "  --text-2: #6a6;\n"  # 7.51:1 on black, read as #66aa66: passes
        "  --link: #595959;\n"  # 3.0:1 on black: fails
        "}\n"
    )
    site = make_site(tmp_path, {"index.html": page("<p>x</p>"), "assets/site.css": css})
    assert rules(check_site(site)) == [
        ("assets/site.css", "low-contrast", 10),
        ("assets/site.css", "low-contrast", 18),
    ]


def test_contrast_reads_tokens_as_browsers_do_and_never_skips_one_silently(
    tmp_path: Path,
) -> None:
    css = (
        ".card { --bg-card: #fff; }\n"  # before :root, yet inherits it: --link-hover fails here
        "/* --text: #fff; is a comment, not a token */\n"
        ":root {\n"
        "  --bg-page: #FFFFFF;\n"
        "  --text: #000 !important;\n"
        "  --link-hover: #aaaaaa;\n"  # 2.32:1 on white: fails
        "  --text-2: rgb(0 0 0);\n"  # not hex: cannot be checked
        "  --text-3: #999999}\n"  # no final semicolon; 2.85:1 on white: fails
    )
    site = make_site(tmp_path, {"index.html": page("<p>x</p>"), "assets/site.css": css})
    assert rules(check_site(site)) == [
        ("assets/site.css", "low-contrast", 1),
        ("assets/site.css", "low-contrast", 1),
        ("assets/site.css", "low-contrast", 6),
        ("assets/site.css", "unchecked-colour", 7),
        ("assets/site.css", "low-contrast", 8),
    ]
