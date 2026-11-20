"""The limits that pages state, stamped from the constants that enforce them (#247).

Seam 1: `build`'s stamped HTML. Seam 2: `site build --check`, the drift test."""

import json
from pathlib import Path

import pytest

from scripts.site.__main__ import main as site_main
from scripts.site.build import SEARCH_INDEX, SiteError, build
from tests.unit.site_pages import NAV, make_repo, page

CAP = "src/data_finops/contracts/limits.py"


def _repo(tmp_path: Path, body: str, sources: dict[str, str]) -> Path:
    repo = make_repo(
        tmp_path,
        NAV,
        {
            "index.html": page("Home"),
            "how-to/index.html": page("How-to guides"),
            "how-to/calculate.html": page("Calculate", body=body),
        },
    )
    for path, text in sources.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    return repo


def test_a_pages_limit_is_stamped_from_the_constant_that_enforces_it(tmp_path: Path) -> None:
    body = "<p>Each file is capped at <!-- limit:input-file -->9 GB<!-- /limit -->.</p>\n"
    repo = _repo(tmp_path, body, {CAP: "MAX_INPUT_FILE_BYTES = 512 * 1024 * 1024\n"})
    assert (
        "capped at <!-- limit:input-file -->512 MiB<!-- /limit -->."
        in build(repo)["how-to/calculate.html"]
    )

    (repo / CAP).write_text("MAX_INPUT_FILE_BYTES = 2 * 1024**3\n", encoding="utf-8")
    assert "<!-- limit:input-file -->2 GiB<!-- /limit -->" in build(repo)["how-to/calculate.html"]


def test_a_count_is_stamped_with_thousands_separators(tmp_path: Path) -> None:
    body = "<p>At most <!-- limit:history-pages-max -->x<!-- /limit --> pages.</p>\n"
    repo = _repo(
        tmp_path, body, {"src/data_finops/collection/run.py": "MAX_HISTORY_PAGES = 12000\n"}
    )
    assert (
        "<!-- limit:history-pages-max -->12,000<!-- /limit -->"
        in (build(repo)["how-to/calculate.html"])
    )


def test_a_setting_default_is_stamped_from_the_settings_dataclass(tmp_path: Path) -> None:
    body = "<p><!-- limit:ephemeral-storage-default -->x<!-- /limit --> by default.</p>\n"
    settings = (
        "class Settings:\n"
        "    region: str\n"
        '    """The Region."""\n'
        "    ephemeral_storage_mb: int = 4096\n"
        '    """The /tmp size."""\n'
    )
    repo = _repo(tmp_path, body, {"infra/pulumi/aws/deployment/settings.py": settings})
    assert (
        "<!-- limit:ephemeral-storage-default -->4,096 MB<!-- /limit -->"
        in (build(repo)["how-to/calculate.html"])
    )


def test_a_value_built_from_other_modules_constants_is_followed_to_them(tmp_path: Path) -> None:
    body = "<p>Today <code><!-- limit:policy-version -->x<!-- /limit --></code>.</p>\n"
    sources = {
        "src/data_finops/calculation/__init__.py": (
            "from data_finops.contracts.allocation import POLICY_SEPARATOR\n"
            "from data_finops.providers import athena_billing\n"
            'ENGINE_VERSION = "allocation-engine/7"\n'
            "POLICY_VERSION = POLICY_SEPARATOR.join(\n"
            "    (ENGINE_VERSION, athena_billing.POLICY_VERSION)\n"
            ")\n"
        ),
        "src/data_finops/contracts/allocation.py": 'POLICY_SEPARATOR = "+"\n',
        "src/data_finops/providers/athena_billing.py": (
            'BILLABLE_USAGE_V9 = "athena-billable-usage/9"\nPOLICY_VERSION = BILLABLE_USAGE_V9\n'
        ),
    }
    repo = _repo(tmp_path, body, sources)
    stamped = "allocation-engine/7+athena-billable-usage/9"
    assert (
        f"<!-- limit:policy-version -->{stamped}<!-- /limit -->"
        in (build(repo)["how-to/calculate.html"])
    )


def test_an_unknown_limit_fails_the_build_naming_it(tmp_path: Path) -> None:
    body = "<p><!-- limit:no-such-cap -->1 MiB<!-- /limit --></p>\n"
    repo = _repo(tmp_path, body, {})
    with pytest.raises(SiteError, match=r"how-to/calculate\.html.*no-such-cap"):
        build(repo)


def test_a_limit_whose_constant_is_missing_fails_the_build_naming_it(tmp_path: Path) -> None:
    body = "<p><!-- limit:input-file -->1 MiB<!-- /limit --></p>\n"
    repo = _repo(tmp_path, body, {CAP: "OTHER = 1\n"})
    with pytest.raises(SiteError, match="MAX_INPUT_FILE_BYTES"):
        build(repo)


def test_a_number_with_a_unit_that_is_not_stamped_fails_the_build(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "<p>Each event is capped at 1 MiB.</p>\n", {})
    with pytest.raises(SiteError, match=r"how-to/calculate\.html states '1 MiB'"):
        build(repo)


def test_a_listed_exception_may_state_its_number_by_hand(tmp_path: Path) -> None:
    # Athena keeps 45 days of history: an AWS fact that no data-finops constant enforces.
    nav = [*NAV, {"title": "Ingest history", "path": "how-to/ingest-athena-history.html"}]
    repo = make_repo(
        tmp_path,
        nav,
        {
            "index.html": page("Home"),
            "how-to/index.html": page("How-to guides"),
            "how-to/calculate.html": page("Calculate"),
            "how-to/ingest-athena-history.html": page(
                "Ingest history", body="<p>Athena keeps 45 days of query history.</p>\n"
            ),
        },
    )
    assert "45 days" in build(repo)["how-to/ingest-athena-history.html"]


def test_build_check_names_the_page_when_a_constant_changes_and_the_page_does_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "<p>Capped at <!-- limit:input-file -->x<!-- /limit -->.</p>\n"
    repo = _repo(tmp_path, body, {CAP: "MAX_INPUT_FILE_BYTES = 512 * 1024 * 1024\n"})
    assert site_main(["build"], repo=repo) == 0
    assert site_main(["build", "--check"], repo=repo) == 0
    capsys.readouterr()

    (repo / CAP).write_text("MAX_INPUT_FILE_BYTES = 256 * 1024 * 1024\n", encoding="utf-8")
    assert site_main(["build", "--check"], repo=repo) == 1
    assert "stale: site/how-to/calculate.html" in capsys.readouterr().out


def test_the_search_index_reads_a_stamped_limit_as_plain_words(tmp_path: Path) -> None:
    body = "<p>Each file is capped at <!-- limit:input-file -->x<!-- /limit -->.</p>\n"
    repo = _repo(tmp_path, body, {CAP: "MAX_INPUT_FILE_BYTES = 512 * 1024 * 1024\n"})
    (calculate,) = [
        entry
        for entry in json.loads(build(repo)[SEARCH_INDEX])
        if entry["path"] == "how-to/calculate.html"
    ]
    assert "Each file is capped at 512 MiB." in calculate["text"]


def test_example_output_in_a_pre_block_is_not_a_stated_limit(tmp_path: Path) -> None:
    body = "<pre><code>run.json: not collected, 25 days old, expires in 5 days</code></pre>\n"
    repo = _repo(tmp_path, body, {})
    assert "25 days old" in build(repo)["how-to/calculate.html"]
