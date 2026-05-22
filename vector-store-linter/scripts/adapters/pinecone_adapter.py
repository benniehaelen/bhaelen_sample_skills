"""Pinecone adapter.

Implements the VectorStoreAdapter interface for a Pinecone index, targeting the
current stable client (v3 and later, imported lazily so the rest of the skill
runs without pinecone-client installed).

Sampling strategy. Pinecone has no native random-sampling endpoint. This
adapter samples by issuing similarity queries with randomly drawn query vectors
at a high top_k and collecting the unique results until the requested sample
size is reached. Each random query lands in a different region of the vector
space, so the union across queries is a broad, representative sample rather than
a contiguous slice. The strategy is approximate: it favors denser regions
slightly, since a random query returns more neighbors where vectors are dense.
For an exact full read, pass sample_size=None, which paginates every id through
list() and fetches in batches. Full read is only available on indexes that
support id listing (serverless and recent pod indexes).

embed_text returns None. Pinecone can embed server-side for some integrated
indexes, but not in general, so the adapter requires pre-embedded queries for
Tier 3 evaluation rather than claiming an embedding capability it may not have.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterator, Optional

import numpy as np

from .base import RetrievalResult, StoreConfig, VectorRecord, VectorStoreAdapter
from .errors import StoreConnectionError

logger = logging.getLogger("vector_store_linter")

# Pinecone caps top_k near 1000 when values are included in the response.
_MAX_SAMPLE_TOP_K = 1000
# Upper bound on random queries during sampling, so a smaller-than-requested
# store cannot drive an unbounded loop.
_MAX_SAMPLE_QUERIES = 200
# Consecutive queries that add no new ids before sampling concludes the store
# is exhausted.
_MAX_SAMPLE_STALLS = 5

# Metadata keys that commonly carry the embedded source text, in priority order.
_CONTENT_METADATA_KEYS = ("text", "content", "chunk_text", "page_content", "body")

_METRIC_MAP = {
    "cosine": "cosine",
    "euclidean": "l2",
    "l2": "l2",
    "dotproduct": "dot_product",
    "dot_product": "dot_product",
}


class PineconeAdapter(VectorStoreAdapter):
    """Adapter for a Pinecone index."""

    def __init__(
        self,
        index_name: str | None = None,
        host: str | None = None,
        api_key: str | None = None,
        namespace: str | None = None,
        client: Any | None = None,
    ) -> None:
        """Create an adapter for one Pinecone index.

        Args:
            index_name: Index name. One of index_name or host is required.
            host: Index host URL, used when connecting without a name.
            api_key: Pinecone API key. Falls back to PINECONE_API_KEY.
            namespace: Namespace to read. Defaults to the default namespace.
            client: An optional pre-built client, used in tests. When omitted,
                connect() builds one from the pinecone package.
        """
        self._index_name = index_name
        self._host = host
        self._api_key = api_key
        self._namespace = namespace or ""
        self._client = client
        self._index: Any | None = None
        self._index_description: Any | None = None
        self._meta_dimension: int | None = None
        self._meta_metric: str | None = None

    def connect(self) -> None:
        """Connect to the index and capture its description when available."""
        client = self._client or self._build_client()
        self._client = client
        try:
            if self._index_name:
                self._index_description = client.describe_index(self._index_name)
                self._meta_dimension = _attr(self._index_description, "dimension")
                self._meta_metric = _attr(self._index_description, "metric")
                self._index = client.Index(self._index_name)
            elif self._host:
                self._index = client.Index(host=self._host)
            else:
                raise StoreConnectionError(
                    "PineconeAdapter requires an index name or a host."
                )
        except StoreConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any client error uniformly
            raise StoreConnectionError(f"Could not connect to Pinecone: {exc}") from exc

    def describe(self) -> StoreConfig:
        """Return the index configuration as a StoreConfig."""
        self._require_connected()
        try:
            stats = self._index.describe_index_stats()
        except Exception as exc:  # noqa: BLE001
            raise StoreConnectionError(f"Could not read Pinecone index stats: {exc}") from exc

        dimension = self._meta_dimension or _attr(stats, "dimension")
        namespaces_map = _attr(stats, "namespaces") or {}
        namespaces = list(namespaces_map.keys()) if namespaces_map else None
        index_type, replicas, shards, index_parameters = self._spec_fields()

        return StoreConfig(
            store_type="pinecone",
            vector_count=_attr(stats, "total_vector_count"),
            dimension=dimension,
            distance_metric=_normalize_metric(self._meta_metric),
            index_type=index_type,
            index_parameters=index_parameters,
            replica_count=replicas,
            shard_count=shards,
            refresh_cadence=None,  # Pinecone does not report a refresh cadence
            metadata_schema=None,  # Pinecone is schemaless; no declared schema
            namespaces=namespaces,
            raw={"stats": _to_plain(stats), "description": _to_plain(self._index_description)},
        )

    def iter_vectors(
        self,
        sample_size: Optional[int] = None,
        seed: int = 42,
    ) -> Iterator[VectorRecord]:
        """Yield vectors, sampling at random unless sample_size is None."""
        self._require_connected()
        if sample_size is None:
            yield from self._iter_all()
        else:
            yield from self._iter_sample(sample_size, seed)

    def search(
        self,
        query_embedding: list[float],
        k: int,
        filter: Optional[dict] = None,
    ) -> list[RetrievalResult]:
        """Run a similarity search and return the top k results."""
        self._require_connected()
        try:
            response = self._index.query(
                vector=list(query_embedding),
                top_k=k,
                include_values=False,
                include_metadata=True,
                filter=filter,
                namespace=self._namespace,
            )
        except Exception as exc:  # noqa: BLE001
            raise StoreConnectionError(f"Pinecone query failed: {exc}") from exc

        results: list[RetrievalResult] = []
        for match in _attr(response, "matches", []) or []:
            metadata = dict(_attr(match, "metadata", {}) or {})
            results.append(
                RetrievalResult(
                    doc_id=_attr(match, "id"),
                    score=float(_attr(match, "score", 0.0) or 0.0),
                    metadata=metadata,
                    content=_extract_content(metadata),
                )
            )
        return results

    def embed_text(self, text: str) -> Optional[list[float]]:
        """Return None. Pinecone does not provide a general embedding path here."""
        return None

    def fetch(self, ids: list[str]) -> dict:
        """Fetch vectors by id via the Pinecone fetch endpoint."""
        self._require_connected()
        if not ids:
            return {}
        try:
            response = self._index.fetch(ids=[str(i) for i in ids], namespace=self._namespace)
        except Exception as exc:  # noqa: BLE001
            raise StoreConnectionError(f"Pinecone fetch failed: {exc}") from exc
        out: dict[str, VectorRecord] = {}
        for vector_id, vector in (_attr(response, "vectors", {}) or {}).items():
            metadata = dict(_attr(vector, "metadata", {}) or {})
            out[vector_id] = VectorRecord(
                id=vector_id,
                embedding=list(_attr(vector, "values", []) or []),
                metadata=metadata,
                content=_extract_content(metadata),
            )
        return out

    def close(self) -> None:
        """Release handles. The Pinecone client holds no long-lived connection."""
        self._index = None
        self._index_description = None

    # ----- internals -------------------------------------------------------

    def _build_client(self) -> Any:
        api_key = self._api_key or os.environ.get("PINECONE_API_KEY")
        if not api_key:
            raise StoreConnectionError(
                "Pinecone API key not found. Set PINECONE_API_KEY or pass api_key."
            )
        try:
            from pinecone import Pinecone
        except ImportError as exc:
            raise StoreConnectionError(
                "pinecone-client is not installed. Install it with: pip install pinecone-client"
            ) from exc
        return Pinecone(api_key=api_key)

    def _require_connected(self) -> None:
        if self._index is None:
            raise StoreConnectionError("Adapter is not connected. Call connect() first.")

    def _spec_fields(self) -> tuple[Optional[str], Optional[int], Optional[int], Optional[dict]]:
        """Pull index type, replica and shard counts, and parameters from the spec.

        Serverless indexes manage the index internally and do not expose the
        algorithm or replica and shard counts, so those come back as None with
        the cloud and region recorded in the parameters.
        """
        description = self._index_description
        if description is None:
            return None, None, None, None
        spec = _attr(description, "spec")
        if spec is None:
            return None, None, None, None

        pod = _attr(spec, "pod")
        if pod is not None:
            parameters = {
                "pod_type": _attr(pod, "pod_type"),
                "pods": _attr(pod, "pods"),
                "environment": _attr(pod, "environment"),
            }
            return _attr(pod, "pod_type"), _attr(pod, "replicas"), _attr(pod, "shards"), parameters

        serverless = _attr(spec, "serverless")
        if serverless is not None:
            parameters = {
                "cloud": _attr(serverless, "cloud"),
                "region": _attr(serverless, "region"),
            }
            return None, None, None, parameters

        return None, None, None, None

    def _iter_all(self) -> Iterator[VectorRecord]:
        list_fn = getattr(self._index, "list", None)
        if list_fn is None:
            raise StoreConnectionError(
                "This Pinecone index does not support listing ids, so a full read is "
                "unavailable. Use sampling instead."
            )
        for id_batch in list_fn(namespace=self._namespace):
            ids = [vid for vid in id_batch]
            if not ids:
                continue
            fetched = self._index.fetch(ids=ids, namespace=self._namespace)
            vectors = _attr(fetched, "vectors", {}) or {}
            for vector_id, vector in vectors.items():
                metadata = dict(_attr(vector, "metadata", {}) or {})
                yield VectorRecord(
                    id=vector_id,
                    embedding=list(_attr(vector, "values", []) or []),
                    metadata=metadata,
                    content=_extract_content(metadata),
                )

    def _iter_sample(self, sample_size: int, seed: int) -> Iterator[VectorRecord]:
        dimension = self._meta_dimension
        total: int | None = None
        try:
            stats = self._index.describe_index_stats()
            dimension = dimension or _attr(stats, "dimension")
            total = _attr(stats, "total_vector_count")
        except Exception as exc:  # noqa: BLE001
            raise StoreConnectionError(f"Could not read Pinecone index stats: {exc}") from exc

        if not dimension:
            raise StoreConnectionError("Cannot sample: the index dimension is unknown.")

        target = sample_size if not total else min(sample_size, total)
        per_query = min(_MAX_SAMPLE_TOP_K, max(target, 10))
        rng = np.random.default_rng(seed)

        seen: set[str] = set()
        stalls = 0
        queries = 0
        while len(seen) < target and stalls < _MAX_SAMPLE_STALLS and queries < _MAX_SAMPLE_QUERIES:
            query_vector = rng.standard_normal(dimension)
            norm = float(np.linalg.norm(query_vector)) or 1.0
            query_vector = query_vector / norm
            response = self._index.query(
                vector=query_vector.tolist(),
                top_k=per_query,
                include_values=True,
                include_metadata=True,
                namespace=self._namespace,
            )
            new_in_query = 0
            for match in _attr(response, "matches", []) or []:
                match_id = _attr(match, "id")
                if match_id in seen:
                    continue
                seen.add(match_id)
                new_in_query += 1
                metadata = dict(_attr(match, "metadata", {}) or {})
                yield VectorRecord(
                    id=match_id,
                    embedding=list(_attr(match, "values", []) or []),
                    metadata=metadata,
                    content=_extract_content(metadata),
                )
                if len(seen) >= target:
                    break
            queries += 1
            stalls = stalls + 1 if new_in_query == 0 else 0

        if len(seen) < target:
            logger.info(
                "Sampling returned %d of %d requested vectors (store appears to hold fewer).",
                len(seen),
                target,
            )


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field whether obj is an object with attributes or a mapping."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _normalize_metric(metric: Any) -> Optional[str]:
    """Map a Pinecone metric name to the StoreConfig vocabulary."""
    if not isinstance(metric, str):
        return None
    return _METRIC_MAP.get(metric.lower(), metric.lower())


def _extract_content(metadata: dict) -> Optional[str]:
    """Return the embedded source text from metadata when a known key carries it."""
    for key in _CONTENT_METADATA_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _to_plain(obj: Any) -> Any:
    """Best-effort conversion of a client response into plain JSON-safe data.

    Client responses are often objects rather than dicts. This keeps the raw
    field useful for adapter-specific checks without depending on the client's
    own serialization, which has changed across versions.
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(v) for v in obj]
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        try:
            return _to_plain(to_dict())
        except Exception:  # noqa: BLE001
            pass
    if hasattr(obj, "__dict__"):
        return {k: _to_plain(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)
