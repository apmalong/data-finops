"""The owner's wizards (#192): they parse, and a dry run with every command stubbed reaches
neither AWS nor Pulumi and shows nothing it must mask. The owner runs them; no test or agent
runs them for real."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

WIZARDS = Path(__file__).parents[1] / "wizards"
DEPLOY = WIZARDS / "deploy-and-verify.sh"
TEARDOWN = WIZARDS / "teardown.sh"
STUBBED = ("aws", "pulumi", "uv", "awscurl", "xdg-open")
# What the stubs hand back, and what must never be shown or recorded.
ACCOUNT_TAIL = "54321"
ACCOUNT = "2109876" + ACCOUNT_TAIL  # never written whole: only 123456789012 may appear
SECRETS = {
    "account": ACCOUNT,
    "another account": ACCOUNT[6:] + ACCOUNT[:6],
    "bucket": "owner-secret-bucket-1a2b",
    "reference bucket": "owner-reference-bucket-5e6f",
    "deployer profile": "owner-deployer-profile",
    "reference profile": "owner-reference-profile",
    "export bucket": "owner-export-bucket-9c1d",
    "export name": "owner-focus-export",
    "email": "owner@example.org",
    "url": "abcdefghij.lambda-url",
    "auto-named bucket": "data-finops-1a2b3c4",
    "passphrase": "correct-horse-staple",
}
SEEDED = {
    "DEPLOYER_PROFILE": SECRETS["deployer profile"],
    "REFERENCE_PROFILE": SECRETS["reference profile"],
    "AWS_REGION_NAME": "us-east-1",
    "EXPORT_BUCKET": SECRETS["export bucket"],
    "EXPORT_NAME": SECRETS["export name"],
    "EXPORT_PREFIX": "focus",
    "WORKGROUP": "owner-workgroup",
    "BUDGET_USD": "5",
    "ALERT_EMAIL": SECRETS["email"],
}
STUB = (Path(__file__).parent / "wizard_stub.sh").read_text(encoding="utf-8")
"""A stand-in for every stubbed tool: it logs each call and answers from the fakes above."""


def _bash() -> str:
    found = shutil.which("bash")
    if found is None or "system32" in found.lower():  # WSL's bash cannot run these paths
        pytest.skip("needs bash (Git Bash on Windows)")
    return found


@pytest.mark.parametrize("wizard", [DEPLOY, TEARDOWN], ids=lambda p: p.name)
def test_the_wizard_parses(wizard: Path) -> None:
    subprocess.run([_bash(), "-n", wizard.as_posix()], check=True)


def _dry_run(
    wizard: Path, answers: list[str], tmp_path: Path, **env: str
) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    bash = _bash()
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name in STUBBED:
        (stubs / name).write_text(STUB, encoding="utf-8", newline="\n")
        (stubs / name).chmod(0o755)
    local = tmp_path / "local"
    (local / "pulumi").mkdir(parents=True)
    # The folder as bash's own `pwd` names it (/c/Users/... in Git Bash), as the wizards'
    # default under the repository is named: Pulumi must still find it.
    bash_local = subprocess.run(
        [bash, "-c", 'cd "$1" && pwd', "_", local.as_posix()],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    env_file = local / "deploy-wizard.env"
    env_file.write_text(
        "".join(f"{k}='{v}'\n" for k, v in SEEDED.items()), encoding="utf-8", newline="\n"
    )
    log = tmp_path / "calls.log"
    log.touch()
    environment = {
        "PATH": os.pathsep.join([str(stubs), str(Path(bash).parent)]),
        "STUB_LOG": log.as_posix(),
        "STUB_ACCOUNT_TAIL": ACCOUNT_TAIL,
        "WIZARD_LOCAL_DIR": bash_local,
        "WIZARD_ENV_FILE": env_file.as_posix(),
        "HOME": tmp_path.as_posix(),
        "TERM": "dumb",
        **env,
    }
    if "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    resolved = subprocess.run(
        [bash, "-c", f"command -v {' '.join(STUBBED)}"],
        env=environment,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert len(resolved) == len(STUBBED)
    assert all("stubs" in path for path in resolved), resolved  # no real tool is reachable
    ran = subprocess.run(
        [bash, wizard.as_posix()],
        input=("\n".join(answers) + "\n").encode(),  # bytes: text mode would send \r\n on Windows
        env=environment,
        capture_output=True,
        timeout=120,
    )
    result = subprocess.CompletedProcess(
        ran.args,
        ran.returncode,
        ran.stdout.decode("utf-8", "replace"),
        ran.stderr.decode("utf-8", "replace"),
    )
    records = "".join(p.read_text(encoding="utf-8") for p in local.glob("*-record.txt"))
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.startswith("PULUMI_BACKEND_URL="):
            state = Path(line.removeprefix("PULUMI_BACKEND_URL=file://"))
            assert state.is_dir(), f"Pulumi would not find its state folder: {line}"
    assert SECRETS["passphrase"] not in env_file.read_text(encoding="utf-8")  # never saved
    return result, log.read_text(encoding="utf-8").splitlines(), records


def _nothing_secret(*texts: str) -> None:
    for text in texts:
        for label, secret in SECRETS.items():
            assert secret not in text, f"the {label} is shown"


DEPLOY_ANSWERS = [
    "",  # banner
    "",  # tools
    "", "", "",  # profile, Region, pause
    "y", "",  # validate the draft
    "", "", "", "", "", "", SECRETS["passphrase"], "",  # settings
    "",  # package
    "y",  # preview
    "y", "",  # deploy
    "",  # what a destroy keeps
    "y", "",  # deployed policies
    "y", "",  # collection run
    "",  # read back
    "",  # record
]  # fmt: skip


def test_the_deploy_wizard_dry_run_reaches_no_real_tool_and_masks_its_output(
    tmp_path: Path,
) -> None:
    result, calls, record = _dry_run(DEPLOY, DEPLOY_ANSWERS, tmp_path)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    commands = [" ".join(c.split()[:3]) for c in calls]
    for expected in (
        "aws accessanalyzer validate-policy",
        "uv run --frozen",
        "pulumi preview --policy-pack",
        "pulumi up --yes",
        "pulumi stack export",
        "aws lambda invoke",
        "awscurl --service lambda",
    ):
        assert expected in commands, f"{expected} was never called"
    assert calls.index(next(c for c in calls if c.startswith("pulumi preview"))) < calls.index(
        next(c for c in calls if c.startswith("pulumi up"))
    )
    assert "pulumi did stack in <account>" in record  # masked by its label, not only the pattern
    assert "<bucket>" in record and "<deployer profile>" in record
    _nothing_secret(result.stdout, result.stderr, record)


@pytest.mark.parametrize("no_at", [16, 17], ids=["no to the preview", "no to pulumi up"])
def test_the_deploy_wizard_stops_before_anything_is_created_without_a_yes(
    no_at: int, tmp_path: Path
) -> None:
    answers = DEPLOY_ANSWERS[:no_at] + ["n"]
    result, calls, _ = _dry_run(DEPLOY, answers, tmp_path)
    assert result.returncode == 1
    assert not any(c.startswith("pulumi up") for c in calls)


TEARDOWN_KEEP = [
    "",  # banner
    "", "", SECRETS["passphrase"], "",  # profile, region, passphrase, pause
    "y",  # every count is 0
    "n", "",  # keep the data
    "y",  # destroy the Deployment
    "y", "",  # stack rm
    "", SECRETS["passphrase"], "",  # the reference workload's profile and passphrase
    "y",  # destroy the reference workload
    "y", "",  # stack rm
    "",  # record
]  # fmt: skip


def test_the_teardown_wizard_dry_run_destroys_both_stacks_and_checks_what_remains(
    tmp_path: Path,
) -> None:
    result, calls, record = _dry_run(TEARDOWN, TEARDOWN_KEEP, tmp_path, STUB_KEPT="1")
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    destroys = [c for c in calls if c.startswith("pulumi destroy")]
    assert len(destroys) == 2
    assert not any("deleteDataOnDestroy" in c for c in calls)
    for line in (
        "gone: collector function",
        "gone: producer policy",
        "kept, as chosen: bucket",
        "gone: reference bucket",
        "gone: Glue database",
        "gone: Athena workgroup",
        "gone: Budget",
    ):
        assert line in record
    _nothing_secret(result.stdout, result.stderr, record)


def test_the_teardown_wizard_records_the_choice_to_delete_the_data_before_destroying(
    tmp_path: Path,
) -> None:
    answers = TEARDOWN_KEEP[:6] + ["y", "y", ""] + TEARDOWN_KEEP[8:]
    result, calls, record = _dry_run(TEARDOWN, answers, tmp_path)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    setting = calls.index("pulumi config set deleteDataOnDestroy true")
    up = next(i for i, c in enumerate(calls) if c.startswith("pulumi up --yes"))
    destroy = next(i for i, c in enumerate(calls) if c.startswith("pulumi destroy"))
    assert setting < up < destroy
    assert "gone: bucket" in record
    _nothing_secret(result.stdout, result.stderr, record)


def test_the_teardown_wizard_changes_nothing_when_the_owner_declines_the_up(
    tmp_path: Path,
) -> None:
    answers = TEARDOWN_KEEP[:6] + ["y", "n"]  # delete the data, then no to pulumi up
    result, calls, _ = _dry_run(TEARDOWN, answers, tmp_path)
    assert result.returncode == 1
    assert not any(
        c.startswith(("pulumi config set deleteDataOnDestroy", "pulumi up")) for c in calls
    )


def test_the_teardown_wizard_never_calls_a_failed_check_gone(tmp_path: Path) -> None:
    # A read that fails for any reason but "not found" (here, access denied) is no proof.
    result, calls, record = _dry_run(
        TEARDOWN, TEARDOWN_KEEP, tmp_path, STUB_KEPT="1", STUB_DENIED="1"
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "could not check: Glue database" in record
    assert "gone: Glue database" not in record
    assert "NOT EVERYTHING IS GONE:" in record
    # The Budget's Pulumi ID is account:name; the check asks for the name in that account.
    assert any(
        c.startswith(f"aws budgets describe-budget --account-id {ACCOUNT} ")
        and "--budget-name data-finops-reference-budget " in c
        for c in calls
    )
    _nothing_secret(result.stdout, result.stderr, record)


def test_the_teardown_wizard_destroys_nothing_without_a_yes(tmp_path: Path) -> None:
    answers = TEARDOWN_KEEP[:6] + ["n", "", "n"]  # keep the data, then no to the destroy
    result, calls, _ = _dry_run(TEARDOWN, answers, tmp_path)
    assert result.returncode == 1
    assert not any(c.startswith("pulumi destroy") for c in calls)
