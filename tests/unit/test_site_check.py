"""The site checker (spec 07, "The site tool"), tested only through `check_site`."""

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
    site = make_site(
        tmp_path,
        {"index.html": page('<p><a href="/index.html">x</a> <a href="//example.org/a">y</a></p>')},
    )
    assert rules(check_site(site)) == [
        ("index.html", "root-absolute-link", BODY),
        ("index.html", "root-absolute-link", BODY),
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
            "nav.json": '{\n  "note": "acct-555555555555"\n}\n',
            "assets/site.css": "/* 9876543210987 has thirteen digits */\n",
        },
    )
    problems = check_site(site)
    assert rules(problems) == [
        ("index.html", "account-id", BODY + 1),
        ("nav.json", "account-id", 2),
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
    assert rules(check_site(tmp_path / "missing")) == [("index.html", "missing-index", 1)]


def test_the_real_site_has_no_problems() -> None:
    assert check_site(REPO / "site") == []


def test_an_element_still_open_at_the_end_of_the_file_is_reported(tmp_path: Path) -> None:
    truncated = page("<p>x</p>").removesuffix("</html>\n")
    site = make_site(tmp_path, {"index.html": truncated})
    assert rules(check_site(site)) == [("index.html", "unbalanced-tag", 2)]
