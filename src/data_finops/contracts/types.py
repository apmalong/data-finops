"""Shared value types: exact decimals, ISO 4217 codes and UTC timestamps."""

import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    PlainSerializer,
    PlainValidator,
    StringConstraints,
    WithJsonSchema,
)

DECIMAL_PATTERN = r"^-?[0-9]+(\.[0-9]+)?$"
_DECIMAL_RE = re.compile(DECIMAL_PATTERN)


def _parse_decimal(value: object) -> Decimal:
    # Money never passes through binary floating point; adapters convert provider
    # doubles to decimal strings explicitly before validation.
    if isinstance(value, Decimal) and value.is_finite():
        return value
    if isinstance(value, str) and _DECIMAL_RE.fullmatch(value):
        return Decimal(value)
    raise ValueError("expected a decimal string such as '12.34'")


def format_decimal(value: Decimal) -> str:
    # Fixed-point keeps the value and scale; str() would emit exponents such as '1E-7'
    # below 1e-6, which DECIMAL_PATTERN rejects on the way back in.
    return format(value, "f")


DecimalString = Annotated[
    Decimal,
    PlainValidator(_parse_decimal),
    PlainSerializer(format_decimal, return_type=str),
    WithJsonSchema({"type": "string", "pattern": DECIMAL_PATTERN}),
]

CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


def _require_utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be UTC")
    return value


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]
