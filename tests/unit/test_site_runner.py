"""The page runner (spec 07, "Runnable pages"), tested only through `run_page` on tiny pages."""

from pathlib import Path

import pytest

from scripts.site.runner import RunProblem, run_page

REPO = Path(__file__).resolve().parents[2]
EXPORT = "fixtures/focus/export-2026-10.json"


def page(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "page.html"
    html = f'<!doctype html>\n<html lang="en">\n<body>\n{body}\n</body>\n</html>\n'
    path.write_text(html, encoding="utf-8")
    return path


def where(problems: list[RunProblem]) -> list[tuple[int, int]]:
    return [(p.block, p.line) for p in problems]


def test_a_page_whose_commands_run_and_whose_checked_output_matches_passes(
    tmp_path: Path,
) -> None:
    path = page(
        tmp_path,
        f'<pre><code class="run">data-finops ingest focus {EXPORT} --db demo.sqlite\n'
        "data-finops calculate --db demo.sqlite</code></pre>\n"
        '<pre class="output checked">focus: new …\n'
        "calculate: 123456789012 2026-10-01 … new calc-…</pre>",
    )
    assert run_page(path, REPO) == []


def test_only_data_finops_and_curl_may_run(tmp_path: Path) -> None:
    path = page(tmp_path, '<pre><code class="run">data-finops --help\nrm -rf fixtures</code></pre>')
    problems = run_page(path, REPO)
    assert where(problems) == [(1, 5)]
    assert "page.html" in problems[0].page
    assert "only data-finops and curl lines may run, not 'rm'" in problems[0].message


def test_a_failing_command_is_named_with_its_exit_code_and_its_block_stops(
    tmp_path: Path,
) -> None:
    path = page(
        tmp_path,
        '<pre><code class="run">data-finops --help</code></pre>\n'
        f'<pre><code class="run">data-finops ingest focus {EXPORT} --db d.sqlite\n'
        "data-finops ingest focus fixtures/none.json --db d.sqlite\n"
        "data-finops calculate --db d.sqlite</code></pre>",
    )
    problems = run_page(path, REPO)
    assert where(problems) == [(2, 6)]
    assert "exited 2" in problems[0].message
    assert "focus: new" not in problems[0].message  # only the failing line's output


INGEST = f'<pre><code class="run">data-finops ingest focus {EXPORT} --db d.sqlite</code></pre>\n'


def test_checked_output_must_match_line_by_line_and_other_output_is_ignored(
    tmp_path: Path,
) -> None:
    path = page(
        tmp_path,
        INGEST
        + '<pre class="output">anything at all</pre>\n'  # not checked
        + INGEST
        + '<pre class="output checked">focus: new aws-…-v1</pre>\n'  # replay
        + INGEST
        + '<pre class="output checked">focus: replay aws-…-v2</pre>',
    )
    problems = run_page(path, REPO)
    assert where(problems) == [(2, 7), (3, 9)]
    assert "focus: replay aws-focus-123456789012-2026-10-v1" in problems[0].message


def test_an_ellipsis_stands_for_text_within_one_line_never_for_lines(tmp_path: Path) -> None:
    path = page(
        tmp_path,
        f'<pre><code class="run">data-finops ingest focus {EXPORT} --db d.sqlite\n'
        "data-finops calculate --db d.sqlite</code></pre>\n"
        '<pre class="output checked">focus: new …</pre>',
    )
    problems = run_page(path, REPO)
    assert where(problems) == [(1, 6)]
    assert "calculate: " in problems[0].message


def test_serve_opens_the_api_and_curl_reads_it_through_the_test_client(tmp_path: Path) -> None:
    path = page(
        tmp_path,
        INGEST + '<pre><code class="run">data-finops calculate --db d.sqlite\n'
        "data-finops serve --db d.sqlite</code></pre>\n"
        '<pre class="output checked">calculate: …\n'
        "serving d.sqlite at http://127.0.0.1:8000/ (Ctrl+C to stop)</pre>\n"
        '<pre><code class="run">curl -s http://127.0.0.1:8000/health\n'
        "curl -s 'http://127.0.0.1:8000/v1/costs/summary?cost_basis=EffectiveCost'</code></pre>\n"
        '<pre class="output checked">{"status":"ok"}\n'
        '{"summaries":[{"scope":{"billing_account_id":"123456789012",…'
        '"cost_basis":"EffectiveCost",…</pre>',
    )
    assert run_page(path, REPO) == []


def test_curl_needs_a_served_api_and_takes_only_its_known_options(tmp_path: Path) -> None:
    path = page(
        tmp_path,
        '<pre><code class="run">curl -s http://127.0.0.1:8000/health</code></pre>\n'
        + INGEST
        + '<pre><code class="run">data-finops serve --db d.sqlite\n'
        "curl -s -o out.json http://127.0.0.1:8000/health</code></pre>",
    )
    problems = run_page(path, REPO)
    assert where(problems) == [(1, 4), (3, 7)]
    assert "curl needs the API" in problems[0].message and "-o" in problems[1].message


def test_a_line_the_shell_could_not_split_is_named_not_fatal(tmp_path: Path) -> None:
    path = page(tmp_path, '<pre><code class="run">data-finops ingest focus "unclosed</code></pre>')
    problems = run_page(path, REPO)
    assert where(problems) == [(1, 4)]
    assert "quotation" in problems[0].message


def test_a_page_runs_the_same_whatever_data_finops_settings_the_caller_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_FINOPS_TOKEN", "a-token-the-page-never-mentions")
    path = page(
        tmp_path,
        INGEST + '<pre><code class="run">data-finops serve --db d.sqlite\n'
        "curl -s http://127.0.0.1:8000/health</code></pre>\n"
        '<pre class="output checked">serving …\n{"status":"ok"}</pre>',
    )
    assert run_page(path, REPO) == []


def test_checked_output_that_follows_no_run_block_is_reported(tmp_path: Path) -> None:
    path = page(
        tmp_path,
        '<pre class="output checked">focus: new …</pre>\n'
        + INGEST
        + "<hr>\n"
        + '<pre class="output checked">focus: new …</pre>\n'
        + INGEST
        + "Bare text between.\n"
        + '<pre class="output checked">focus: replay …</pre>\n'
        + INGEST
        + '<pre class="output">illustrative, not checked</pre>'
        + '<pre class="output checked">focus: replay …</pre>\n'
        + INGEST
        + '<pre class="output checked">focus: replay …</pre>',
    )
    problems = run_page(path, REPO)
    assert [(p.block, p.line) for p in problems] == [(0, 4), (1, 7), (2, 10), (3, 12)]
    assert all("follows no run block" in p.message for p in problems)


SERVE = '<pre><code class="run">data-finops serve --db d.sqlite --port 8123</code></pre>\n'


def test_demo_cannot_run_in_a_page(tmp_path: Path) -> None:
    path = page(tmp_path, '<pre><code class="run">data-finops demo</code></pre>')
    problems = run_page(path, REPO)
    assert where(problems) == [(1, 4)]
    assert "demo" in problems[0].message


def test_curl_must_name_the_served_address_and_get_a_success(tmp_path: Path) -> None:
    path = page(
        tmp_path,
        INGEST
        + SERVE
        + '<pre><code class="run">curl -s http://127.0.0.1:8000/health</code></pre>\n'
        + '<pre><code class="run">curl -s http://127.0.0.1:8123/v1/nowhere</code></pre>\n'
        + '<pre><code class="run">curl -s http://127.0.0.1:8123/health</code></pre>',
    )
    problems = run_page(path, REPO)
    assert where(problems) == [(3, 6), (4, 7)]
    assert "http://127.0.0.1:8123" in problems[0].message and "404" in problems[1].message


def test_lines_of_checked_output_count_from_where_its_text_starts(tmp_path: Path) -> None:
    path = page(tmp_path, INGEST + '<pre class="output checked">\nfocus: old …</pre>')
    assert where(run_page(path, REPO)) == [(1, 6)]


def test_a_relative_repository_path_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = page(tmp_path, INGEST)
    monkeypatch.chdir(REPO)
    assert run_page(path, Path(".")) == []


BEARER = "Authorization: Bearer $(cat fixtures/docs/example.token)"
TOKEN_SERVE = (
    '<pre><code class="run">data-finops serve --db d.sqlite'
    " --token-file fixtures/docs/example.token</code></pre>\n"
)


def test_curl_sends_its_headers_so_a_token_served_api_answers(tmp_path: Path) -> None:
    path = page(
        tmp_path,
        INGEST
        + TOKEN_SERVE
        + '<pre><code class="run">curl -s http://127.0.0.1:8000/health</code></pre>\n'
        + f'<pre><code class="run">curl -s -H "{BEARER}"'
        " http://127.0.0.1:8000/health</code></pre>\n"
        '<pre class="output checked">{"status":"ok"}</pre>',
    )
    problems = run_page(path, REPO)
    assert where(problems) == [(3, 6)]
    assert "401" in problems[0].message


def test_cat_of_a_file_stands_for_its_text_as_in_a_shell(tmp_path: Path) -> None:
    """`$(cat FILE)` lets a page send a token without writing it down."""
    health = "http://127.0.0.1:8000/health"
    path = page(
        tmp_path,
        INGEST
        + TOKEN_SERVE
        + f'<pre><code class="run">curl -s -H "Authorization: Bearer $(cat missing.token)" {health}'
        "</code></pre>\n" + f"<pre><code class=\"run\">curl -s -H '{BEARER}'"
        f" {health}</code></pre>",
    )
    problems = run_page(path, REPO)
    assert where(problems) == [(3, 6), (4, 7)]
    assert "missing.token" in problems[0].message and "single quotes" in problems[1].message


POST_EVENT = (
    "curl -s -X POST -H 'Content-Type: application/json'"
    " --data-binary @fixtures/openlineage/one-event.json"
    " http://127.0.0.1:8000/api/v1/lineage"
)
# A body alone makes it a POST, as in curl; a header may leave out the space after its colon.
IMPLIED_POST = POST_EVENT.replace("-X POST ", "").replace("Content-Type: ", "Content-Type:")


def test_curl_posts_a_file_to_the_receiver(tmp_path: Path) -> None:
    path = page(
        tmp_path,
        INGEST
        + '<pre><code class="run">data-finops serve --db d.sqlite</code></pre>\n'
        + f'<pre><code class="run">{POST_EVENT}\n{IMPLIED_POST}</code></pre>\n'
        '<pre class="output checked">{"accepted":1,"duplicates":0,"changed_copies":0}\n'
        '{"accepted":0,"duplicates":1,"changed_copies":0}</pre>',
    )
    assert run_page(path, REPO) == []


@pytest.mark.parametrize(
    ("args", "complaint"),
    [
        ("-X DELETE http://127.0.0.1:8000/health", "DELETE"),
        ("--data-binary '{}' http://127.0.0.1:8000/api/v1/lineage", "@"),
        ("--data-binary @missing.json http://127.0.0.1:8000/api/v1/lineage", "missing.json"),
        ("-H http://127.0.0.1:8000/health", "Name: value"),
        ("-H 'Authorization: Bearer x'", "exactly one URL"),
        ("-d @fixtures/openlineage/one-event.json http://127.0.0.1:8000/api/v1/lineage", "-d"),
        ("http://127.0.0.1:8000/health -X", "-X needs a value"),
        (
            "--data-binary @fixtures/openlineage/one-event.json http://127.0.0.1:8000/api/v1/lineage",
            "Content-Type",
        ),
    ],
)
def test_curl_refuses_what_it_cannot_send(tmp_path: Path, args: str, complaint: str) -> None:
    path = page(
        tmp_path,
        INGEST
        + '<pre><code class="run">data-finops serve --db d.sqlite</code></pre>\n'
        + f'<pre><code class="run">curl -s {args}</code></pre>',
    )
    problems = run_page(path, REPO)
    assert where(problems) == [(3, 6)]
    assert complaint in problems[0].message


def test_calculation_run_id_stands_for_the_run_calculate_last_printed(tmp_path: Path) -> None:
    path = page(
        tmp_path,
        INGEST + '<pre><code class="run">data-finops calculate --db d.sqlite\n'
        "data-finops serve --db d.sqlite\n"
        'curl -s -H "X-Note: it\'s quoted"'
        ' "http://127.0.0.1:8000/v1/provenance?calculation_run_id=$CALCULATION_RUN_ID"\n'
        "curl -s -H 'Accept: application/json'"
        ' "http://127.0.0.1:8000/v1/allocations/${CALCULATION_RUN_ID}%230/explanation"'
        "</code></pre>\n"
        '<pre class="output checked">calculate: … new calc-…\n'
        "serving …\n"
        '{"calculation_run_id":"calc-…","is_current":true,…\n'
        '{"ref":"calc-…#0",…</pre>',
    )
    assert run_page(path, REPO) == []


def test_calculation_run_id_needs_a_calculation_and_double_quotes(tmp_path: Path) -> None:
    url = "http://127.0.0.1:8000/v1/provenance?calculation_run_id=$CALCULATION_RUN_ID"
    path = page(
        tmp_path,
        INGEST
        + '<pre><code class="run">data-finops serve --db d.sqlite</code></pre>\n'
        + f'<pre><code class="run">curl -s "{url}"</code></pre>\n'
        + '<pre><code class="run">data-finops calculate --db d.sqlite</code></pre>\n'
        + f"<pre><code class=\"run\">curl -s '{url}'</code></pre>",
    )
    problems = run_page(path, REPO)
    assert where(problems) == [(3, 6), (5, 8)]
    assert "calculate" in problems[0].message and "single quotes" in problems[1].message
