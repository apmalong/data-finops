"""The receiver's and API's one static bearer token (ADR 0024).

It is read from the environment or a file, never both, and is never logged, echoed or
stored: errors say what is wrong with it, not what it is.
"""

from collections.abc import Mapping
from pathlib import Path

# The environment variable's name, not a token (bandit B105 is a false positive here).
TOKEN_VARIABLE = "DATA_FINOPS_TOKEN"  # nosec B105
MIN_TOKEN_LENGTH = 16


def read_token(*, environ: Mapping[str, str], file: Path | None) -> str | None:
    """The configured token, or None when neither the variable nor a file gives one.

    Raises ValueError if both give one, or if it is shorter than MIN_TOKEN_LENGTH after
    surrounding whitespace is removed.
    """
    from_environment = environ.get(TOKEN_VARIABLE)
    if from_environment is not None and file is not None:
        raise ValueError(f"set the token in {TOKEN_VARIABLE} or a file, not both")
    raw = file.read_text(encoding="utf-8") if file is not None else from_environment
    return None if raw is None else checked_token(raw.strip())


def checked_token(token: str) -> str:
    """`token` itself, or ValueError if it is shorter than MIN_TOKEN_LENGTH or has
    surrounding whitespace: the one rule for every way a token is configured."""
    if len(token) < MIN_TOKEN_LENGTH or token != token.strip():
        raise ValueError(
            f"the token must be at least {MIN_TOKEN_LENGTH} characters, without surrounding spaces"
        )
    return token
