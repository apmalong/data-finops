"""The Simplified Technical English checker (#235): each rule has a passing and a failing case,
and only the converted files are a gate."""

from pathlib import Path

import pytest

from scripts import ste_check


def _words(count: int) -> str:
    return " ".join(["word"] * (count - 1) + ["end."])


def _rules(text: str, glossary: frozenset[str] = frozenset()) -> list[str]:
    return [finding.rule for finding in ste_check.check_text(text, glossary=glossary)]


def test_a_description_sentence_of_25_words_passes_and_26_fails() -> None:
    assert _rules(_words(25)) == []
    findings = ste_check.check_text("Short one.\n" + _words(26))

    assert [(f.line, f.rule) for f in findings] == [(2, "sentence-length")]


def test_a_numbered_step_of_20_words_passes_and_21_fails() -> None:
    assert _rules(f"1. {_words(20)}\n2. {_words(3)}") == []

    assert _rules(f"1. {_words(3)}\n2. {_words(21)}") == ["step-length"]


def test_a_bulleted_item_has_the_description_limit() -> None:
    assert _rules(f"- {_words(25)}") == []


def test_a_paragraph_of_6_sentences_passes_and_7_fails() -> None:
    assert _rules(" ".join(["One sentence."] * 6)) == []

    assert _rules(" ".join(["One sentence."] * 7)) == ["paragraph-length"]


def test_each_list_item_is_its_own_paragraph() -> None:
    items = "\n".join(f"- {' '.join(['One sentence.'] * 4)}" for _ in range(2))

    assert _rules(items) == []


def test_a_semicolon_fails_and_a_period_passes() -> None:
    assert _rules("The run stops. The log shows why.") == []

    assert _rules("The run stops; the log shows why.") == ["semicolon"]


def test_a_character_reference_is_not_a_semicolon() -> None:
    assert _rules("Use the A&amp;B key.") == []


def test_should_and_may_fail_and_must_and_can_pass() -> None:
    assert _rules("You must run it. You can skip it.") == []

    assert _rules("You should run it. You may skip it.") == ["should", "may"]


def test_a_british_spelling_fails_and_the_american_one_passes() -> None:
    assert _rules("Normalize the color of the catalog.") == []
    findings = ste_check.check_text("Normalise the colour.")

    assert [f.rule for f in findings] == ["british-spelling", "british-spelling"]
    assert "normalize" in findings[0].message and "color" in findings[1].message


def test_words_inside_a_word_are_not_matched() -> None:
    assert _rules("The dismay and the colourless... no.") == ["british-spelling"]
    assert _rules("Otherwise use a precise exercise.") == []


def test_an_ing_verb_fails() -> None:
    findings = ste_check.check_text("Run the check before merging.")

    assert [f.rule for f in findings] == ["ing-verb"]
    assert '"merging"' in findings[0].message


def test_an_ing_word_in_a_glossary_term_passes() -> None:
    text = "Each Charging Window and its Charging\nWindows are kept."

    assert _rules(text, glossary=frozenset({"Charging Window"})) == []
    assert _rules(text) == ["ing-verb", "ing-verb"]


def test_known_ing_nouns_and_words_that_are_not_verbs_pass() -> None:
    text = "During a string check, nothing in the warning or the settings is lost."

    assert _rules(text) == []


@pytest.mark.parametrize(
    "one_word",
    [
        "`uv sync --extra api`",  # a code span (project)
        "(with its four extra columns)",  # text in parentheses (8.5)
        '"the quoted text here"',  # quoted text (8.6)
        "“the curly quoted text”",
        "512 MiB",  # a number with its unit (8.6)
        "Snapshot Scope",  # a GLOSSARY term (project)
    ],
)
def test_these_count_as_one_word(one_word: str) -> None:
    glossary = frozenset({"Snapshot Scope"})
    words = ["word"] * 23

    assert _rules(" ".join([*words, one_word, "end."]), glossary) == []
    assert _rules(" ".join(["word", *words, one_word, "end."]), glossary) == ["sentence-length"]


def test_a_hyphenated_word_is_one_word() -> None:
    assert _rules(" ".join(["read-only"] * 25) + ".") == []


def test_a_links_text_counts_and_its_address_does_not() -> None:
    link = "[the deploy guide](../how-to/deploy-to-aws.html)"

    assert _rules(" ".join(["word"] * 21 + [link, "end."])) == []
    assert _rules(" ".join(["word"] * 22 + [link, "end."])) == ["sentence-length"]


def test_a_code_span_is_not_checked_for_words() -> None:
    assert _rules("Run `a; b` with `--may-fail` and `should_retry`.") == []


def test_a_colon_in_a_list_item_ends_a_sentence() -> None:
    item = f"- **The label:** {_words(20)}"
    long_item = f"- {' '.join(['word'] * 10)}: {_words(20)}"

    assert _rules(item) == []
    assert _rules(long_item) == []
    assert _rules(f"{' '.join(['word'] * 10)}: {_words(20)}") == ["sentence-length"]


@pytest.mark.parametrize(
    "skipped",
    [
        "```text\nYou should; it may.\n```",  # code or output
        "~~~\nYou should.\n~~~",
        "    You should.",  # an indented code block
        "> You should, says the owner.",  # a direct quote
        "<!-- You should. -->",
        "<!-- reference:start -->\nYou should.\n<!-- reference:end -->",  # generated
    ],
)
def test_code_output_quotes_comments_and_generated_regions_are_skipped(skipped: str) -> None:
    assert _rules(f"Run it.\n\n{skipped}\n\nYou may.") == ["may"]


def test_front_matter_is_skipped() -> None:
    assert _rules("---\nname: should\n---\n# Title\n\nYou may.") == ["may"]


def test_indented_text_in_a_list_item_is_not_code() -> None:
    assert _rules("- First.\n\n    You should.") == ["should"]
    assert _rules("- First.\n\n    Second.\n\n    You should.") == ["should"]


def test_a_heading_is_its_own_segment() -> None:
    assert _rules("## Getting started\n" + _words(25)) == ["ing-verb"]


def test_each_table_cell_is_checked_and_the_rule_row_is_not() -> None:
    table = "| Instead of | Use |\n|---|---|\n| should | must; can |"

    assert _rules(table) == ["should", "semicolon"]


def test_the_decision_section_of_an_adr_can_be_skipped() -> None:
    adr = "## Context\nYou may.\n\n## Decision\nYou should.\n\n## Consequences\nYou may."

    assert _rules(adr) == ["may", "should", "may"]
    assert [f.rule for f in ste_check.check_text(adr, skip_decision=True)] == ["may", "may"]


def _html_rules(text: str) -> list[tuple[int, str]]:
    return [(f.line, f.rule) for f in ste_check.check_text(text, html=True)]


def test_an_html_paragraph_has_the_description_limit_and_its_own_line() -> None:
    page = f"<main>\n<h1>Title</h1>\n<p>{_words(25)}</p>\n<p>Short.\n{_words(26)}</p>\n</main>"

    assert _html_rules(page) == [(5, "sentence-length")]


def test_an_html_numbered_step_has_the_step_limit_and_a_bullet_does_not() -> None:
    steps = f"<ol>\n<li>{_words(21)}</li>\n<li><p>{_words(3)}</p></li>\n</ol>\n"
    page = steps + f"<ul><li>{_words(21)}</li></ul>"

    assert _html_rules(page) == [(2, "step-length")]


def test_html_code_quotes_scripts_and_generated_regions_are_skipped() -> None:
    page = (
        "<!-- nav:start -->\n<p>You should.</p>\n<!-- nav:end -->\n"
        "<!-- a comment: you should -->\n"
        "<pre><code>you should; it may</code></pre>\n"
        "<blockquote><p>You should, says the owner.</p></blockquote>\n"
        "<script>const should = 1;</script><style>p { color: red; }</style>\n"
        "<p>Run <code>a; should</code> with A&amp;B. You may.</p>"
    )

    assert _html_rules(page) == [(8, "may")]


def test_html_inline_code_is_one_word_and_link_text_counts() -> None:
    sentence = (
        " ".join(["word"] * 22) + ' <code>uv sync --frozen</code> <a href="x.html">two words</a>.'
    )

    assert _html_rules(f"<p>{sentence}</p>") == []
    assert _html_rules(f"<p>word {sentence}</p>") == [(1, "sentence-length")]


def test_each_html_table_cell_is_its_own_segment() -> None:
    cells = "".join(f"<td>{_words(20)}</td>" for _ in range(2))

    assert _html_rules(f"<table><tr>{cells}</tr></table>") == []


def _repo(tmp_path: Path, files: dict[str, str], converted: str = "") -> Path:
    for name, text in {**files, "scripts/ste_converted.txt": converted}.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return tmp_path


def test_a_converted_file_that_breaks_a_rule_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, {"docs/specs/01.md": "Run it.\n\nYou should."}, "docs/specs/01.md\n")

    assert ste_check.main([], root=repo) == 1
    assert "docs/specs/01.md:3: should:" in capsys.readouterr().out


def test_a_file_not_yet_converted_only_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, {"docs/specs/01.md": "You should.", "docs/specs/02.md": "Run it."})

    assert ste_check.main([], root=repo) == 0
    assert "docs/specs/01.md:1: should:" in capsys.readouterr().out


def test_a_converted_file_that_obeys_the_rules_passes(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"site/a.html": "<p>Run it.</p>"}, "# converted\n\nsite/a.html\n")

    assert ste_check.main([], root=repo) == 0


def test_the_gate_reads_only_the_converted_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, {"README.md": "Run it.", "GLOSSARY.md": "You should."}, "README.md\n")

    assert ste_check.main(["--gate"], root=repo) == 0
    assert "GLOSSARY.md" not in capsys.readouterr().out


def test_named_paths_are_checked_and_a_converted_one_is_a_gate(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"a.md": "You should.", "b.md": "You may."}, "b.md\n")

    assert ste_check.main([str(repo / "a.md")], root=repo) == 0
    assert ste_check.main([str(repo / "b.md")], root=repo) == 1


def test_a_converted_path_that_does_not_exist_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, {}, "docs/specs/gone.md\n")

    assert ste_check.main(["--gate"], root=repo) == 2
    assert "docs/specs/gone.md" in capsys.readouterr().err


def test_only_the_documents_in_scope_are_read_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_of_scope = {
        "docs/implementation/progress.md": "You may.",
        "docs/research/notes.md": "You may.",
        ".venv/lib/README.md": "You may.",
        "node_modules/x/README.md": "You may.",
    }
    in_scope = {
        "README.md": "You may.",
        "examples/demo/README.md": "You may.",
        "AGENTS.md": "You may.",
        "GLOSSARY.md": "You may.",
        "docs/specs/01-x.md": "You may.",
        "docs/adr/0001-x.md": "You may.",
        "docs/implementation/architecture-boundaries.md": "You may.",
        "site/how-to/x.html": "<p>You may.</p>",
    }
    ste_check.main([], root=_repo(tmp_path, out_of_scope | in_scope))
    out = capsys.readouterr().out

    assert sorted(line.split(":")[0] for line in out.splitlines() if ": may:" in line) == sorted(
        in_scope
    )


def test_an_accepted_adrs_decision_section_is_skipped_and_a_proposed_ones_is_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = "\n\n## Decision\nYou should.\n\n## Consequences\nRun it.\n"
    repo = _repo(
        tmp_path,
        {
            "docs/adr/0001-a.md": "# ADR 0001\n\nStatus: Accepted (design)" + body,
            "docs/adr/0002-b.md": "# ADR 0002\n\nStatus: Proposed" + body,
        },
    )
    ste_check.main([], root=repo)
    out = capsys.readouterr().out

    assert "0001-a.md" not in out
    assert "docs/adr/0002-b.md:6: should:" in out


def test_the_glossary_terms_come_from_glossary_md(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    glossary = "# Terms\n\n**Charging Window**:\nThe hours that one charge covers.\n"
    repo = _repo(tmp_path, {"GLOSSARY.md": glossary, "README.md": "Each Charging Window ends."})
    ste_check.main([], root=repo)

    assert "README.md" not in capsys.readouterr().out
