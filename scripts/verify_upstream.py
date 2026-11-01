"""Check fixtures or real outputs against pinned upstream schemas (ADR 0014). Needs network.

Not part of CI, which runs offline. Run `uv run python scripts/verify_upstream.py`
and record the output in docs/implementation/progress.md. A checksum mismatch means
the upstream file changed: re-review it before updating the pin.

dbt artifacts may carry fields their published schema does not list (OQ-23): dbt adds
fields without bumping the schema version. Every node type still has to match its own
`resource_type`; only dbt's two test types share one. OpenLineage events are checked
strictly. Real outputs: `--dbt DIR` (repeatable; holds manifest.json and run_results.json)
and `--openlineage FILE` (JSON array or JSON Lines, as `dbt-ol` writes).
"""

import argparse
import copy
import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]

PINNED = {
    "dbt-manifest-v12": (
        "https://schemas.getdbt.com/dbt/manifest/v12.json",
        "f29ac66b0ea66b46575da0a5da66b2716f06f25295044234304e16631773ea4c",
    ),
    "dbt-run-results-v6": (
        "https://schemas.getdbt.com/dbt/run-results/v6.json",
        "1783bda55656bde624ed67640375640a2919a8608e2505e3a02c04cd139e2cdf",
    ),
    "openlineage-2-0-2": (
        "https://openlineage.io/spec/2-0-2/OpenLineage.json",
        "69f68bee00b9beac88a87059c0102410e7bb05f3f43c46d02a0409831eceb0d2",
    ),
    "openlineage-parent-run-1-1-0": (
        "https://openlineage.io/spec/facets/1-1-0/ParentRunFacet.json",
        "8c35b1347582d71b880f35effc005fda0910e34a6a2bbbcb5f0e2ac3d717096c",
    ),
}

# Known value additions in dbt 1.12 artifacts (OQ-23): exact macro, field and value.
# dbt's built-in function materialization lists "javascript", outside the v12 enum.
DBT_KNOWN_VALUES = (
    ("macro.dbt.materialization_function_default", "supported_languages", "javascript"),
)

FORMAT_CHECKER = Draft202012Validator.FORMAT_CHECKER


def fetch(name: str) -> dict[str, Any]:
    url, expected = PINNED[name]
    # Pinned https URLs only, checked by sha256 below.
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310  # nosec B310
        body = response.read()
    actual = hashlib.sha256(body).hexdigest()
    if actual != expected:
        raise SystemExit(f"{name}: checksum {actual} does not match pin {expected}")
    schema: dict[str, Any] = json.loads(body)
    return schema


def _allow_additional_properties(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _allow_additional_properties(value)
            for key, value in node.items()
            if not (key == "additionalProperties" and value is False)
        }
    if isinstance(node, list):
        return [_allow_additional_properties(item) for item in node]
    return node


def dbt_validator(schema: dict[str, Any]) -> Draft202012Validator:
    """A validator that accepts fields the dbt schema does not list; everything else is checked."""
    return strict_validator(_allow_additional_properties(schema))


def strict_validator(
    schema: dict[str, Any], registry: Registry | None = None
) -> Draft202012Validator:
    return Draft202012Validator(
        schema, registry=registry or Registry(), format_checker=FORMAT_CHECKER
    )


def failures(validator: Draft202012Validator, instance: Any) -> list[str]:
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors]


def _without_known_values(artifact: Any) -> Any:
    """A copy of the artifact with each known dbt value addition removed, so the rest is checked."""
    if not isinstance(artifact, dict) or not isinstance(artifact.get("macros"), dict):
        return artifact
    cleaned = copy.deepcopy(artifact)
    for macro_id, field, value in DBT_KNOWN_VALUES:
        values = cleaned["macros"].get(macro_id, {}).get(field)
        if isinstance(values, list):
            cleaned["macros"][macro_id][field] = [v for v in values if v != value]
    return cleaned


def dbt_failures(validator: Draft202012Validator, artifact: Any) -> list[str]:
    return failures(validator, _without_known_values(artifact))


def load_events(path: Path) -> list[Any]:
    """A JSON array of events, or JSON Lines (one event per line, as `dbt-ol` writes)."""
    text = path.read_text(encoding="utf-8-sig")
    if text.lstrip().startswith("["):
        events: list[Any] = json.loads(text)
        return events
    return [json.loads(line) for line in text.split("\n") if line.strip()]


def report(label: str, found: list[str]) -> int:
    for failure in found:
        print(f"FAIL {label}: {failure}")
    if not found:
        print(f"ok   {label}")
    return len(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dbt", type=Path, action="append", help="folder with dbt artifacts")
    parser.add_argument("--openlineage", type=Path, help="events file, JSON array or JSON Lines")
    args = parser.parse_args(argv)
    dbt_dirs: list[Path] = args.dbt or [ROOT / "fixtures/dbt"]
    events_file: Path = args.openlineage or ROOT / "fixtures/openlineage/events.json"

    schemas = {name: fetch(name) for name in PINNED}
    total_failures = 0

    dbt_validators = {
        "manifest.json": dbt_validator(schemas["dbt-manifest-v12"]),
        "run_results.json": dbt_validator(schemas["dbt-run-results-v6"]),
    }
    for folder in dbt_dirs:
        for file, validator in dbt_validators.items():
            artifact = json.loads((folder / file).read_text(encoding="utf-8"))
            total_failures += report(str(folder / file), dbt_failures(validator, artifact))

    core = schemas["openlineage-2-0-2"]
    parent = schemas["openlineage-parent-run-1-1-0"]
    registry: Registry = Registry().with_resources(
        [(s["$id"], Resource.from_contents(s)) for s in (core, parent)]
    )
    run_event = strict_validator({"$ref": f"{core['$id']}#/$defs/RunEvent"}, registry)
    parent_facet = strict_validator({"$ref": f"{parent['$id']}#/$defs/ParentRunFacet"}, registry)
    for i, event in enumerate(load_events(events_file)):
        total_failures += report(f"openlineage event {i} RunEvent", failures(run_event, event))
        if "parent" in event["run"]["facets"]:
            facet = event["run"]["facets"]["parent"]
            total_failures += report(
                f"openlineage event {i} ParentRunFacet", failures(parent_facet, facet)
            )

    print(f"{total_failures} failure(s)")
    return 1 if total_failures else 0


if __name__ == "__main__":
    sys.exit(main())
