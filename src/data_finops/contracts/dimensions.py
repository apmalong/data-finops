"""The Cost Dimensions configuration (spec 09 §Cost Dimensions, ADR 0031).

An organization names the attributes it groups cost by (team, tenant, DAG run) and where each
one comes from. A dimension's value is a claim, never Evidence (ADR 0006), and a dimension is
a view: it never changes how a Billing Line is partitioned.

    [dimensions.team]
    sources = ["query_comment.team", "openlineage_tag.team", "workgroup_map"]
    workgroup_map = { "etl-finance" = "finance" }

    [dimensions.tenant]
    sources = ["query_comment.tenant_id"]
    sensitive = true
"""

import hashlib
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

MAX_DIMENSIONS = 8
"""Dimensions in one configuration (spec 09)."""
MAX_CLAIM_KEYS = 16
"""Query comment keys that ingest keeps for each Query Execution (spec 09, ADR 0012)."""
MAX_CLAIM_CHARS = 128
"""The longest claim value that ingest keeps. A longer value is dropped and counted."""

_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
"""A dimension name or a source key."""
_DIMENSION_KEYS = frozenset({"sources", "workgroup_map", "sensitive"})


class SourceKind(StrEnum):
    QUERY_COMMENT = "query_comment"
    """A key in the first-line JSON comment of the query."""
    OPENLINEAGE_TAG = "openlineage_tag"
    """A tag on the Workload Run linked to the query, then on its parents up to the root."""
    WORKGROUP_MAP = "workgroup_map"
    """The workgroup of the query (or of the line, for an amount with no query), mapped."""


@dataclass(frozen=True)
class Source:
    kind: SourceKind
    key: str | None = None
    """The comment key or tag key. None for `workgroup_map`."""

    def __str__(self) -> str:
        return self.kind.value if self.key is None else f"{self.kind.value}.{self.key}"


@dataclass(frozen=True)
class Dimension:
    name: str
    sources: tuple[Source, ...]
    """In order: the first source with a value wins."""
    workgroup_map: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    sensitive: bool = False
    """Left out of the minimal Cost Explanation and the Demo Snapshot (ADR 0031 point 8)."""


@dataclass(frozen=True)
class CostDimensions:
    dimensions: tuple[Dimension, ...]
    sha256: str | None = None
    """The SHA-256 of the configuration file. A snapshot records it (Q72). None without a
    file."""

    @property
    def comment_keys(self) -> frozenset[str]:
        """The query comment keys that ingest keeps as claims."""
        return frozenset(
            source.key
            for dimension in self.dimensions
            for source in dimension.sources
            if source.kind is SourceKind.QUERY_COMMENT and source.key is not None
        )

    @classmethod
    def from_toml(cls, path: Path) -> "CostDimensions":
        """The configuration file of `--dimensions FILE`, validated as `from_mapping`
        validates. ValueError names the fault, never a value."""
        data = path.read_bytes()
        try:
            values = tomllib.loads(data.decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            raise ValueError("the Cost Dimensions file is not valid TOML") from None
        return cls.from_mapping(values, sha256=hashlib.sha256(data).hexdigest())

    @classmethod
    def from_mapping(
        cls, values: Mapping[str, Any], *, sha256: str | None = None
    ) -> "CostDimensions":
        """The configuration from its table. An unknown key or source is refused, so a typo
        cannot silently do nothing."""
        if not isinstance(values, Mapping):
            raise ValueError("the Cost Dimensions are not a table")
        unknown = sorted(set(values) - {"dimensions"})
        if unknown:
            raise ValueError(f"unknown key {', '.join(unknown)}")
        tables = values.get("dimensions", {})
        if not isinstance(tables, Mapping):
            raise ValueError("dimensions must be a table of dimensions")
        if len(tables) > MAX_DIMENSIONS:
            raise ValueError(f"at most {MAX_DIMENSIONS} dimensions can be configured")
        config = cls(tuple(_dimension(name, table) for name, table in tables.items()), sha256)
        if len(config.comment_keys) > MAX_CLAIM_KEYS:
            raise ValueError(f"at most {MAX_CLAIM_KEYS} comment keys can be configured")
        return config


NO_DIMENSIONS = CostDimensions(())
"""No configuration: ingest keeps no claims, and Allocations carry no dimensions."""


def _dimension(name: str, table: Any) -> Dimension:
    if not _NAME.fullmatch(name):
        raise ValueError("a dimension name is not a plain name (letters, digits, _ . -)")
    if not isinstance(table, Mapping):
        raise ValueError(f"dimensions.{name} must be a table")
    unknown = sorted(set(table) - _DIMENSION_KEYS)
    if unknown:
        raise ValueError(f"dimensions.{name}: unknown key {', '.join(unknown)}")
    sources = table.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"dimensions.{name}: sources must be a non-empty list")
    parsed = tuple(_source(name, index, source) for index, source in enumerate(sources, 1))
    workgroup_map = table.get("workgroup_map")
    if any(source.kind is SourceKind.WORKGROUP_MAP for source in parsed) and workgroup_map is None:
        raise ValueError(
            f"dimensions.{name}: a source is workgroup_map, but workgroup_map is not given"
        )
    if workgroup_map is not None and not (
        isinstance(workgroup_map, Mapping)
        and all(isinstance(k, str) and isinstance(v, str) and v for k, v in workgroup_map.items())
    ):
        raise ValueError(f"dimensions.{name}: workgroup_map must map workgroup names to values")
    sensitive = table.get("sensitive", False)
    if not isinstance(sensitive, bool):
        raise ValueError(f"dimensions.{name}: sensitive must be true or false")
    return Dimension(name, parsed, MappingProxyType(dict(workgroup_map or {})), sensitive)


def _source(dimension: str, index: int, text: Any) -> Source:
    if isinstance(text, str):
        if text == SourceKind.WORKGROUP_MAP.value:
            return Source(SourceKind.WORKGROUP_MAP)
        kind, _, key = text.partition(".")
        if kind in (SourceKind.QUERY_COMMENT, SourceKind.OPENLINEAGE_TAG) and _NAME.fullmatch(key):
            return Source(SourceKind(kind), key)
    raise ValueError(f"dimensions.{dimension}: source {index} is not a known source")


def select_claims(
    comment: Mapping[str, Any] | None, keys: frozenset[str]
) -> tuple[dict[str, str], int]:
    """The claims to keep from a query comment, and how many configured keys were dropped.

    Only the configured `keys` are kept. A kept value is a string of 1 to MAX_CLAIM_CHARS
    characters. A configured key with any other value is dropped and counted (spec 09).
    """
    present = comment or {}
    claims: dict[str, str] = {}
    dropped = 0
    for key in sorted(keys & set(present)):
        value = present[key]
        if isinstance(value, str) and 0 < len(value) <= MAX_CLAIM_CHARS:
            claims[key] = value
        else:
            dropped += 1
    return claims, dropped
