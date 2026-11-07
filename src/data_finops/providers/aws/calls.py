"""Every AWS call a collector makes goes through `call` (spec 08)."""

from collections.abc import Mapping
from typing import Any


class AwsSourceError(RuntimeError):
    """An AWS call failed. The message is the AWS error code only (for example
    `AccessDenied`), never AWS's message text, which may quote ARNs or keys."""


def call(operation: Any, **params: Any) -> Mapping[str, Any]:
    """One AWS call; any failure becomes AwsSourceError carrying the AWS error code alone."""
    try:
        response: Mapping[str, Any] = operation(**params)
    except Exception as error:  # botocore's ClientError and connection errors
        raise AwsSourceError(_code(error)) from None
    return response


def _code(error: Exception) -> str:
    response = getattr(error, "response", None)
    if isinstance(response, Mapping):
        code = response.get("Error", {}).get("Code")
        if isinstance(code, str) and code:
            return code
    return type(error).__name__
