"""Validate fixtures against pinned upstream schemas (ADR 0014). Needs network access.

Not part of CI, which runs offline. Run `uv run python scripts/verify_upstream.py`
and record the output in docs/implementation/progress.md. A checksum mismatch means
the upstream file changed: re-review it before updating the pin.
"""

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


def fetch(name: str) -> dict[str, Any]:
    url, expected = PINNED[name]
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (pinned https URL)
        body = response.read()
    actual = hashlib.sha256(body).hexdigest()
    if actual != expected:
        raise SystemExit(f"{name}: checksum {actual} does not match pin {expected}")
    schema: dict[str, Any] = json.loads(body)
    return schema


def load(relative: str) -> Any:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def check(label: str, validator: Draft202012Validator, instance: Any) -> int:
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    for error in errors:
        print(f"FAIL {label}: {'/'.join(map(str, error.absolute_path))}: {error.message}")
    if not errors:
        print(f"ok   {label}")
    return len(errors)


def main() -> int:
    schemas = {name: fetch(name) for name in PINNED}
    fmt = Draft202012Validator.FORMAT_CHECKER
    failures = 0

    for name, fixture in [
        ("dbt-manifest-v12", "fixtures/dbt/manifest.json"),
        ("dbt-run-results-v6", "fixtures/dbt/run_results.json"),
    ]:
        failures += check(
            fixture, Draft202012Validator(schemas[name], format_checker=fmt), load(fixture)
        )

    core = schemas["openlineage-2-0-2"]
    parent = schemas["openlineage-parent-run-1-1-0"]
    registry: Registry = Registry().with_resources(
        [(s["$id"], Resource.from_contents(s)) for s in (core, parent)]
    )
    run_event = Draft202012Validator(
        {"$ref": f"{core['$id']}#/$defs/RunEvent"}, registry=registry, format_checker=fmt
    )
    parent_facet = Draft202012Validator(
        {"$ref": f"{parent['$id']}#/$defs/ParentRunFacet"}, registry=registry, format_checker=fmt
    )
    for i, event in enumerate(load("fixtures/openlineage/events.json")):
        failures += check(f"openlineage event {i} RunEvent", run_event, event)
        if "parent" in event["run"]["facets"]:
            failures += check(
                f"openlineage event {i} ParentRunFacet",
                parent_facet,
                event["run"]["facets"]["parent"],
            )

    print(f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
