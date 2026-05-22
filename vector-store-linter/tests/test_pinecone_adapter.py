"""Unit tests for the Pinecone adapter.

Exercises connect, describe (serverless and pod specs, metric normalization),
sampling and full-read iteration, search, embed_text, and the error paths,
using a fake client and index. A live integration test is included but skips
itself unless PINECONE_API_KEY and PINECONE_TEST_INDEX are set.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from adapters.base import RetrievalResult, StoreConfig, VectorRecord
from adapters.errors import StoreConnectionError
from adapters.pinecone_adapter import PineconeAdapter

VECTORS = {
    "v1": {"values": [0.1, 0.2, 0.3, 0.4], "metadata": {"text": "hello world", "source": "doc1"}},
    "v2": {"values": [0.5, 0.5, 0.5, 0.5], "metadata": {"content": "second chunk", "source": "doc2"}},
    "v3": {"values": [0.9, 0.0, 0.1, 0.0], "metadata": {"source": "doc3"}},
    "v4": {"values": [0.2, 0.2, 0.9, 0.1], "metadata": {"body": "fourth chunk"}},
}


class FakeIndex:
    """Minimal stand-in for a Pinecone index handle."""

    def __init__(self, vectors, dimension, namespaces=None, supports_list=True):
        self.vectors = vectors
        self.dimension = dimension
        self._namespaces = namespaces or {"": len(vectors)}
        self.query_calls: list[dict] = []
        if not supports_list:
            self.list = None  # shadows the method so getattr returns None

    def describe_index_stats(self):
        namespaces = {name: SimpleNamespace(vector_count=count) for name, count in self._namespaces.items()}
        return SimpleNamespace(
            total_vector_count=len(self.vectors),
            dimension=self.dimension,
            namespaces=namespaces,
            index_fullness=0.1,
        )

    def query(self, vector, top_k, include_values=False, include_metadata=False, filter=None, namespace=None):
        self.query_calls.append(
            {"top_k": top_k, "include_values": include_values, "filter": filter, "namespace": namespace}
        )
        matches = []
        for vid, vec in list(self.vectors.items())[:top_k]:
            matches.append(
                SimpleNamespace(
                    id=vid,
                    score=0.9,
                    values=list(vec["values"]) if include_values else None,
                    metadata=dict(vec["metadata"]) if include_metadata else None,
                )
            )
        return SimpleNamespace(matches=matches)

    def list(self, namespace=None):
        ids = list(self.vectors)
        for i in range(0, len(ids), 2):
            yield ids[i : i + 2]

    def fetch(self, ids, namespace=None):
        vectors = {
            vid: SimpleNamespace(values=list(self.vectors[vid]["values"]), metadata=dict(self.vectors[vid]["metadata"]))
            for vid in ids
            if vid in self.vectors
        }
        return SimpleNamespace(vectors=vectors)


class FakeClient:
    """Stand-in for the pinecone.Pinecone client."""

    def __init__(self, index, description):
        self.index = index
        self.description = description

    def describe_index(self, name):
        return self.description

    def Index(self, name=None, host=None):  # noqa: N802 - mirrors the real client name
        return self.index


def _serverless_description(metric="cosine", dimension=4):
    return SimpleNamespace(
        name="kb",
        dimension=dimension,
        metric=metric,
        host="https://kb.example.pinecone.io",
        spec=SimpleNamespace(serverless=SimpleNamespace(cloud="aws", region="us-east-1"), pod=None),
        status=SimpleNamespace(ready=True),
    )


def _pod_description(metric="dotproduct", dimension=4):
    return SimpleNamespace(
        name="kb",
        dimension=dimension,
        metric=metric,
        host="https://kb.example.pinecone.io",
        spec=SimpleNamespace(
            serverless=None,
            pod=SimpleNamespace(pod_type="p1.x1", replicas=2, shards=1, pods=2, environment="us-east1-gcp"),
        ),
        status=SimpleNamespace(ready=True),
    )


def _connected_adapter(description=None, supports_list=True, namespace=None):
    index = FakeIndex(VECTORS, dimension=4, supports_list=supports_list)
    client = FakeClient(index, description or _serverless_description())
    adapter = PineconeAdapter(index_name="kb", client=client, namespace=namespace)
    adapter.connect()
    return adapter, index


# ----- describe ------------------------------------------------------------


def test_describe_serverless():
    adapter, _ = _connected_adapter(_serverless_description())
    config = adapter.describe()
    assert isinstance(config, StoreConfig)
    assert config.store_type == "pinecone"
    assert config.dimension == 4
    assert config.vector_count == 4
    assert config.distance_metric == "cosine"
    assert config.index_type is None  # serverless does not expose the algorithm
    assert config.replica_count is None
    assert config.shard_count is None
    assert config.index_parameters == {"cloud": "aws", "region": "us-east-1"}
    assert config.namespaces == [""]


def test_describe_pod():
    adapter, _ = _connected_adapter(_pod_description())
    config = adapter.describe()
    assert config.distance_metric == "dot_product"
    assert config.index_type == "p1.x1"
    assert config.replica_count == 2
    assert config.shard_count == 1
    assert config.index_parameters["pod_type"] == "p1.x1"


def test_describe_metric_normalization_euclidean():
    adapter, _ = _connected_adapter(_serverless_description(metric="euclidean"))
    assert adapter.describe().distance_metric == "l2"


# ----- iter_vectors --------------------------------------------------------


def test_iter_vectors_sample_returns_records():
    adapter, index = _connected_adapter()
    records = list(adapter.iter_vectors(sample_size=2))
    assert len(records) == 2
    assert all(isinstance(r, VectorRecord) for r in records)
    assert len({r.id for r in records}) == 2  # unique
    assert all(len(r.embedding) == 4 for r in records)  # values included
    assert records[0].content == "hello world"  # extracted from the text key
    # Sampling must request values so embeddings are populated.
    assert index.query_calls and index.query_calls[0]["include_values"] is True


def test_iter_vectors_sample_caps_at_total():
    adapter, _ = _connected_adapter()
    records = list(adapter.iter_vectors(sample_size=100))
    assert len(records) == 4  # store holds only 4


def test_iter_vectors_full_uses_list_and_fetch():
    adapter, _ = _connected_adapter()
    records = list(adapter.iter_vectors(sample_size=None))
    assert sorted(r.id for r in records) == ["v1", "v2", "v3", "v4"]
    by_id = {r.id: r for r in records}
    assert by_id["v2"].content == "second chunk"
    assert by_id["v3"].content is None  # no recognized content key


def test_iter_vectors_full_without_list_support_raises():
    adapter, _ = _connected_adapter(supports_list=False)
    with pytest.raises(StoreConnectionError, match="full read is unavailable"):
        list(adapter.iter_vectors(sample_size=None))


# ----- search --------------------------------------------------------------


def test_search_returns_results():
    adapter, index = _connected_adapter()
    results = adapter.search([0.1, 0.1, 0.1, 0.1], k=2)
    assert len(results) == 2
    assert all(isinstance(r, RetrievalResult) for r in results)
    assert results[0].doc_id == "v1"
    assert results[0].score == 0.9
    assert results[0].content == "hello world"
    # search does not need embeddings back.
    assert index.query_calls[-1]["include_values"] is False


def test_search_passes_namespace_and_filter():
    adapter, index = _connected_adapter(namespace="tenant-a")
    adapter.search([0.1, 0.1, 0.1, 0.1], k=1, filter={"source": "doc1"})
    call = index.query_calls[-1]
    assert call["namespace"] == "tenant-a"
    assert call["filter"] == {"source": "doc1"}


# ----- embed_text ----------------------------------------------------------


def test_embed_text_returns_none():
    adapter, _ = _connected_adapter()
    assert adapter.embed_text("any query") is None


def test_fetch_by_id():
    adapter, _ = _connected_adapter()
    out = adapter.fetch(["v1", "v3"])
    assert set(out) == {"v1", "v3"}
    assert out["v1"].content == "hello world"
    assert len(out["v1"].embedding) == 4


def test_fetch_empty_ids():
    adapter, _ = _connected_adapter()
    assert adapter.fetch([]) == {}


# ----- error paths ---------------------------------------------------------


def test_describe_before_connect_raises():
    adapter = PineconeAdapter(index_name="kb")
    with pytest.raises(StoreConnectionError, match="not connected"):
        adapter.describe()


def test_search_before_connect_raises():
    adapter = PineconeAdapter(index_name="kb")
    with pytest.raises(StoreConnectionError, match="not connected"):
        adapter.search([0.0, 0.0, 0.0, 0.0], k=1)


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("PINECONE_API_KEY", raising=False)
    adapter = PineconeAdapter(index_name="kb")  # no injected client
    with pytest.raises(StoreConnectionError, match="API key not found"):
        adapter.connect()


def test_missing_pinecone_package_raises(monkeypatch):
    monkeypatch.setenv("PINECONE_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "pinecone", None)  # force the import to fail
    adapter = PineconeAdapter(index_name="kb")
    with pytest.raises(StoreConnectionError, match="pinecone-client is not installed"):
        adapter.connect()


def test_connect_requires_index_or_host():
    adapter = PineconeAdapter(client=FakeClient(FakeIndex(VECTORS, 4), _serverless_description()))
    with pytest.raises(StoreConnectionError, match="index name or a host"):
        adapter.connect()


def test_connect_by_host_without_description():
    index = FakeIndex(VECTORS, dimension=4)
    adapter = PineconeAdapter(host="https://kb.example.pinecone.io", client=FakeClient(index, None))
    adapter.connect()
    # No describe_index call was possible, but stats still populate the config.
    config = adapter.describe()
    assert config.dimension == 4
    assert config.distance_metric is None  # metric unknown without a description


# ----- live integration (skipped without credentials) ----------------------


@pytest.mark.integration
def test_pinecone_integration():
    api_key = os.environ.get("PINECONE_API_KEY")
    index_name = os.environ.get("PINECONE_TEST_INDEX")
    if not api_key or not index_name:
        pytest.skip("PINECONE_API_KEY and PINECONE_TEST_INDEX not set")
    adapter = PineconeAdapter(index_name=index_name, api_key=api_key)
    adapter.connect()
    try:
        config = adapter.describe()
        assert config.store_type == "pinecone"
        assert config.dimension and config.dimension > 0
        sample = list(adapter.iter_vectors(sample_size=10))
        assert all(len(r.embedding) == config.dimension for r in sample)
    finally:
        adapter.close()
