"""End-to-end tests for the evaluate subcommand.

Drives lint.main with a fake adapter (config, sample, and a search function) and
a JSON query-embeddings file. Covers the success path across all three tiers,
failing-query classification in the metadata, the synthetic-ground-truth
warning, the recall gate, and the no-embeddings and bad-file error paths.
"""

from __future__ import annotations

import json

import pytest

import lint
from adapters.base import RetrievalResult, StoreConfig, VectorRecord


def _config(**overrides) -> StoreConfig:
    base = dict(
        store_type="pinecone", vector_count=100, dimension=8, distance_metric="cosine",
        index_type="hnsw", index_parameters={"M": 32, "ef_search": 100}, replica_count=2,
        shard_count=1, refresh_cadence="daily", metadata_schema={"source": "str", "model": "str"},
        namespaces=["a"], raw={},
    )
    base.update(overrides)
    return StoreConfig(**base)


def _one_hot(i, dim=8):
    vec = [0.0] * dim
    vec[i % dim] = 1.0
    return vec


def _clean_sample(n=30):
    return [
        VectorRecord(id=f"s{i}", embedding=_one_hot(i), metadata={"source": f"src{i}", "model": "m1"}, content="word " * 80)
        for i in range(n)
    ]


class FakeAdapter:
    def __init__(self, config, sample, search_fn, docs=None):
        self._config = config
        self._sample = sample
        self._search_fn = search_fn
        self._docs = docs or {}  # id -> {"content", "metadata"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def describe(self):
        return self._config

    def iter_vectors(self, sample_size=None, seed=42):
        return iter(self._sample if sample_size is None else self._sample[:sample_size])

    def embed_text(self, text):
        return None

    def search(self, embedding, k, filter=None):
        return self._search_fn(embedding, k)

    def fetch(self, ids):
        from adapters.base import VectorRecord
        return {
            i: VectorRecord(id=i, embedding=[], metadata=self._docs[i].get("metadata", {}), content=self._docs[i].get("content"))
            for i in ids if i in self._docs
        }


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _rr(doc_id, score=0.9, content="some retrieved text"):
    return RetrievalResult(doc_id=doc_id, score=score, metadata={"source": doc_id}, content=content)


# ----- success path --------------------------------------------------------


def test_evaluate_scores_all_three_tiers(monkeypatch, tmp_path):
    gt = _write(tmp_path, "gt.csv", "query,relevant_doc_ids\n\"q one\",doc_1\n\"q two\",doc_2\n")
    emb = _write(tmp_path, "emb.json", json.dumps({"q one": [1.0, 0.0], "q two": [0.0, 1.0]}))

    def search_fn(embedding, k):
        return {(1.0, 0.0): [_rr("doc_1")], (0.0, 1.0): [_rr("doc_2")]}.get(tuple(embedding), [])

    monkeypatch.setattr(lint, "_build_adapter", lambda args: FakeAdapter(_config(), _clean_sample(), search_fn))
    out = tmp_path / "report.json"
    code = lint.main(
        ["evaluate", "--store", "pinecone", "--index", "kb",
         "--ground-truth", str(gt), "--query-embeddings", str(emb), "--output", str(out)]
    )
    assert code == lint.EXIT_SUCCESS
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "tier_1_config" in data["tier_scores"]
    assert "tier_2_content" in data["tier_scores"]
    assert "tier_3_retrieval" in data["tier_scores"]
    assert data["metadata"]["ground_truth"]["evaluated"] == 2
    assert data["metadata"]["k"] == [5, 10, 20]


def test_failing_query_is_classified(monkeypatch, tmp_path):
    gt = _write(tmp_path, "gt.csv", "query,relevant_doc_ids\n\"good\",doc_1\n\"bad\",doc_2\n")
    emb = _write(tmp_path, "emb.json", json.dumps({"good": [1.0, 0.0], "bad": [0.0, 1.0]}))

    def search_fn(embedding, k):
        if tuple(embedding) == (1.0, 0.0):
            return [_rr("doc_1")]
        return [_rr("wrong_doc")]  # "bad" query retrieves nothing relevant

    monkeypatch.setattr(lint, "_build_adapter", lambda args: FakeAdapter(_config(), _clean_sample(), search_fn))
    out = tmp_path / "report.json"
    lint.main(
        ["evaluate", "--store", "pinecone", "--index", "kb",
         "--ground-truth", str(gt), "--query-embeddings", str(emb), "--output", str(out)]
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    failing = data["metadata"]["failing_queries"]
    assert [f["query"] for f in failing] == ["bad"]
    assert failing[0]["expected_ids"] == ["doc_2"]


def test_expected_doc_content_enables_vocabulary_classification(monkeypatch, tmp_path):
    gt = _write(tmp_path, "gt.csv", "query,relevant_doc_ids\n\"penalty methodology specifics\",doc_9\n")
    emb = _write(tmp_path, "emb.json", json.dumps({"penalty methodology specifics": [0.0, 1.0]}))
    # doc_9 is fetched (not retrieved), and its content shares no terms with the query.
    docs = {"doc_9": {"content": "completely different words about apples and oranges", "metadata": {}}}
    adapter = FakeAdapter(_config(), _clean_sample(), lambda e, k: [_rr("wrong_doc")], docs=docs)
    monkeypatch.setattr(lint, "_build_adapter", lambda args: adapter)
    out = tmp_path / "report.json"
    lint.main(
        ["evaluate", "--store", "pinecone", "--index", "kb",
         "--ground-truth", str(gt), "--query-embeddings", str(emb), "--output", str(out)]
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    failing = data["metadata"]["failing_queries"]
    modes = {c["mode"] for c in failing[0]["classifications"]}
    assert "vocabulary_mismatch" in modes  # enabled by the fetched expected-doc content


def test_synthetic_ground_truth_warns(monkeypatch, tmp_path):
    gt = _write(tmp_path, "gt.csv", "# synthetic: true\nquery,relevant_doc_ids\n\"q one\",doc_1\n")
    emb = _write(tmp_path, "emb.json", json.dumps({"q one": [1.0, 0.0]}))
    monkeypatch.setattr(
        lint, "_build_adapter",
        lambda args: FakeAdapter(_config(), _clean_sample(), lambda e, k: [_rr("doc_1")]),
    )
    out = tmp_path / "report.json"
    lint.main(
        ["evaluate", "--store", "pinecone", "--index", "kb",
         "--ground-truth", str(gt), "--query-embeddings", str(emb), "--output", str(out)]
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["metadata"]["ground_truth"]["is_synthetic"] is True
    assert any("synthetic" in w for w in data["metadata"]["warnings"])


# ----- gates ---------------------------------------------------------------


def test_expect_min_recall_at_10_fail(monkeypatch, tmp_path):
    gt = _write(tmp_path, "gt.csv", "query,relevant_doc_ids\n\"q\",doc_1\n")
    emb = _write(tmp_path, "emb.json", json.dumps({"q": [1.0, 0.0]}))
    monkeypatch.setattr(
        lint, "_build_adapter",
        lambda args: FakeAdapter(_config(), _clean_sample(), lambda e, k: [_rr("wrong")]),  # recall 0
    )
    code = lint.main(
        ["evaluate", "--store", "pinecone", "--index", "kb",
         "--ground-truth", str(gt), "--query-embeddings", str(emb), "--expect-min-recall-at-10", "0.5"]
    )
    assert code == lint.EXIT_EXPECTATION_NOT_MET


# ----- error paths ---------------------------------------------------------


def test_no_embeddings_is_usage_error(monkeypatch, tmp_path):
    gt = _write(tmp_path, "gt.csv", "query,relevant_doc_ids\n\"q\",doc_1\n")
    monkeypatch.setattr(
        lint, "_build_adapter",
        lambda args: FakeAdapter(_config(), _clean_sample(), lambda e, k: []),
    )
    # No --query-embeddings, and embed_text returns None, so nothing can be evaluated.
    code = lint.main(["evaluate", "--store", "pinecone", "--index", "kb", "--ground-truth", str(gt)])
    assert code == lint.EXIT_USAGE_ERROR


def test_bad_embeddings_file_is_usage_error(monkeypatch, tmp_path):
    gt = _write(tmp_path, "gt.csv", "query,relevant_doc_ids\n\"q\",doc_1\n")
    bad = _write(tmp_path, "emb.json", "not valid json at all")
    monkeypatch.setattr(
        lint, "_build_adapter",
        lambda args: FakeAdapter(_config(), _clean_sample(), lambda e, k: []),
    )
    code = lint.main(
        ["evaluate", "--store", "pinecone", "--index", "kb",
         "--ground-truth", str(gt), "--query-embeddings", str(bad)]
    )
    assert code == lint.EXIT_USAGE_ERROR


def test_missing_ground_truth_is_usage_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        lint, "_build_adapter",
        lambda args: FakeAdapter(_config(), _clean_sample(), lambda e, k: []),
    )
    code = lint.main(
        ["evaluate", "--store", "pinecone", "--index", "kb", "--ground-truth", str(tmp_path / "nope.csv")]
    )
    assert code == lint.EXIT_USAGE_ERROR
