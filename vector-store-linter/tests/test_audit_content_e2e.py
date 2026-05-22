"""End-to-end tests for the audit-content subcommand.

Drives lint.main with a fake adapter that returns a config and a vector sample,
covering the success path, the Tier 2 CI gates (duplicate rate, orphans),
sampling vs full read, and the connection-error exit code.
"""

from __future__ import annotations

import json

import pytest

import lint
from adapters.base import StoreConfig, VectorRecord
from adapters.errors import StoreConnectionError


def _config(**overrides) -> StoreConfig:
    base = dict(
        store_type="pinecone", vector_count=30, dimension=30, distance_metric="cosine",
        index_type="hnsw", index_parameters={"M": 32, "ef_search": 100}, replica_count=2,
        shard_count=1, refresh_cadence="daily", metadata_schema={"source": "str", "model": "str"},
        namespaces=["a", "b"], raw={},
    )
    base.update(overrides)
    return StoreConfig(**base)


def _one_hot(i, dim=30):
    vec = [0.0] * dim
    vec[i % dim] = 1.0
    return vec


def _clean_vectors(n=30):
    # Distinct orthogonal embeddings, each with source, model, and content.
    return [
        VectorRecord(
            id=f"v{i}",
            embedding=_one_hot(i),
            metadata={"source": f"doc_{i}", "model": "text-embedding-3-small"},
            content="word " * 80,
        )
        for i in range(n)
    ]


class FakeAdapter:
    def __init__(self, config, vectors, error=None):
        self._config = config
        self._vectors = vectors
        self._error = error
        self.requested_sample = "unset"

    def __enter__(self):
        if self._error is not None:
            raise self._error
        return self

    def __exit__(self, *exc):
        return False

    def describe(self):
        return self._config

    def iter_vectors(self, sample_size=None, seed=42):
        self.requested_sample = sample_size
        rows = self._vectors if sample_size is None else self._vectors[:sample_size]
        return iter(rows)


def _patch(monkeypatch, adapter):
    monkeypatch.setattr(lint, "_build_adapter", lambda args: adapter)


# ----- success path --------------------------------------------------------


def test_audit_content_scores_both_tiers(monkeypatch, tmp_path):
    _patch(monkeypatch, FakeAdapter(_config(), _clean_vectors()))
    out = tmp_path / "report.json"
    code = lint.main(
        ["audit-content", "--store", "pinecone", "--index", "kb",
         "--sample-size", "1000", "--output", str(out)]
    )
    assert code == lint.EXIT_SUCCESS
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "tier_1_config" in data["tier_scores"]
    assert "tier_2_content" in data["tier_scores"]
    assert data["metadata"]["modes"] == ["audit-content"]
    assert data["metadata"]["sample_size"] == 1000


def test_full_read_requests_all(monkeypatch, capsys):
    adapter = FakeAdapter(_config(), _clean_vectors())
    _patch(monkeypatch, adapter)
    code = lint.main(["audit-content", "--store", "pinecone", "--index", "kb", "--full"])
    assert code == lint.EXIT_SUCCESS
    assert adapter.requested_sample is None  # full read


def test_default_sample_size_applied(monkeypatch, capsys):
    adapter = FakeAdapter(_config(), _clean_vectors())
    _patch(monkeypatch, adapter)
    lint.main(["audit-content", "--store", "pinecone", "--index", "kb"])
    assert adapter.requested_sample == lint.DEFAULT_SAMPLE_SIZE


# ----- Tier 2 CI gates -----------------------------------------------------


def test_expect_max_duplicate_rate_fail(monkeypatch):
    dupes = [
        VectorRecord(id=f"v{i}", embedding=[1.0, 0.0, 0.0], metadata={"source": "d"}, content="word " * 80)
        for i in range(20)
    ]  # all identical -> high duplicate rate
    _patch(monkeypatch, FakeAdapter(_config(dimension=3), dupes))
    code = lint.main(
        ["audit-content", "--store", "pinecone", "--index", "kb", "--expect-max-duplicate-rate", "0.01"]
    )
    assert code == lint.EXIT_EXPECTATION_NOT_MET


def test_expect_zero_orphans_fail(monkeypatch):
    vectors = [VectorRecord(id="v1", embedding=_one_hot(1), metadata={"source": "d1"}, content="x" * 200)]
    vectors += [
        VectorRecord(id=f"v{i}", embedding=_one_hot(i), metadata={"source": ""}, content="x" * 200)
        for i in range(2, 6)
    ]
    _patch(monkeypatch, FakeAdapter(_config(), vectors))
    code = lint.main(
        ["audit-content", "--store", "pinecone", "--index", "kb", "--expect-zero-orphans"]
    )
    assert code == lint.EXIT_EXPECTATION_NOT_MET


def test_expect_zero_orphans_pass(monkeypatch):
    _patch(monkeypatch, FakeAdapter(_config(), _clean_vectors()))
    code = lint.main(
        ["audit-content", "--store", "pinecone", "--index", "kb", "--expect-zero-orphans"]
    )
    assert code == lint.EXIT_SUCCESS


# ----- error path ----------------------------------------------------------


def test_connection_error(monkeypatch):
    _patch(monkeypatch, FakeAdapter(_config(), [], error=StoreConnectionError("boom")))
    code = lint.main(["audit-content", "--store", "pinecone", "--index", "kb"])
    assert code == lint.EXIT_STORE_CONNECTION_ERROR
