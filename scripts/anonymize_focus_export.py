"""Anonymize a downloaded AWS FOCUS export so it can serve as the M1 verification sample.

Usage (the secret keys the pseudonyms; keep it, never commit it):
    $env:DATA_FINOPS_ANON_SECRET = "<long random value>"
    uv run python scripts/anonymize_focus_export.py <downloaded-export-dir> <output-dir>

Allowlist policy (spec 04, data protection): only the non-identifying FOCUS columns in
PASS_THROUGH, numbers, timestamps and booleans are copied, and even those have every
12-digit number replaced. Every other text value, including unknown and `x_` columns and
free text, becomes a deterministic HMAC-SHA256 pseudonym, so joins and grouping still
work. ARNs keep partition, service, region and resource type. Output paths get the same
account-ID replacement as file contents, so the manifest still matches its data files.
Files other than Parquet and JSON are skipped and reported, never copied. The output
must be outside the repository or under its git-ignored `.local/`.
"""

import hashlib
import hmac
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from data_finops.billing.focus import utc_wall_clock

REPO = Path(__file__).resolve().parents[1]
SKIPPED = "<skipped files>"
ACCOUNT_COLUMNS = frozenset({"BillingAccountId", "SubAccountId"})
TAG_COLUMNS = frozenset({"Tags"})
PASS_THROUGH = frozenset(
    {
        "AvailabilityZone",
        "BillingAccountType",
        "BillingCurrency",
        "CapacityReservationStatus",
        "ChargeCategory",
        "ChargeClass",
        "ChargeFrequency",
        "CommitmentDiscountCategory",
        "CommitmentDiscountStatus",
        "CommitmentDiscountType",
        "CommitmentDiscountUnit",
        "ConsumedUnit",
        "InvoiceIssuerName",
        "PricingCategory",
        "PricingCurrency",
        "PricingUnit",
        "ProviderName",
        "PublisherName",
        "RegionId",
        "RegionName",
        "ResourceType",
        "ServiceCategory",
        "ServiceName",
        "ServiceSubcategory",
        "SkuId",
        "SkuMeter",
        "SkuPriceDetails",
        "SkuPriceId",
        "SubAccountType",
        "x_Discounts",
        "x_Operation",
        "x_ServiceCode",
    }
)
_ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
# Bounded on both sides, so an account ID running into hex text is never taken for a UUID.
_UUID = re.compile(
    r"(?<![0-9A-Za-z-])([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    r"(?![0-9A-Za-z-])"
)


class Pseudonyms:
    """Deterministic replacements keyed by the operator's secret."""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("a non-empty secret is required")
        self._key = secret.encode("utf-8")

    def _digest(self, kind: str, value: str) -> str:
        return hmac.new(self._key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()

    def account(self, value: str) -> str:
        return str(int(self._digest("account", value), 16) % 10**12).zfill(12)

    def scrub_accounts(self, text: str) -> str:
        """Replace account IDs, leaving UUIDs whole: their last group can be 12 digits."""
        return "".join(
            part if _UUID.fullmatch(part) else _ACCOUNT_ID.sub(lambda m: self.account(m[0]), part)
            for part in _UUID.split(text)
        )

    @staticmethod
    def account_ids(text: str) -> set[str]:
        """The account IDs `scrub_accounts` would replace in `text`."""
        return {
            found
            for part in _UUID.split(text)
            if not _UUID.fullmatch(part)
            for found in _ACCOUNT_ID.findall(part)
        }

    def text(self, value: str) -> str:
        """Any identifying text: ARNs keep their shape, account IDs stay 12 digits."""
        if value.startswith("arn:"):
            return self._arn(value)
        if _ACCOUNT_ID.fullmatch(value):
            return self.account(value)
        return "anon-" + self._digest("text", value)[:12]

    def _arn(self, value: str) -> str:
        parts = value.split(":", 5)
        if len(parts) != 6:
            return "anon-" + self._digest("text", value)[:12]
        _, partition, service, region, account, resource = parts
        kind = re.match(r"[^/:]*[/:]", resource)
        prefix = kind.group(0) if kind else ""
        fake = "anon-" + self._digest("text", resource)[:12]
        return ":".join(
            [
                "arn",
                partition,
                service,
                region,
                self.account(account) if account else "",
                prefix + fake,
            ]
        )

    def tags(self, pairs: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
        return [(self._tag_key(k), None if v is None else self.text(v)) for k, v in pairs]

    def _tag_key(self, key: str) -> str:
        prefix, sep, _ = key.partition(":")
        return prefix + sep + self.text(key) if sep else self.text(key)


def anonymize_export(source: Path, target: Path, secret: str) -> dict[str, int]:
    """Write an anonymized copy of `source` to `target`; return replacements per column."""
    target = writable_target(target)
    pseudonyms = Pseudonyms(secret)
    summary: Counter[str] = Counter()
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        relative = Path(pseudonyms.scrub_accounts(path.relative_to(source).as_posix()))
        if path.suffix == ".parquet":
            _write_parquet(path, _output(target, relative), pseudonyms, summary)
        elif path.suffix == ".json":
            document = json.loads(path.read_text(encoding="utf-8"))
            scrubbed = _apply(document, pseudonyms.scrub_accounts)
            _output(target, relative).write_text(json.dumps(scrubbed, indent=2), encoding="utf-8")
        else:
            summary[SKIPPED] += 1
    return dict(summary)


def writable_target(target: Path) -> Path:
    """The resolved output directory; never inside the repository except under `.local/`."""
    target = target.resolve()
    if target.is_relative_to(REPO) and not target.is_relative_to(REPO / ".local"):
        raise ValueError(f"refusing to write inside the repository outside .local/: {target}")
    return target


def _output(target: Path, relative: Path) -> Path:
    out = target / relative
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def _write_parquet(source: Path, out: Path, pseudonyms: Pseudonyms, summary: Counter[str]) -> None:
    metadata = pq.ParquetFile(source).metadata
    compression = (
        metadata.row_group(0).column(0).compression if metadata.num_row_groups else "snappy"
    )
    table = pq.read_table(source)
    columns = []
    for field in table.schema:
        # Zoned timestamps as their UTC wall clock: Python never resolves the zone's name,
        # which needs a time-zone database Windows lacks. Cast back on write.
        readable = utc_wall_clock(field.type)
        values = table.column(field.name).cast(readable).to_pylist()
        if field.name in TAG_COLUMNS:
            replaced = [None if v is None else pseudonyms.tags(v) for v in values]
        else:
            rule = _rule_for(field.name, pseudonyms)
            replaced = [_apply(value, rule) for value in values]
        summary[field.name] += sum(
            1 for old, new in zip(values, replaced, strict=True) if old != new
        )
        columns.append(pa.array(replaced, type=readable).cast(field.type))
    result = pa.Table.from_arrays(columns, schema=table.schema)
    pq.write_table(result, out, compression=compression.lower())


def _rule_for(column: str, pseudonyms: Pseudonyms) -> Callable[[str], str]:
    if column in ACCOUNT_COLUMNS:
        return pseudonyms.account
    if column in PASS_THROUGH:
        return pseudonyms.scrub_accounts
    return pseudonyms.text


def _apply(value: Any, rule: Callable[[str], str]) -> Any:
    """Apply `rule` to every string inside a value; maps arrive as (key, value) pairs."""
    if isinstance(value, str):
        return rule(value)
    if isinstance(value, list) and value and all(isinstance(i, tuple) for i in value):
        return [(rule(k), _apply(v, rule)) for k, v in value]
    if isinstance(value, list):
        return [_apply(item, rule) for item in value]
    if isinstance(value, dict):
        return {key: _apply(item, rule) for key, item in value.items()}
    return value  # numbers, timestamps, booleans and nulls are copied untouched


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    summary = anonymize_export(
        Path(argv[0]), Path(argv[1]), os.environ.get("DATA_FINOPS_ANON_SECRET", "")
    )
    for column, count in sorted(summary.items()):
        print(f"{column}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
