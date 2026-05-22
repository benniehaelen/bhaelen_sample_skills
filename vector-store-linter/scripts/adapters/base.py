"""
Base adapter interface for vector stores.

Every store adapter implements this interface. The rest of the linter is
store-agnostic and works through this protocol.

Adding a new store: subclass VectorStoreAdapter and implement the abstract
methods. Register in scripts/adapters/__init__.py.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass
class StoreConfig:
    """Configuration of a vector store as reported by the adapter.

    Not every field is meaningful for every store; adapters set what they can
    and leave the rest as None. The Tier 1 checks tolerate None as "unknown"
    and downgrade severity accordingly.
    """
    store_type: str                            # "pinecone", "bigquery", "pgvector"
    vector_count: Optional[int]
    dimension: Optional[int]
    distance_metric: Optional[str]             # "cosine", "l2", "dot_product"
    index_type: Optional[str]                  # "flat", "hnsw", "ivf", "scann", etc.
    index_parameters: Optional[dict]           # store-specific, e.g. {"M": 16, "ef": 50}
    replica_count: Optional[int]
    shard_count: Optional[int]
    refresh_cadence: Optional[str]             # documented refresh interval if any
    metadata_schema: Optional[dict]            # field name -> type
    namespaces: Optional[list[str]]            # for multi-tenant stores
    raw: dict                                  # full raw config for adapter-specific checks


@dataclass
class VectorRecord:
    """A single vector and its metadata, as returned by the store.

    embedding is the raw float vector. metadata is a dict of arbitrary
    fields the store has attached to the vector. content, when present,
    is the source text that was embedded; some stores carry this, others
    do not.
    """
    id: str
    embedding: list[float]
    metadata: dict
    content: Optional[str]


@dataclass
class RetrievalResult:
    """One result row from a similarity query."""
    doc_id: str
    score: float
    metadata: dict
    content: Optional[str]


class VectorStoreAdapter(ABC):
    """Abstract interface every vector store adapter implements."""

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the store. Raise on failure."""

    @abstractmethod
    def describe(self) -> StoreConfig:
        """Return the store's current configuration."""

    @abstractmethod
    def iter_vectors(
        self,
        sample_size: Optional[int] = None,
        seed: int = 42,
    ) -> Iterator[VectorRecord]:
        """Yield vectors from the store.

        If sample_size is None, yields every vector (slow on large stores).
        If sample_size is set, yields a representative sample of that size.

        Sampling strategy is adapter-specific but must be statistically
        representative (e.g., random, not "first N").
        """

    @abstractmethod
    def search(
        self,
        query_embedding: list[float],
        k: int,
        filter: Optional[dict] = None,
    ) -> list[RetrievalResult]:
        """Run a similarity search and return the top k results."""

    @abstractmethod
    def embed_text(self, text: str) -> Optional[list[float]]:
        """Embed a query string using the same model the store was populated with.

        Returns None if the adapter cannot produce embeddings (e.g., the store
        does not record its embedding model, or the model is unavailable in this
        environment). Tier 3 evaluation requires this to work; if it returns
        None, the user must supply pre-embedded queries.
        """

    def fetch(self, ids: list[str]) -> dict:
        """Fetch specific vectors by id, returning a mapping of id to VectorRecord.

        Ids that are not found are omitted from the result. Tier 3 evaluation
        uses this to load the expected documents so the failure classifier can
        compare a query against their content.

        The default returns an empty mapping, for stores that cannot look up by
        id. Adapters override it where the store supports id lookup. This is the
        one method with a default rather than an abstract requirement, so the
        interface stays usable by a store that only supports search and scan.
        """
        return {}

    @abstractmethod
    def close(self) -> None:
        """Release connection resources."""

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
