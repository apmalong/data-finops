"""One Pulumi release across the repository (#210).

The stack's SDK pin is the reference. The CLI that runs the stack, the policy pack and the
reference workload must be that release, so every pin and every check that names it must agree.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STACK = ROOT / "infra" / "pulumi" / "aws"


def _sdk_pin(project: Path) -> str:
    with (project / "pyproject.toml").open("rb") as file:
        dependencies = tomllib.load(file)["project"]["dependencies"]
    pins = [d.split("==", 1)[1] for d in dependencies if re.match(r"pulumi==", d)]
    assert len(pins) == 1, f"{project}: expected one exact `pulumi==` pin, found {pins}"
    return pins[0]


RELEASE = _sdk_pin(STACK)


def test_the_policy_pack_and_the_reference_workload_pin_the_stacks_pulumi_release() -> None:
    assert _sdk_pin(STACK / "policy") == RELEASE
    assert _sdk_pin(ROOT / "examples" / "reference-workload") == RELEASE


def test_ci_installs_the_stacks_pulumi_cli_release() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert re.findall(r"pulumi-version:\s*(\S+)", workflow) == [RELEASE]


def test_the_wizards_and_their_stub_name_the_stacks_pulumi_release() -> None:
    for script in ("wizards/deploy-and-verify.sh", "wizards/teardown.sh", "tests/wizard_stub.sh"):
        text = (STACK / script).read_text(encoding="utf-8")
        named = set(re.findall(r"(?<![\d.])3\.\d+\.\d+(?![\d.])", text))

        assert named == {RELEASE}, f"{script} names {sorted(named)}"
