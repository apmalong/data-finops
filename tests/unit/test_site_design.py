"""The site's design note keeps every spec 07 design and writing rule in view (spec 07)."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _spec_07_rules() -> list[str]:
    spec = (REPO / "docs" / "specs" / "07-documentation-site.md").read_text(encoding="utf-8")
    section = spec.split("## Design and writing", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^- (.+)$", section, flags=re.M)


def test_the_design_note_quotes_and_ticks_every_spec_07_design_rule() -> None:
    note = (REPO / "site" / "DESIGN.md").read_text(encoding="utf-8")
    ticked = set(re.findall(r"^- \[x\] (.+)$", note, flags=re.M))
    rules = _spec_07_rules()
    assert len(rules) == 6
    assert [rule for rule in rules if rule not in ticked] == []
    assert re.findall(r"^- \[ \] ", note, flags=re.M) == []
