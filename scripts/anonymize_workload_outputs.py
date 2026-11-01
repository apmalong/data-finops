"""Anonymize the reference workload's outputs (Athena history, dbt artifacts, OpenLineage events).

Usage (same secret as the FOCUS anonymizer, so pseudonyms match across billing and workload):
    $env:DATA_FINOPS_ANON_SECRET = "<long random value>"
    uv run python scripts/anonymize_workload_outputs.py <outputs-dir> <output-dir>

Files are recognised by content: a BatchGetQueryExecution response, a dbt manifest or run
results, or OpenLineage events (JSON array or JSON Lines). Anything else is skipped and
reported, never copied. Rules (spec 04, data protection; #32, #36):
- fields: only allowlisted fields are kept (ATHENA_FIELDS here; dbt and OpenLineage specs in
  anonymize_workload_fields.py); every other field is dropped and counted by its path;
  free text becomes pseudonyms; dbt collections the product does not read are emptied;
- every string: S3 bucket names found anywhere in the inputs (`s3://`, S3 ARNs, virtual-host
  URLs), 12-digit account IDs and local paths (whole values, paths inside text, `~/` and
  `file://`) become the FOCUS anonymizer's keyed pseudonyms, also in output file names;
  timestamps with a UTC offset are converted to UTC, so they do not reveal a time zone;
- SQL keeps only a dbt comment and the statement's leading keywords (`create table …`);
- dbt: `user_id` is cleared, `project_id` and environment values pseudonymized; run-results
  `args` keep flags, numbers and the selection and logging settings in ARGS_TEXT;
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from anonymize_focus_export import SKIPPED, Pseudonyms, writable_target  # noqa: E402
from anonymize_workload_fields import (  # noqa: E402
    EMPTY,
    KEEP,
    MANIFEST,
    OPENLINEAGE_EVENT,
    RUN_RESULTS,
    SQL,
    TEXT,
    Each,
    Items,
)

# Known BatchGetQueryExecution fields; None keeps a field whole, a set keeps those sub-fields.
ATHENA_FIELDS: dict[str, frozenset[str] | None] = {
    "EngineVersion": frozenset({"EffectiveEngineVersion", "SelectedEngineVersion"}),
    "Query": None,
    "QueryExecutionContext": frozenset({"Catalog", "Database"}),
    "QueryExecutionId": None,
    "ResultConfiguration": frozenset(
        {"EncryptionConfiguration", "ExpectedBucketOwner", "OutputLocation"}
    ),
    "ResultReuseConfiguration": frozenset({"ResultReuseByAgeConfiguration"}),
    "StatementType": None,
    "Statistics": frozenset(
        {
            "DataManifestLocation",
            "DataScannedInBytes",
            "DpuCount",
            "EngineExecutionTimeInMillis",
            "QueryPlanningTimeInMillis",
            "QueryQueueTimeInMillis",
            "ResultReuseInformation",
            "ServicePreProcessingTimeInMillis",
            "ServiceProcessingTimeInMillis",
            "TotalExecutionTimeInMillis",
        }
    ),
    "Status": frozenset({"CompletionDateTime", "State", "SubmissionDateTime"}),
    "SubstatementType": None,
    "WorkGroup": None,
}
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
_BUCKET_MENTIONS = (
    re.compile(r"s3://" + _BUCKET),
    re.compile(r"arn:aws:s3:::" + _BUCKET),
    re.compile(_BUCKET + r"\.s3[.-](?:[a-z0-9-]+\.)?amazonaws\.com"),
)
_WHOLE_PATH = re.compile(r"(?:/(?![/*])|~/|[A-Za-z]:[\\/])")
_EMBEDDED_PATH = re.compile(
    r"file://[^\s'\"]+"
    r"|(?<![\w])[A-Za-z]:[\\/][^\s'\"<>|]*"
    r"|(?<![\w:/.~-])~?/(?:[\w.-]+/)+[\w.-]*"
)
_OFFSET_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{2}:\d{2}")
_COMMENT = re.compile(r"-- /\* \{.*\} \*/\n")
_KEYWORD = re.compile(r"[A-Za-z_]+")
_LEADING_KEYWORDS = 2


class _Scrubber:
    """String rules shared by every source; remembers originals for the final scan."""

    def __init__(self, pseudonyms: Pseudonyms, buckets: set[str]) -> None:
        self.pseudonyms = pseudonyms
        self.originals: set[str] = set(buckets)
        self._buckets = [
            # A name ends where bucket characters end, or at a virtual-host `.s3.` suffix.
            (
                re.compile(rf"(?<![a-z0-9.-]){re.escape(b)}(?![a-z0-9-]|\.(?!s3[.-]))"),
                pseudonyms.text(b),
            )
            for b in sorted(buckets, key=len, reverse=True)
        ]

    def text(self, value: str) -> str:
        if _OFFSET_TIME.fullmatch(value):
            return _utc(value)
        if _WHOLE_PATH.match(value) and "://" not in value:
            return self.redact(value)
        value = _EMBEDDED_PATH.sub(lambda m: self.redact(m[0]), value)
        self.originals.update(self.pseudonyms.account_ids(value))
        for pattern, replacement in self._buckets:
            value = pattern.sub(replacement, value)
        return self.pseudonyms.scrub_accounts(value)

    def redact(self, value: str) -> str:
        """Replace an identifier that must not survive in any form, such as a path."""
        self.originals.add(value)
        return self.pseudonyms.text(value)


def _utc(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(UTC).isoformat().replace("+00:00", "Z")


def anonymize_workload_outputs(source: Path, target: Path, secret: str) -> dict[str, int]:
    """Write anonymized copies of the outputs under `source` to `target`; return a summary."""
    target = writable_target(target)
    pseudonyms = Pseudonyms(secret)
    files = {p: _read(p) for p in sorted(source.rglob("*")) if p.is_file()}
    buckets = {
        bucket
        for path, document in files.items()
        for text in [path.relative_to(source).as_posix(), *_strings(document)]
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
        out = target.joinpath(*(scrubber.text(part) for part in path.relative_to(source).parts))
        out.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".jsonl":
            out.write_text("".join(json.dumps(e) + "\n" for e in anonymized), encoding="utf-8")
        else:
            out.write_text(json.dumps(anonymized, indent=2), encoding="utf-8")
        written.append(out)
    _scan(written, target, scrubber.originals)
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
        kept: dict[str, Any] = {}
        for key, value in execution.items():
            if key not in ATHENA_FIELDS:
                summary[f"dropped athena.{key}"] += 1
                continue
            allowed = ATHENA_FIELDS[key]
            if allowed is not None and isinstance(value, dict):
                for sub in sorted(value.keys() - allowed):
                    summary[f"dropped athena.{key}.{sub}"] += 1
                value = {k: v for k, v in value.items() if k in allowed}
            kept[key] = _sql_summary(value) if key == "Query" else value
        executions.append(kept)
    return _walk({**document, "QueryExecutions": executions}, scrubber.text)


def _sql_summary(text: str) -> str:
    """Keep a dbt comment and the statement's leading keywords; drop the rest."""
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
        scrubber.redact(str(metadata["user_id"]))
        metadata["user_id"] = None
    if metadata.get("project_id"):
        metadata["project_id"] = scrubber.redact(str(metadata["project_id"]))
    metadata["env"] = {k: scrubber.redact(str(v)) for k, v in metadata.get("env", {}).items()}
    result = {**document, "metadata": metadata}
    if "args" in document:
        args = {}
        for key, value in document["args"].items():
            if key in ARGS_TEXT or value is None or isinstance(value, bool | int | float):
                args[key] = value
            else:
                summary[f"dropped dbt.args.{key}"] += 1
        result["args"] = args
    is_manifest = "/manifest/" in str(metadata.get("dbt_schema_version"))
    spec, name = (MANIFEST, "manifest") if is_manifest else (RUN_RESULTS, "run_results")
    return _walk(_allow(result, spec, name, scrubber, summary), scrubber.text)


def _openlineage(events: list[Any], scrubber: _Scrubber, summary: Counter[str]) -> Any:
    allowed = _allow(events, Items(OPENLINEAGE_EVENT), "openlineage", scrubber, summary)
    return _walk(allowed, scrubber.text)


_ANONYMIZERS: dict[str, Callable[[Any, _Scrubber, Counter[str]], Any]] = {
    "athena": _athena,
    "dbt": _dbt,
    "openlineage": _openlineage,
}


def _allow(value: Any, spec: Any, path: str, scrubber: _Scrubber, summary: Counter[str]) -> Any:
    """Keep only the fields `spec` lists (see anonymize_workload_fields); count the rest."""
    if spec == KEEP or value is None:
        return value
    if spec == SQL:
        return _sql_summary(value) if isinstance(value, str) else value
    if spec == TEXT:
        return _free_text(value, scrubber, summary, path)
    if spec == EMPTY:
        if value:
            summary[f"emptied {path}"] += 1
        return {}
    if isinstance(spec, Each) and isinstance(value, dict):
        return {k: _allow(v, spec.spec, f"{path}.*", scrubber, summary) for k, v in value.items()}
    if isinstance(spec, Items) and isinstance(value, list):
        return [_allow(v, spec.spec, f"{path}[]", scrubber, summary) for v in value]
    if isinstance(spec, dict) and isinstance(value, dict):
        kept = {}
        for key, item in value.items():
            if key in spec:
                kept[key] = _allow(item, spec[key], f"{path}.{key}", scrubber, summary)
            else:
                summary[f"dropped {path}.{key}"] += 1
        return kept
    return value


def _free_text(value: Any, scrubber: _Scrubber, summary: Counter[str], path: str) -> Any:
    """Free text is not an identifier, so it is pseudonymized without joining the scan list."""
    if isinstance(value, str):
        return scrubber.pseudonyms.text(value) if value else value
    if isinstance(value, list):
        return [scrubber.pseudonyms.text(v) for v in value if isinstance(v, str)]
    if value:
        summary[f"emptied {path}"] += 1
    return {} if isinstance(value, dict) else value


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


def _scan(written: list[Path], target: Path, originals: set[str]) -> None:
    """Fail if any original identifier survives; name files and counts, never the values."""
    leaks = {}
    for path in written:
        text = path.relative_to(target).as_posix() + "\n" + path.read_text(encoding="utf-8")
        if count := sum(1 for original in originals if original in text):
            leaks[path.name] = count
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
