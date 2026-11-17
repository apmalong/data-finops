"""The `--deployment NAME` target of the command line (spec 10 §Targets): an entry of
`~/.data-finops/deployments.toml`, and a signed GET of its API."""

import ipaddress
import json
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_finops import aws_clients

KEYS = {"api_url", "region", "profile", "issuer", "client_id", "audience"}
"""What an entry may hold: no token and no secret (spec 10 §Targets)."""

_TIMEOUT_SECONDS = 30


class DeploymentFailed(Exception):
    """The Deployment cannot be read; the message names the file and the key, never a value."""


def deployments_file() -> Path:
    return Path.home() / ".data-finops" / "deployments.toml"


@dataclass(frozen=True)
class Deployment:
    """One entry of `deployments.toml`: the table `[NAME]`."""

    name: str
    api_url: str
    region: str
    profile: str

    @classmethod
    def named(cls, name: str) -> "Deployment":
        path = deployments_file()
        try:
            entries = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise DeploymentFailed(
                f"no {path}: deploy writes it, or add the Deployment by hand"
            ) from None
        except tomllib.TOMLDecodeError:
            raise DeploymentFailed(f"{path} is not valid TOML") from None
        entry = entries.get(name)
        if not isinstance(entry, dict):
            raise DeploymentFailed(f"{path} has no Deployment {name}")
        unknown = sorted(set(entry) - KEYS)
        if unknown:
            raise DeploymentFailed(f"{path}: [{name}] has unknown keys: {', '.join(unknown)}")
        for key in ("api_url", "region"):
            if not isinstance(entry.get(key), str):
                raise DeploymentFailed(f"{path}: [{name}] needs {key}")
        if not isinstance(entry.get("profile"), str):  # OIDC sign-in comes with `login` (#297)
            raise DeploymentFailed(
                f"{path}: [{name}] has no profile. This version of data-finops cannot sign in"
                " with OIDC."
            )
        if not _secure(entry["api_url"]):
            raise DeploymentFailed(f"{path}: [{name}] api_url must use https")
        return cls(name, entry["api_url"], entry["region"], entry["profile"])

    def get(self, path: str, query: dict[str, str]) -> Any:
        """The JSON that the API answers to a GET of `path`, signed with SigV4."""
        encoded = urllib.parse.urlencode(query)
        url = self.api_url.rstrip("/") + path + (f"?{encoded}" if encoded else "")
        try:
            headers = aws_clients.signed_headers(url, region=self.region, profile=self.profile)
        except aws_clients.ClientsUnavailable as error:
            raise DeploymentFailed(str(error)) from None
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            # B310: `named` allows only https, or http to a loopback address.
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # nosec B310
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise DeploymentFailed(
                f"the Deployment answered {error.code}: {_problem_detail(error)}"
            ) from None
        except urllib.error.URLError as error:
            raise DeploymentFailed(
                f"cannot reach the Deployment {self.name}: {error.reason}"
            ) from None


def _secure(url: str) -> bool:
    """https, or http to a literal loopback address only (as `serve`, ADR 0024)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        return True
    try:
        return parts.scheme == "http" and ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        return False


def _problem_detail(error: urllib.error.HTTPError) -> str:
    """The Problem Details `detail` of an error answer (spec 06), or its reason phrase."""
    try:
        detail = json.loads(error.read()).get("detail")
    except (ValueError, AttributeError):
        detail = None
    return detail if isinstance(detail, str) else str(error.reason)
