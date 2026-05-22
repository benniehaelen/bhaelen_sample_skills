"""Unit tests for synthetic ground-truth generation and its CLI wireup.

A fake Anthropic client returns scripted queries; no network or key is used. The
generated CSV is round-tripped through the loader to confirm it is valid and
marked synthetic. The CLI path is driven with a fake adapter and a patched
generator.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import lint
from adapters.base import StoreConfig, VectorRecord
from ground_truth import GroundTruth, generate_queries, to_csv
from ground_truth.generator import GenerationError


class FakeMessages:
    def __init__(self, text, error_on=None):
        self._text = text
        self._error_on = error_on  # doc content substring that triggers an error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        user = kwargs["messages"][0]["content"]
        if self._error_on and self._error_on in user:
            raise RuntimeError("model error")
        return SimpleNamespace(content=[SimpleNamespace(text=self._text)])


class FakeClient:
    def __init__(self, text, error_on=None):
        self.messages = FakeMessages(text, error_on)


# ----- generate_queries ----------------------------------------------------


def test_generate_queries_basic():
    client = FakeClient(text='["What is X?", "How does Y work?"]')
    docs = [("doc_1", "content one"), ("doc_2", "content two")]
    pairs = generate_queries(docs, queries_per_doc=2, client=client)
    assert len(pairs) == 4
    assert ("What is X?", "doc_1") in pairs
    assert ("How does Y work?", "doc_2") in pairs


def test_generate_queries_respects_count():
    client = FakeClient(text='["q1", "q2", "q3", "q4"]')  # model returned extra
    pairs = generate_queries([("doc_1", "content")], queries_per_doc=2, client=client)
    assert len(pairs) == 2


def test_generate_queries_skips_empty_content():
    client = FakeClient(text='["q1"]')
    pairs = generate_queries([("doc_1", ""), ("doc_2", "   "), ("doc_3", "real")], 1, client=client)
    assert pairs == [("q1", "doc_3")]


def test_generate_queries_skips_failed_document():
    client = FakeClient(text='["q1"]', error_on="bad")
    pairs = generate_queries([("doc_1", "good content"), ("doc_2", "bad content")], 1, client=client)
    assert pairs == [("q1", "doc_1")]  # doc_2 errored and was skipped


def test_generate_queries_requires_key_without_client(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(GenerationError, match="ANTHROPIC_API_KEY"):
        generate_queries([("doc_1", "content")], 1)


# ----- CSV output and round trip -------------------------------------------


def test_to_csv_round_trips_as_synthetic():
    pairs = [("What is X?", "doc_1"), ("How does Y work?", "doc_2")]
    metadata = {"store": "pinecone", "index_or_table": "kb", "queries_per_doc": 1, "documents_sampled": 2}
    text = to_csv(pairs, metadata)
    gt = GroundTruth.from_text(text)
    assert gt.is_synthetic is True
    assert gt.metadata["generated_by"] == "vector-store-linter"
    assert len(gt.queries) == 2
    assert gt.queries[0].relevant_doc_ids == {"doc_1"}
    assert gt.queries[0].query_type == "synthetic"


# ----- CLI wireup ----------------------------------------------------------


class FakeAdapter:
    def __init__(self, vectors, error=None):
        self._vectors = vectors
        self._error = error

    def __enter__(self):
        if self._error:
            raise self._error
        return self

    def __exit__(self, *exc):
        return False

    def iter_vectors(self, sample_size=None, seed=42):
        return iter(self._vectors[:sample_size] if sample_size is not None else self._vectors)


def _vectors_with_content(n=3):
    return [VectorRecord(id=f"d{i}", embedding=[], metadata={}, content=f"passage {i}") for i in range(n)]


def test_cli_generate_writes_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(lint, "_build_adapter", lambda args: FakeAdapter(_vectors_with_content()))
    monkeypatch.setattr(lint, "generate_queries", lambda docs, n: [("q?", d) for d, _ in docs])
    out = tmp_path / "queries.csv"
    code = lint.main(
        ["generate-ground-truth", "--store", "pinecone", "--index", "kb",
         "--output", str(out), "--queries-per-doc", "1", "--sample-size", "3"]
    )
    assert code == lint.EXIT_SUCCESS
    gt = GroundTruth.load(out)
    assert gt.is_synthetic is True
    assert len(gt.queries) == 3


def test_cli_generate_no_content_is_usage_error(monkeypatch, tmp_path):
    no_content = [VectorRecord(id=f"d{i}", embedding=[1.0], metadata={}, content=None) for i in range(3)]
    monkeypatch.setattr(lint, "_build_adapter", lambda args: FakeAdapter(no_content))
    code = lint.main(
        ["generate-ground-truth", "--store", "pinecone", "--index", "kb", "--output", str(tmp_path / "q.csv")]
    )
    assert code == lint.EXIT_USAGE_ERROR
