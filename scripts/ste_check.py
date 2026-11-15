"""Check documents against the Simplified Technical English rules (#235).

    python -m scripts.ste_check               # every document in scope (#234)
    python -m scripts.ste_check PATH...       # only these documents
    python -m scripts.ste_check --gate        # only the converted documents (a gate)

The rules are the `simplified-technical-english` skill's (`.claude/skills/`). The check reports
sentences and numbered steps over their word limits, long paragraphs, semicolons, "should",
"may", "-ing" verbs and British spellings, by file and line. It reads Markdown and HTML, and it
skips code, output, quotes, comments, generated regions and an accepted ADR's Decision section.

A finding in a document that `scripts/ste_converted.txt` lists fails the check (exit 1). Other
documents only report. The check cannot find names, titles and labels, so it counts each of
their words (8.6). It does not read Python, and it skips generated regions, so it does not
check help text, docstrings or the generated reference pages.

Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

DESCRIPTION_LIMIT = 25  # words in a description or note sentence (6.3, 5.5)
STEP_LIMIT = 20  # words in a sentence of a numbered procedure step (5.1)
PARAGRAPH_LIMIT = 6  # sentences in a paragraph (6.6)

UNAPPROVED = {
    "should": 'write "must", or "we recommend that"',
    "may": 'write "can"',
}
"""Words the skill names as not approved, and what to write in their place."""

_ISE_VERBS = (
    "anonym", "author", "capital", "categor", "central", "character", "custom", "deserial",
    "digit", "emphas", "final", "general", "initial", "item", "local", "maxim", "memor", "minim",
    "normal", "optim", "organ", "parameter", "priorit", "random", "real", "recogn", "sanit",
    "serial", "special", "stabil", "standard", "summar", "synchron", "token", "util", "visual",
)  # fmt: skip
_OUR_NOUNS = ("behaviour", "colour", "favour", "flavour", "honour", "labour", "neighbour")
_RE_NOUNS = ("centre", "fibre", "litre", "metre", "theatre")
_DOUBLED_L = ("cancel", "channel", "fuel", "label", "level", "model", "signal", "total", "travel")
_OTHER_BRITISH = {
    "analyse": "analyze", "analysed": "analyzed", "analyses": "analyzes", "analysing": "analyzing",
    "catalogue": "catalog", "catalogues": "catalogs", "artefact": "artifact",
    "artefacts": "artifacts", "licence": "license", "licences": "licenses", "defence": "defense",
    "offence": "offense", "programme": "program", "programmes": "programs", "grey": "gray",
    "whilst": "while", "amongst": "among", "enrolment": "enrollment", "fulfil": "fulfill",
    "instalment": "installment", "sceptical": "skeptical", "aluminium": "aluminum",
}  # fmt: skip


def _british() -> dict[str, str]:
    words = dict(_OTHER_BRITISH)
    for stem in _ISE_VERBS:
        for british, american in (
            ("ise", "ize"), ("ised", "ized"), ("ises", "izes"), ("ising", "izing"),
            ("isation", "ization"), ("isations", "izations"), ("iser", "izer"),
        ):  # fmt: skip
            words[stem + british] = stem + american
    for noun in _OUR_NOUNS:
        american = noun.replace("our", "or")
        for suffix in ("", "s", "ed", "ing", "al", "ful", "less", "ite", "able"):
            words[noun + suffix] = american + suffix
    for noun in _RE_NOUNS:
        for suffix in ("", "s", "d"):
            words[noun + suffix] = noun[:-2] + "er" + {"d": "ed"}.get(suffix, suffix)
    for verb in _DOUBLED_L:
        for suffix in ("led", "ling", "ler", "lers"):
            words[verb + suffix] = verb + suffix[1:]
    return words


BRITISH = _british()
"""British spellings and the American spellings to write in their place (1.14)."""

TECHNICAL_ING = frozenset({
    "accounting", "billing", "caching", "docstring", "encoding", "engineering", "floating",
    "hashing", "heading", "logging", "mapping", "padding", "pricing", "rounding", "routing",
    "setting", "staging", "tagging", "tooling", "versioning", "warning",
})  # fmt: skip
"""Technical nouns that end in "-ing" (3.5). GLOSSARY terms pass too. Add a word only when it
is a noun in every use in our documents."""
_NOT_VERBS = frozenset({"ceiling", "during", "evening", "meaning", "morning"})
"""Words that end in "-ing" but are not verb forms."""


@dataclass(frozen=True)
class Finding:
    line: int
    rule: str
    message: str


@dataclass(frozen=True)
class _Segment:
    """One block of prose: its first line and its text, newlines kept for line numbers."""

    line: int
    text: str
    step: bool = False  # a numbered procedure step
    listed: bool = False  # a list item, numbered or not

    def replaced(self, pattern: re.Pattern[str], template: str) -> _Segment:
        """The segment with each match of `pattern` replaced by `template`, newlines kept. The
        template is a placeholder word, or a group reference as `re.Match.expand` reads it."""

        def replace(match: re.Match[str]) -> str:
            kept = match.expand(template)
            return kept + "\n" * (match.group().count("\n") - kept.count("\n"))

        return _Segment(self.line, pattern.sub(replace, self.text), self.step, self.listed)


_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_CODE_SPAN = re.compile(r"(`+)(?!`).+?(?<!`)\1", re.DOTALL)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_EMPHASIS = re.compile(r"(?<!\w)[*_]{1,3}|[*_]{1,3}(?!\w)")
_CHARACTER_REFERENCE = re.compile(r"&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z]+);")
_PARENTHESES = re.compile(r"\([^()]*\)")
_QUOTED = re.compile(r'"[^"]*"|“[^”]*”')
_UNITS = r"%|[KMGTP]i?B|bytes?|ms|seconds?|minutes?|hours?|days?|weeks?|months?|years?"
_NUMBER_WITH_UNIT = re.compile(rf"(?<![\w.])\d[\d.,]*\s*(?:{_UNITS})(?!\w)")
_SENTENCE_END = re.compile(r"[.!?]+(?:\s+|$)")
_SENTENCE_END_IN_LIST = re.compile(r"[.!?]+(?:\s+|$)|:(?:\s+|$)")  # for the word count (8.4)
_ABBREVIATION = re.compile(r"(?<!\w)(?:e\.g|i\.e|vs|cf)\.", re.IGNORECASE)  # not a sentence end
_LIST_ITEM = re.compile(r"^\s*(?:(?P<number>\d+[.)])|[-*+])\s+")
_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(?P<text>.*?)[\s#]*$")
_TERM_LINE = re.compile(r"^\*\*(?P<text>[^*]+)\*\*:\s*$")  # GLOSSARY: a term, as a heading
_AVOID_LINE = re.compile(r"^\s*_Avoid_:")  # GLOSSARY: words to avoid, mentioned, not used
_BLOCK_START = re.compile(r"^\s*(?:#{1,6}\s|>|\|)")  # a heading, a quote or a table row
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
_FRONT_MATTER = re.compile(r"^---\s*$")
_FENCE = re.compile(r"^ {0,3}(?P<mark>`{3,}|~{3,})")
_REGION_START = re.compile(r"<!--\s*(?P<name>[\w-]+):start\s*-->")  # the site build's regions
_COMMENT = re.compile(r"<!--.*?-->")
_COMMENT_END = re.compile(r"-->")
_DECISION = re.compile(r"^##\s+Decision\s*$")
_DECISION_END = re.compile(r"^#{1,2}\s")


def check_text(
    text: str,
    *,
    html: bool = False,
    glossary: frozenset[str] = frozenset(),
    skip_decision: bool = False,
) -> list[Finding]:
    """Every rule broken in a Markdown document (or, with `html`, an HTML page), in line order.

    `glossary` holds the GLOSSARY terms, which count as one word and pass the "-ing" rule.
    `skip_decision` skips a Markdown `## Decision` section, which does not change in an accepted
    ADR."""
    terms = _terms_pattern(glossary)
    segments = _html_segments(text) if html else _markdown_segments(text, skip_decision)
    findings: list[Finding] = []
    for segment in segments:
        findings.extend(_check_segment(_mask_words(segment, terms)))
    return sorted(findings, key=lambda finding: finding.line)  # stable: same line, rule order


def _terms_pattern(glossary: frozenset[str]) -> re.Pattern[str] | None:
    if not glossary:
        return None
    longest_first = sorted(glossary, key=len, reverse=True)
    alternatives = "|".join(r"\s+".join(map(re.escape, term.split())) for term in longest_first)
    return re.compile(rf"(?<!\w)(?:{alternatives})s?(?!\w)")


def _mask_words(segment: _Segment, terms: re.Pattern[str] | None) -> _Segment:
    """The text that the word rules read: code spans, quoted text and GLOSSARY terms become one
    placeholder word each, a link becomes its text, and emphasis marks go. Quoted text is not
    our wording, and 8.6 counts it as one word."""
    segment = segment.replaced(_CODE_SPAN, "CODE").replaced(_QUOTED, "QUOTED")
    segment = segment.replaced(_LINK, r"\1").replaced(_EMPHASIS, "")
    segment = segment.replaced(_CHARACTER_REFERENCE, "&")
    return segment if terms is None else segment.replaced(terms, "TERM")


def _mask_counts(segment: _Segment) -> _Segment:
    """The text that the sentence rules read: what 8.5 and 8.6 count as one word becomes one
    placeholder word, and a Latin abbreviation does not end a sentence."""
    while _PARENTHESES.search(segment.text):  # inner parentheses first
        segment = segment.replaced(_PARENTHESES, "PARENTHESES")
    segment = segment.replaced(_NUMBER_WITH_UNIT, "NUMBER")
    return segment.replaced(_ABBREVIATION, "ABBREVIATION")


def _check_segment(segment: _Segment) -> Iterator[Finding]:
    yield from _check_words(segment)
    counted = _mask_counts(segment)
    paragraph = sum(1 for _ in _sentences(counted, _SENTENCE_END))
    if paragraph > PARAGRAPH_LIMIT:
        yield Finding(
            segment.line,
            "paragraph-length",
            f"{paragraph} sentences in a paragraph, over {PARAGRAPH_LIMIT}",
        )
    sentences = _sentences(counted, _SENTENCE_END_IN_LIST if segment.listed else _SENTENCE_END)
    rule, limit = (
        ("step-length", STEP_LIMIT) if segment.step else ("sentence-length", DESCRIPTION_LIMIT)
    )
    for line, sentence in sentences:
        words = _word_count(sentence)
        if words > limit:
            yield Finding(line, rule, f"{words} words in a sentence, over {limit}")


def _check_words(segment: _Segment) -> Iterator[Finding]:
    text = segment.text
    for offset, character in enumerate(text):
        if character == ";":
            yield Finding(_line(text, segment.line, offset), "semicolon", "write a period (8.1)")
    for match in _WORD.finditer(text):
        word, line = match.group().lower(), _line(text, segment.line, match.start())
        if word in UNAPPROVED:
            yield Finding(line, word, f'"{match.group()}" is not approved: {UNAPPROVED[word]}')
        elif word in BRITISH:
            yield Finding(
                line, "british-spelling", f'"{match.group()}" is British: write "{BRITISH[word]}"'
            )
        elif _is_ing_verb(word):
            yield Finding(
                line,
                "ing-verb",
                f'"{match.group()}": use "-ing" only in a technical noun (3.5)',
            )


def _is_ing_verb(word: str) -> bool:
    stem = word.removesuffix("ing")
    return (
        stem != word
        and any(vowel in stem for vowel in "aeiouy")  # not "string", "bring" or "thing"
        and not word.endswith("thing")  # "nothing", "something"
        and word not in _NOT_VERBS
        and word not in TECHNICAL_ING
    )


def _line(text: str, first: int, offset: int) -> int:
    return first + text.count("\n", 0, offset)


def _markdown_lines(text: str, skip_decision: bool) -> Iterator[tuple[int, str]]:
    """Each line, numbered, with the lines that are not prose made empty: front matter, code
    and output blocks, comments, generated regions and (with `skip_decision`) the Decision
    section."""
    end: re.Pattern[str] | None = None  # the end of the skipped block that this line is in
    end_is_prose = False  # the line that ends the Decision section is the next heading
    for number, line in enumerate(text.splitlines(), start=1):
        if end is not None:
            if not end.search(line):
                yield number, ""
                continue
            end = None
            if not end_is_prose:
                yield number, ""
                continue
        end_is_prose = False
        prose = _CODE_SPAN.sub("CODE", line)  # a marker in a code span is text, not markup
        if number == 1 and _FRONT_MATTER.match(line):
            end = _FRONT_MATTER
        elif fence := _FENCE.match(line):
            mark, length = fence["mark"][0], len(fence["mark"])
            end = re.compile(rf"^ {{0,3}}{re.escape(mark)}{{{length},}}\s*$")
        elif region := _REGION_START.search(prose):
            end = re.compile(rf"<!--\s*{re.escape(region['name'])}:end\s*-->")
        elif "<!--" in (prose := _COMMENT.sub("", prose)):
            end = _COMMENT_END
        elif skip_decision and _DECISION.match(line):
            end, end_is_prose = _DECISION_END, True
        else:
            yield number, prose
            continue
        yield number, ""


def _markdown_segments(text: str, skip_decision: bool = False) -> Iterator[_Segment]:
    lines: list[str] = []
    start, step, listed, in_list = 0, False, False, False

    def flush() -> Iterator[_Segment]:
        if lines:
            yield _Segment(start, "\n".join(lines), step, listed)
            lines.clear()

    for number, line in _markdown_lines(text, skip_decision):
        item = _LIST_ITEM.match(line)
        indented = line.startswith(("    ", "\t"))
        if not line.strip() or item or _BLOCK_START.match(line):
            yield from flush()
        if not line.strip() or line.lstrip().startswith(">"):  # a blank line or a quote
            continue
        if heading := _HEADING.match(line) or _TERM_LINE.match(line):
            yield from flush()
            yield _Segment(number, heading["text"])
            in_list = False
            continue
        if _AVOID_LINE.match(line):
            yield from flush()
            continue
        if line.lstrip().startswith("|"):
            if not _TABLE_RULE.match(line):
                cells = line.strip().strip("|").split("|")
                yield from (_Segment(number, cell) for cell in cells if cell.strip())
            continue
        if indented and not lines and not in_list:  # an indented code block
            continue
        if not lines:
            start, step, listed = number, bool(item and item["number"]), bool(item)
            in_list = bool(item) or (in_list and indented)
        lines.append(line[item.end() :] if item else line)
    yield from flush()


def _sentences(segment: _Segment, ends: re.Pattern[str]) -> Iterator[tuple[int, str]]:
    text, begin = segment.text, 0
    for match in ends.finditer(text):
        yield _first_line(segment, begin), text[begin : match.end()]
        begin = match.end()
    if text[begin:].strip():
        yield _first_line(segment, begin), text[begin:]


def _first_line(segment: _Segment, offset: int) -> int:
    """The line where the sentence at `offset` starts, after its leading whitespace."""
    while offset < len(segment.text) and segment.text[offset].isspace():
        offset += 1
    return _line(segment.text, segment.line, offset)


def _word_count(sentence: str) -> int:
    return sum(1 for token in sentence.split() if any(c.isalnum() for c in token))


_HTML_BLOCKS = frozenset({
    "p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "dt", "dd", "caption",
    "figcaption", "summary",
})  # fmt: skip
"""Elements whose text is one segment."""
_HTML_BREAKS = _HTML_BLOCKS | frozenset({
    "article", "aside", "body", "br", "details", "div", "dl", "figure", "footer", "head",
    "header", "hr", "html", "main", "nav", "ol", "section", "table", "tbody", "thead", "title",
    "tr", "ul",
})  # fmt: skip
"""Elements that end the text before them."""
_HTML_SKIPPED = frozenset({"blockquote", "pre", "script", "style", "svg", "template"})
"""Elements whose text is not prose: quotes, code and output, and what is not text."""
_HTML_CODE = frozenset({"code", "kbd", "samp", "var"})
"""Inline code, which counts as one word."""
_HTML_REGION = re.compile(r"^\s*(?P<name>[\w-]+):(?P<edge>start|end)\s*$")
CHECKED_REGIONS = frozenset({"reference"})
"""Generated regions that are checked: the reference text that `scripts/site` makes from
its sources (#237). The other regions (head, nav, onpage, pager, pages, footer) are layout."""


class _HtmlSegments(HTMLParser):
    """The prose segments of an HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.segments: list[_Segment] = []
        self._text: list[str] = []
        self._line = 0
        self._lists: list[str] = []  # the open `ol` and `ul` elements
        self._items = 0  # the open `li` elements
        self._skipped = 0  # the open skipped elements
        self._code = 0  # the open inline code elements
        self._region: str | None = None  # the generated region that the parser is in
        self._avoid: str | None = None  # the open element that lists words to avoid

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._region or self._skipped or self._avoid:
            self._skipped += tag in _HTML_SKIPPED and not (self._region or self._avoid)
            return
        if "avoid" in (dict(attrs).get("class") or "").split():  # the glossary's Avoid lists
            self._flush()
            self._avoid = tag
        elif tag in _HTML_SKIPPED:
            self._flush()
            self._skipped = 1
        elif tag in _HTML_CODE:
            if not self._code:
                self._add(" CODE ")
            self._code += 1
        elif tag in _HTML_BREAKS:
            self._flush()
            if tag in ("ol", "ul"):
                self._lists.append(tag)
            self._items += tag == "li"

    def handle_endtag(self, tag: str) -> None:
        if self._region:
            return
        if self._avoid:
            self._avoid = None if tag == self._avoid else self._avoid
        elif self._skipped:
            self._skipped -= tag in _HTML_SKIPPED
        elif tag in _HTML_CODE:
            self._code = max(self._code - 1, 0)
        elif tag in _HTML_BREAKS:
            self._flush()
            if tag in ("ol", "ul") and self._lists:
                self._lists.pop()
            self._items -= tag == "li" and self._items > 0

    def handle_comment(self, data: str) -> None:
        region = _HTML_REGION.match(data)
        if region and region["name"] in CHECKED_REGIONS:
            self._flush()  # a checked region starts and ends a segment, but is read
        elif region and region["edge"] == "start" and not self._region:
            self._flush()
            self._region = region["name"]
        elif region and region["name"] == self._region:
            self._region = None

    def handle_data(self, data: str) -> None:
        if not (self._region or self._skipped or self._code or self._avoid):
            self._add(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _add(self, data: str) -> None:
        if not "".join(self._text).strip() and data.strip():
            line, _ = self.getpos()
            self._line = line + data.count("\n", 0, len(data) - len(data.lstrip()))
            self._text.clear()
        self._text.append(data)

    def _flush(self) -> None:
        text = "".join(self._text).strip()
        if text:
            listed = self._items > 0
            step = listed and bool(self._lists) and self._lists[-1] == "ol"
            self.segments.append(_Segment(self._line, text, step, listed))
        self._text.clear()


def _html_segments(text: str) -> list[_Segment]:
    parser = _HtmlSegments()
    parser.feed(text)
    parser.close()
    return parser.segments


ROOT = Path(__file__).resolve().parent.parent
CONVERTED = Path("scripts") / "ste_converted.txt"
"""The documents already converted, one repository path in each line. Each is a gate: a
finding in one of them fails the check."""
_SCOPE_FILES = frozenset({
    "AGENTS.md", "GLOSSARY.md", "docs/implementation/architecture-boundaries.md",
})  # fmt: skip
_SCOPE_FOLDERS = frozenset({"docs/adr", "docs/specs"})
_PRUNED = frozenset({"build", "node_modules", "__pycache__"})
_GLOSSARY_TERM = re.compile(r"^\*\*(?P<term>[^*]+)\*\*:", re.MULTILINE)
_ADR_STATUS = re.compile(r"^Status:\s*(?P<status>.*)$", re.MULTILINE)


def in_scope(root: Path) -> list[str]:
    """The documents that the rollout (#234) converts, as sorted repository paths."""
    found: list[str] = []
    for folder, folders, files in os.walk(root):
        folders[:] = [f for f in folders if not f.startswith(".") and f not in _PRUNED]
        for name in files:
            path = (Path(folder) / name).relative_to(root).as_posix()
            if (
                name == "README.md"
                or path in _SCOPE_FILES
                or (path.startswith("site/") and name.endswith(".html"))
                or (path.rsplit("/", 1)[0] in _SCOPE_FOLDERS and name.endswith(".md"))
            ):
                found.append(path)
    return sorted(found)


def check_file(root: Path, path: str, glossary: frozenset[str]) -> list[Finding]:
    """The findings in one document, given by its repository path."""
    text = (root / path).read_text(encoding="utf-8")
    status = _ADR_STATUS.search(text)
    decided = status is not None and not status["status"].startswith("Proposed")
    accepted_adr = path.startswith("docs/adr/") and decided
    return check_text(
        text, html=path.endswith(".html"), glossary=glossary, skip_decision=accepted_adr
    )


def converted(root: Path) -> list[str]:
    """The paths in the converted list, without comments and blank lines."""
    lines = (root / CONVERTED).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def glossary_terms(root: Path) -> frozenset[str]:
    glossary = root / "GLOSSARY.md"
    if not glossary.is_file():
        return frozenset()
    return frozenset(
        m["term"].strip() for m in _GLOSSARY_TERM.finditer(glossary.read_text("utf-8"))
    )


def main(argv: Sequence[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.ste_check",
        description="Check documents against the Simplified Technical English rules.",
    )
    parser.add_argument("paths", nargs="*", type=Path, help="documents to check (default: all)")
    parser.add_argument("--gate", action="store_true", help="check only the converted documents")
    arguments = parser.parse_args(argv)
    gated = set(converted(root))
    missing = sorted(path for path in gated if not (root / path).is_file())
    if missing:
        sys.stderr.write(f"{CONVERTED.as_posix()} names files that do not exist: {missing}\n")
        return 2
    if arguments.gate:
        paths = sorted(gated)
    elif arguments.paths:
        paths = [p.resolve().relative_to(root.resolve()).as_posix() for p in arguments.paths]
    else:
        paths = in_scope(root)
    glossary = glossary_terms(root)
    total = gate_failures = 0
    for path in paths:
        for finding in check_file(root, path, glossary):
            sys.stdout.write(f"{path}:{finding.line}: {finding.rule}: {finding.message}\n")
            total += 1
            gate_failures += path in gated
    sys.stdout.write(
        f"\n{total} findings in {len(paths)} documents. "
        f"{gate_failures} in the {len(gated)} converted documents.\n"
    )
    return 1 if gate_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
