"""The Collection Run (spec 08; GLOSSARY): a thin orchestrator over `providers.aws`, the
stores' public ingest functions and `calculation` (architecture-boundaries.md)."""

from data_finops.collection.run import (
    CollectionSettings,
    CollectionSummary,
    RejectedItem,
    SourceSummary,
    run_collection,
)

__all__ = [
    "CollectionSettings",
    "CollectionSummary",
    "RejectedItem",
    "SourceSummary",
    "run_collection",
]
