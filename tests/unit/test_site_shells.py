"""Windows and macOS/Linux versions of every command block (#173, spec 07 "Layout")."""

from pathlib import Path

import pytest

from scripts.site.build import SiteError, build
from tests.unit.site_pages import NAV, make_repo, page


def built(tmp_path: Path, body: str, *, source: str = "") -> str:
    repo = make_repo(
        tmp_path,
        NAV,
        {
            "index.html": page("Home"),
            "how-to/index.html": page("How-to guides"),
            "how-to/calculate.html": source or page("Calculate", body=body),
        },
    )
    return build(repo)["how-to/calculate.html"]


def windows(html: str) -> str:
    """The text of the page's first Windows block, without its line spans."""
    start = html.index('<pre class="shell-windows"')
    block = html[start : html.index("</pre>", start)]
    for tag in ('<span class="line">', "</span>", "<code>", '<code class="run">', "</code>"):
        block = block.replace(tag, "")
    return block.split(">", 1)[1]


def test_a_command_block_gets_a_windows_version_beside_it(tmp_path: Path) -> None:
    html = built(
        tmp_path, '<pre><code class="run">data-finops calculate --db d.sqlite</code></pre>\n'
    )
    assert html.count('<div class="shell-tabs">') == 1
    assert '<pre class="shell-posix"><code class="run">' in html
    assert '<pre class="shell-windows" data-derived=""><code>' in html  # never class="run"
    assert windows(html) == "data-finops calculate --db d.sqlite"


@pytest.mark.parametrize(
    ("posix", "powershell"),
    [
        ("curl -s 'http://x/health'", "curl.exe -s 'http://x/health'"),
        (
            'export DATA_FINOPS_TOKEN="$(cat finops.token)"',
            '$env:DATA_FINOPS_TOKEN = "$(cat finops.token)"',
        ),
        (
            "export OPENLINEAGE_URL=http://127.0.0.1:8000",
            '$env:OPENLINEAGE_URL = "http://127.0.0.1:8000"',
        ),
        ("CALCULATION_RUN_ID=calc-1", '$CALCULATION_RUN_ID = "calc-1"'),
        (
            "curl -s --data-binary @fixtures/e.json http://x/api",
            "curl.exe -s --data-binary '@fixtures/e.json' http://x/api",
        ),
        ('curl -s "http://x/$CALCULATION_RUN_ID"', 'curl.exe -s "http://x/$CALCULATION_RUN_ID"'),
        ("data-finops ingest dbt fixtures/curl.json", "data-finops ingest dbt fixtures/curl.json"),
        ("curl -s -H 'Accept: a|b' http://x", "curl.exe -s -H 'Accept: a|b' http://x"),
    ],
)
def test_each_rule_turns_one_shell_line_into_powershell(
    tmp_path: Path, posix: str, powershell: str
) -> None:
    html = built(tmp_path, f"<pre><code>{posix}</code></pre>\n")
    assert windows(html) == powershell


@pytest.mark.parametrize(
    "line",
    [
        "aws athena x --output json &gt; out.json",
        "cd a &amp;&amp; ls",
        'export X="$(cd .. &amp;&amp; pwd)"',
        "data-finops ingest \\",
    ],
)
def test_a_line_the_rules_cannot_translate_fails_the_build_naming_it(
    tmp_path: Path, line: str
) -> None:
    with pytest.raises(SiteError, match=r"how-to/calculate\.html.*Windows.*line 1"):
        built(tmp_path, f"<pre><code>{line}</code></pre>\n")


def test_a_hand_written_windows_block_is_used_as_written(tmp_path: Path) -> None:
    body = (
        "<pre><code>aws athena x --output json &gt; out.json</code></pre>\n"
        '<pre class="windows"><code>cmd /c "aws athena x --output json &gt; out.json"'
        "</code></pre>\n"
    )
    html = built(tmp_path, body)
    assert '<pre class="shell-windows"><code>' in html  # hand-written: not data-derived
    assert windows(html) == 'cmd /c "aws athena x --output json &gt; out.json"'
    assert html.count('<pre class="windows">') == 0


def test_output_and_usage_blocks_get_no_tabs(tmp_path: Path) -> None:
    body = (
        '<pre class="output checked">focus: new</pre>\n'
        '<pre class="usage"><code>usage: data-finops calculate</code></pre>\n'
    )
    assert "shell-tabs" not in built(tmp_path, body)


def test_building_a_built_page_changes_nothing(tmp_path: Path) -> None:
    body = (
        '<pre><code class="run">curl -s http://x/a\ncurl -s http://x/b</code></pre>\n'
        "<pre><code>aws x &gt; y</code></pre>\n"
        '<pre class="windows"><code>cmd /c "aws x &gt; y"</code></pre>\n'
    )
    first = built(tmp_path / "1", body)
    assert built(tmp_path / "2", "", source=first) == first


def test_every_page_loads_the_tab_script_after_parsing(tmp_path: Path) -> None:
    html = built(tmp_path, "")
    assert '<script src="../assets/site.js" defer></script>' in html


def test_an_edited_block_on_a_built_page_gets_a_fresh_windows_version(tmp_path: Path) -> None:
    first = built(tmp_path / "1", '<pre><code class="run">curl -s http://x/a</code></pre>\n')
    edited = first.replace("curl -s http://x/a</span>", "curl -s http://x/z</span>", 1)
    assert "http://x/z" in edited
    assert windows(built(tmp_path / "2", "", source=edited)) == "curl.exe -s http://x/z"
