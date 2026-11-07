"""Checks on a stack's exported state, for the owner's wizards (#192).

The wizards run `pulumi stack export --file FILE`, then `python -m deployment.state_check`:

- `retention FILE --delete-data true|false`: whether `pulumi destroy` would keep exactly the
  Storage component's resources (the bucket and its guards) when the data is kept, and nothing
  when it is to be deleted. Pulumi's mocks cannot see `retain_on_delete`, so only a real
  state shows it.
- `remaining FILE`: every resource still in the stack, after a destroy.
- `policies FILE DIR`: each IAM policy document in the state, written to `DIR/<logical
  name>.json` for IAM Access Analyzer. The documents hold ARNs, so the wizard deletes them.
- `ids FILE TYPE`: the physical ID of each resource of one type, for the wizard to keep and
  check after a destroy. The wizard captures it and never prints it.

The first two print `ok`, or one problem per line and exit 1. A resource is named by its type and
logical name, never by a physical name or ARN, so the output can be recorded.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

STORAGE = "data-finops:index:Storage"
IAM_DOCUMENTS = ("aws:iam/rolePolicy:RolePolicy", "aws:iam/policy:Policy")
OWN_TYPES = "data-finops:"  # the components: they hold resources but are none themselves


def retention_problems(state: dict[str, Any], *, delete_data: bool) -> list[str]:
    """What `pulumi destroy` would keep or delete against the `deleteDataOnDestroy` choice."""
    resources = _resources(state)
    storage = {r["urn"] for r in resources if r.get("type") == STORAGE}
    problems = []
    if not any(r.get("type") == "aws:s3/bucket:Bucket" for r in resources):
        problems.append("the state has no bucket")
    for resource in resources:
        typ = str(resource.get("type", ""))
        if typ.startswith(("pulumi:", OWN_TYPES)):
            continue
        kept = bool(resource.get("retainOnDelete"))
        should_keep = resource.get("parent") in storage and not delete_data
        if kept != should_keep:
            what = "kept" if kept else "deleted"
            problems.append(f"{_name(resource)} would be {what} by destroy")
    return problems


def remaining(state: dict[str, Any]) -> list[str]:
    """Every resource in the state but the stack itself and its providers."""
    return [
        _name(r)
        for r in _resources(state)
        if r.get("type") != "pulumi:pulumi:Stack"
        and not str(r.get("type", "")).startswith("pulumi:providers:")
    ]


def write_policies(state: dict[str, Any], folder: Path) -> list[Path]:
    """Each IAM policy document in the state, as its own file, named by its logical name."""
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for resource in _resources(state):
        document = (resource.get("outputs") or {}).get("policy")
        if resource.get("type") in IAM_DOCUMENTS and document:
            path = folder / f"{_logical(resource)}.json"
            path.write_text(document, encoding="utf-8")
            written.append(path)
    return written


def ids(state: dict[str, Any], typ: str) -> list[str]:
    return [str(r["id"]) for r in _resources(state) if r.get("type") == typ and r.get("id")]


def _resources(state: dict[str, Any]) -> list[dict[str, Any]]:
    return list((state.get("deployment") or {}).get("resources") or [])


def _name(resource: dict[str, Any]) -> str:
    return f"{resource.get('type')} {_logical(resource)}"


def _logical(resource: dict[str, Any]) -> str:
    return str(resource.get("urn", "")).rsplit("::", 1)[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m deployment.state_check")
    checks = parser.add_subparsers(dest="check", required=True)
    retention = checks.add_parser("retention")
    retention.add_argument("state", type=Path)
    retention.add_argument("--delete-data", choices=("true", "false"), required=True)
    checks.add_parser("remaining").add_argument("state", type=Path)
    policies = checks.add_parser("policies")
    policies.add_argument("state", type=Path)
    policies.add_argument("folder", type=Path)
    of_type = checks.add_parser("ids")
    of_type.add_argument("state", type=Path)
    of_type.add_argument("type")
    args = parser.parse_args(argv)
    state = json.loads(args.state.read_text(encoding="utf-8"))
    if args.check == "policies":
        for path in write_policies(state, args.folder):
            print(path.name)
        return 0
    if args.check == "ids":
        for found in ids(state, args.type):
            print(found)
        return 0
    if args.check == "retention":
        problems = retention_problems(state, delete_data=args.delete_data == "true")
    else:
        problems = remaining(state)
    print("\n".join(problems) if problems else "ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
