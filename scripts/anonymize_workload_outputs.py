"""Anonymize the reference workload's outputs (Athena history, dbt artifacts, OpenLineage events).

Usage (same secret as the FOCUS anonymizer, so pseudonyms match across billing and workload):
    $env:DATA_FINOPS_ANON_SECRET = "<long random value>"
    uv run python scripts/anonymize_workload_outputs.py <outputs-dir> <output-dir>

Files are recognised by content: a BatchGetQueryExecution response, a dbt manifest or run
results, or OpenLineage events (JSON array or JSON Lines). Anything else is skipped and
reported, never copied. Rules (spec 04, data protection; #32):
- every string: S3 bucket names found anywhere in the inputs, 12-digit account IDs and
  absolute local paths become the FOCUS anonymizer's keyed pseudonyms;
- Athena: only known execution fields are kept; query text keeps its dbt comment and the
  statement's leading keywords (`create table …`), never literals or object names;
- dbt: `user_id` is removed and environment values pseudonymized; run-results `args` keep
  flags, numbers and the selection and logging settings in ARGS_TEXT, nothing else;
- correlation survives: query execution IDs, workgroup, `node_id` and `invocation_id`.
After writing, the output is scanned for every original identifier and the run fails if
one remains. The output must be outside the repository or under its git-ignored `.local/`.
"""

import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from anonymize_focus_export import _UUID, REPO, SKIPPED, Pseudonyms  # noqa: E402

ATHENA_FIELDS = frozenset(
    {
        "EngineVersion",
        "Query",
        "QueryExecutionContext",
        "QueryExecutionId",
        "ResultConfiguration",
        "ResultReuseConfiguration",
        "StatementType",
        "Statistics",
        "Status",
        "SubstatementType",
        "WorkGroup",
    }
)
ARGS_TEXT = frozenset(
    {
        "exclude",
        "indirect_selection",
        "log_format",
        "log_format_file",
        "log_level",
        "log_level_file",
        "resource_types",
        "select",
        "selector",
        "which",
    }
)
_BUCKET = r"([a-z0-9][a-z0-9.-]{1,61}[a-z0-9])"
_BUCKET_MENTIONS = (re.compile(r"s3://" + _BUCKET), re.compile(r"arn:aws:s3:::" + _BUCKET))
_ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
_ABSOLUTE_PATH = re.compile(r"(?:/(?![/*])|[A-Za-z]:[\\/])")
_COMMENT = re.compile(r"-- /\* \{.*\} \*/\n")
_KEYWORD = re.compile(r"[A-Za-z_]+")
_LEADING_KEYWORDS = 2


class _Scrubber:
    """String rules shared by every source; remembers originals for the final scan."""

    def __init__(self, pseudonyms: Pseudonyms, buckets: set[str]) -> None:
        self.pseudonyms = pseudonyms
        self.originals: set[str] = set(buckets)
        self._buckets = [
            (re.compile(rf"(?<![a-z0-9.-]){re.escape(b)}(?![a-z0-9.-])"), pseudonyms.text(b))
            for b in sorted(buckets, key=len, reverse=True)
        ]

    def text(self, value: str) -> str:
        if _ABSOLUTE_PATH.match(value) and "://" not in value:
            self.originals.add(value)
            return self.pseudonyms.text(value)
        for part in _UUID.split(value):
            if not _UUID.fullmatch(part):
                self.originals.update(_ACCOUNT_ID.findall(part))
        for pattern, replacement in self._buckets:
            value = pattern.sub(replacement, value)
        return self.pseudonyms.scrub_accounts(value)

    def secret(self, value: str) -> str:
        """A value that must not survive in any form, such as an environment value."""
        self.originals.add(value)
        return self.pseudonyms.text(value)


def anonymize_workload_outputs(source: Path, target: Path, secret: str) -> dict[str, int]:
    """Write anonymized copies of the outputs under `source` to `target`; return a summary."""
    target = target.resolve()
    if target.is_relative_to(REPO) and not target.is_relative_to(REPO / ".local"):
        raise ValueError(f"refusing to write inside the repository outside .local/: {target}")
    pseudonyms = Pseudonyms(secret)
    files = {p: _read(p) for p in sorted(source.rglob("*")) if p.is_file()}
    buckets = {
        bucket
        for document in files.values()
        for text in _strings(document)
        for pattern in _BUCKET_MENTIONS
        for bucket in pattern.findall(text)
    }
    scrubber = _Scrubber(pseudonyms, buckets)
    summary: Counter[str] = Counter()
    written = []
    for path, document in files.items():
        kind = _kind(document)
        if kind is None:
            summary[SKIPPED] += 1
            continue
        anonymized = _ANONYMIZERS[kind](document, scrubber, summary)
        summary[f"{kind} files"] += 1
        out = target / pseudonyms.scrub_accounts(path.relative_to(source).as_posix())
        out.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".jsonl":
            out.write_text("".join(json.dumps(e) + "\n" for e in anonymized), encoding="utf-8")
        else:
            out.write_text(json.dumps(anonymized, indent=2), encoding="utf-8")
        written.append(out)
    _scan(written, scrubber.originals)
    return dict(summary)


def _read(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return None
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        return [document] if _is_event(document) else document  # one-line JSON Lines
    try:
        return [json.loads(line) for line in text.split("\n") if line.strip()]
    except json.JSONDecodeError:
        return None


def _kind(document: Any) -> str | None:
    if isinstance(document, dict) and isinstance(document.get("QueryExecutions"), list):
        return "athena"
    if isinstance(document, dict):
        version = str(document.get("metadata", {}).get("dbt_schema_version", ""))
        if "/manifest/" in version or "/run-results/" in version:
            return "dbt"
    if isinstance(document, list) and document and all(_is_event(e) for e in document):
        return "openlineage"
    return None


def _is_event(value: Any) -> bool:
    return isinstance(value, dict) and {"eventTime", "run", "job"} <= value.keys()


def _athena(document: dict[str, Any], scrubber: _Scrubber, summary: Counter[str]) -> Any:
    executions = []
    for execution in document["QueryExecutions"]:
        kept = {}
        for key, value in execution.items():
            if key in ATHENA_FIELDS:
                kept[key] = _query(value) if key == "Query" else value
            else:
                summary[f"dropped athena.{key}"] += 1
        executions.append(kept)
    return _walk({**document, "QueryExecutions": executions}, scrubber.text)


def _query(text: str) -> str:
    """Keep the dbt comment and the statement's leading keywords; drop the rest."""
    comment = _COMMENT.match(text)
    body = text[comment.end() :] if comment else text
    keywords = []
    for word in body.split():
        if not _KEYWORD.fullmatch(word) or len(keywords) == _LEADING_KEYWORDS:
            break
        keywords.append(word.lower())
    return (comment.group(0) if comment else "") + " ".join(keywords) + " …"


def _dbt(document: dict[str, Any], scrubber: _Scrubber, summary: Counter[str]) -> Any:
    metadata = dict(document["metadata"])
    if metadata.get("user_id"):
        scrubber.secret(str(metadata["user_id"]))
        metadata["user_id"] = None
    metadata["env"] = {k: scrubber.secret(str(v)) for k, v in metadata.get("env", {}).items()}
    result = {**document, "metadata": metadata}
    if "args" in document:
        args = {}
        for key, value in document["args"].items():
            if key in ARGS_TEXT or value is None or isinstance(value, bool | int | float):
                args[key] = value
            else:
                summary[f"dropped dbt.args.{key}"] += 1
        result["args"] = args
    return _walk(result, scrubber.text)


def _openlineage(events: list[Any], scrubber: _Scrubber, summary: Counter[str]) -> Any:
    return _walk(events, scrubber.text)


_ANONYMIZERS: dict[str, Callable[[Any, _Scrubber, Counter[str]], Any]] = {
    "athena": _athena,
    "dbt": _dbt,
    "openlineage": _openlineage,
}


def _walk(value: Any, rule: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return rule(value)
    if isinstance(value, list):
        return [_walk(item, rule) for item in value]
    if isinstance(value, dict):
        return {key: _walk(item, rule) for key, item in value.items()}
    return value  # numbers, booleans and nulls are copied untouched


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    return []


def _scan(written: list[Path], originals: set[str]) -> None:
    """Fail if any original identifier survives; name files and counts, never the values."""
    leaks = {
        str(path): count
        for path in written
        if (count := sum(1 for o in originals if o in path.read_text(encoding="utf-8")))
    }
    if leaks:
        raise RuntimeError(f"original identifiers remain in the output: {leaks}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    summary = anonymize_workload_outputs(
        Path(argv[0]), Path(argv[1]), os.environ.get("DATA_FINOPS_ANON_SECRET", "")
    )
    for key, count in sorted(summary.items()):
        print(f"{key}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
