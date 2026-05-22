"""Vector store adapters.

Exposes the adapter interface and types from base, the shared connection error,
and a small registry mapping a store-type string to its adapter class. New
adapters register themselves here as they are implemented (BigQuery and pgvector
land in Phase 3).
"""

from __future__ import annotations

from .base import (
    RetrievalResult,
    StoreConfig,
    VectorRecord,
    VectorStoreAdapter,
)
from .bigquery_adapter import BigQueryAdapter
from .errors import StoreConnectionError
from .pgvector_adapter import PgVectorAdapter
from .pinecone_adapter import PineconeAdapter

ADAPTERS: dict[str, type[VectorStoreAdapter]] = {
    "pinecone": PineconeAdapter,
    "bigquery": BigQueryAdapter,
    "pgvector": PgVectorAdapter,
}


def get_adapter_class(store_type: str) -> type[VectorStoreAdapter]:
    """Return the adapter class registered for a store type.

    Raises:
        ValueError: if no adapter is registered for the store type yet.
    """
    try:
        return ADAPTERS[store_type]
    except KeyError:
        known = ", ".join(sorted(ADAPTERS)) or "none"
        raise ValueError(
            f"No adapter registered for store type {store_type!r}. Available: {known}."
        ) from None


__all__ = [
    "RetrievalResult",
    "StoreConfig",
    "VectorRecord",
    "VectorStoreAdapter",
    "StoreConnectionError",
    "PineconeAdapter",
    "BigQueryAdapter",
    "PgVectorAdapter",
    "ADAPTERS",
    "get_adapter_class",
]
