"""Unit tests for the Tier 2 content checks.

Each check is exercised with synthetic VectorRecord sets for its pass, fail, and
unknown (data absent) cases. Criteria come from the shipped rubric so thresholds
are real. run_tier2 is tested end to end.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from adapters.base import StoreConfig, VectorRecord
from checks import tier2_content as t2
from rubric import Rubric

RUBRIC = Rubric.load(Path(__file__).resolve().parent.parent / "scripts" / "rubric.yaml")


def crit(criterion_id: str):
    return RUBRIC.by_id(criterion_id)


def vr(vid="v", embedding=None, metadata=None, content=None) -> VectorRecord:
    return VectorRecord(id=vid, embedding=list(embedding or []), metadata=dict(metadata or {}), content=content)


def config(**overrides) -> StoreConfig:
    base = dict(
        store_type="pinecone", vector_count=None, dimension=None, distance_metric=None,
        index_type=None, index_parameters=None, replica_count=None, shard_count=None,
        refresh_cadence=None, metadata_schema=None, namespaces=None, raw={},
    )
    base.update(overrides)
    return StoreConfig(**base)


# ----- c_no_exact_duplicates -----------------------------------------------


def test_exact_duplicates_pass():
    vectors = [vr(f"v{i}", embedding=[float(i), 0.0, 0.0]) for i in range(50)]
    r = t2.check_no_exact_duplicates(crit("c_no_exact_duplicates"), config(), vectors)
    assert r.passed


def test_exact_duplicates_fail():
    vectors = [vr(f"v{i}", embedding=[1.0, 2.0, 3.0]) for i in range(50)]  # all identical
    r = t2.check_no_exact_duplicates(crit("c_no_exact_duplicates"), config(), vectors)
    assert not r.passed and r.evidence["duplicate_count"] == 49


def test_exact_duplicates_unknown_without_embeddings():
    vectors = [vr("v1"), vr("v2")]
    r = t2.check_no_exact_duplicates(crit("c_no_exact_duplicates"), config(), vectors)
    assert not r.passed and r.score == 0.5 and r.severity == "info"  # warn downgraded


# ----- c_no_near_duplicates ------------------------------------------------


def test_near_duplicates_pass():
    # 30 orthogonal one-hot directions: pairwise cosine is 0, no near-duplicates.
    vectors = [vr(f"v{i}", embedding=[1.0 if j == i else 0.0 for j in range(30)]) for i in range(30)]
    r = t2.check_no_near_duplicates(crit("c_no_near_duplicates"), config(), vectors)
    assert r.passed


def test_near_duplicates_fail():
    vectors = [vr(f"v{i}", embedding=[1.0, 0.0, 0.0, 0.0]) for i in range(20)]  # all parallel
    r = t2.check_no_near_duplicates(crit("c_no_near_duplicates"), config(), vectors)
    assert not r.passed
    assert r.evidence["near_duplicate_rate"] == 1.0


def test_near_duplicates_unknown():
    r = t2.check_no_near_duplicates(crit("c_no_near_duplicates"), config(), [vr("v1")])
    assert not r.passed and r.score == 0.5


# ----- c_chunk_size_distribution -------------------------------------------


def test_chunk_size_pass():
    # ~400 chars -> ~100 estimated tokens, inside the 50-2000 band.
    vectors = [vr(f"v{i}", content="word " * 80) for i in range(20)]
    r = t2.check_chunk_size_distribution(crit("c_chunk_size_distribution"), config(), vectors)
    assert r.passed


def test_chunk_size_fail_too_small():
    vectors = [vr(f"v{i}", content="tiny") for i in range(20)]  # ~1 token each
    r = t2.check_chunk_size_distribution(crit("c_chunk_size_distribution"), config(), vectors)
    assert not r.passed and r.evidence["outlier_rate"] == 1.0


def test_chunk_size_unknown_without_content():
    vectors = [vr(f"v{i}", embedding=[1.0]) for i in range(5)]
    r = t2.check_chunk_size_distribution(crit("c_chunk_size_distribution"), config(), vectors)
    assert not r.passed and r.score == 0.5


# ----- c_no_empty_content --------------------------------------------------


def test_empty_content_pass():
    vectors = [vr(f"v{i}", content="x" * 200) for i in range(10)]
    r = t2.check_no_empty_content(crit("c_no_empty_content"), config(), vectors)
    assert r.passed


def test_empty_content_fail():
    vectors = [vr("v1", content="x" * 200), vr("v2", content="   "), vr("v3", content="short")]
    r = t2.check_no_empty_content(crit("c_no_empty_content"), config(), vectors)
    assert not r.passed and r.evidence["empty_count"] == 2


def test_empty_content_unknown_without_content():
    vectors = [vr(f"v{i}", embedding=[1.0]) for i in range(5)]
    r = t2.check_no_empty_content(crit("c_no_empty_content"), config(), vectors)
    assert not r.passed and r.score == 0.5


# ----- c_metadata_completeness ---------------------------------------------


def test_metadata_completeness_pass():
    cfg = config(metadata_schema={"source": "text", "page": "int"})
    vectors = [vr(f"v{i}", metadata={"source": "d", "page": i}) for i in range(10)]
    r = t2.check_metadata_completeness(crit("c_metadata_completeness"), cfg, vectors)
    assert r.passed


def test_metadata_completeness_fail():
    cfg = config(metadata_schema={"source": "text", "page": "int"})
    # "source" present everywhere, "page" missing in most.
    vectors = [vr(f"v{i}", metadata={"source": "d"}) for i in range(10)]
    r = t2.check_metadata_completeness(crit("c_metadata_completeness"), cfg, vectors)
    assert not r.passed and "page" in r.evidence["coverage"]


def test_metadata_completeness_no_expected_fields_passes():
    vectors = [vr(f"v{i}", metadata={"x": 1}) for i in range(5)]
    r = t2.check_metadata_completeness(crit("c_metadata_completeness"), config(), vectors)
    assert r.passed and r.evidence["expected_fields"] == []


# ----- c_embedding_distribution --------------------------------------------


def _one_hot(index: int, dim: int = 10) -> list[float]:
    vec = [0.0] * dim
    vec[index] = 1.0
    return vec


def test_embedding_distribution_pass_spread():
    # 200 isotropic random points: k-means k=10 stays well balanced, no cluster
    # near the 30 percent concentration limit.
    rng = np.random.default_rng(0)
    points = rng.standard_normal((200, 16))
    vectors = [vr(f"v{i}", embedding=points[i].tolist()) for i in range(200)]
    r = t2.check_embedding_distribution(crit("c_embedding_distribution"), config(), vectors)
    assert r.passed


def test_embedding_distribution_fail_concentrated():
    # 60 identical plus 40 spread -> a single cluster holds 60 percent.
    vectors = [vr(f"a{i}", embedding=_one_hot(0)) for i in range(60)]
    vectors += [vr(f"b{i}", embedding=_one_hot(1 + (i % 4))) for i in range(40)]
    r = t2.check_embedding_distribution(crit("c_embedding_distribution"), config(), vectors)
    assert not r.passed
    assert r.evidence["max_cluster_concentration"] >= 0.30


def test_embedding_distribution_too_few_passes_with_note():
    vectors = [vr(f"v{i}", embedding=_one_hot(i % 3)) for i in range(5)]
    r = t2.check_embedding_distribution(crit("c_embedding_distribution"), config(), vectors)
    assert r.passed and r.score == 0.8


# ----- c_no_orphan_references ----------------------------------------------


def test_orphan_references_pass():
    vectors = [vr(f"v{i}", metadata={"source": f"doc_{i}"}) for i in range(10)]
    r = t2.check_no_orphan_references(crit("c_no_orphan_references"), config(), vectors)
    assert r.passed


def test_orphan_references_fail_missing():
    vectors = [vr("v1", metadata={"source": "doc_1"})]
    vectors += [vr(f"v{i}", metadata={"source": ""}) for i in range(2, 6)]
    r = t2.check_no_orphan_references(crit("c_no_orphan_references"), config(), vectors)
    assert not r.passed and r.evidence["missing_source_count"] == 4


def test_orphan_references_no_source_field_downgrades():
    vectors = [vr(f"v{i}", metadata={"unrelated": 1}) for i in range(5)]
    r = t2.check_no_orphan_references(crit("c_no_orphan_references"), config(), vectors)
    assert not r.passed and r.severity == "warn"  # fail downgraded one step


# ----- c_embedding_model_homogeneous ---------------------------------------


def test_model_homogeneous_pass():
    vectors = [vr(f"v{i}", metadata={"model": "text-embedding-3-small"}) for i in range(10)]
    r = t2.check_embedding_model_homogeneous(crit("c_embedding_model_homogeneous"), config(), vectors)
    assert r.passed


def test_model_homogeneous_fail_mixed():
    vectors = [vr("v1", metadata={"model": "model-a"}), vr("v2", metadata={"model": "model-b"})]
    r = t2.check_embedding_model_homogeneous(crit("c_embedding_model_homogeneous"), config(), vectors)
    assert not r.passed and r.severity == "critical" and r.evidence["model_count"] == 2


def test_model_homogeneous_unknown_without_field():
    vectors = [vr(f"v{i}", metadata={"other": 1}) for i in range(5)]
    r = t2.check_embedding_model_homogeneous(crit("c_embedding_model_homogeneous"), config(), vectors)
    assert not r.passed and r.score == 0.5  # critical downgraded to warn


# ----- registry and runner -------------------------------------------------


def test_registry_covers_all_tier2_criteria():
    tier2_ids = {c.id for c in RUBRIC.by_tier("tier_2_content")}
    assert set(t2.TIER2_CHECKS) == tier2_ids


def test_run_tier2_returns_result_per_criterion():
    cfg = config(metadata_schema={"source": "text", "model": "text"})
    rng = np.random.default_rng(1)
    points = rng.standard_normal((60, 16))  # distinct, well-spread embeddings
    vectors = [
        vr(f"v{i}", embedding=points[i].tolist(), metadata={"source": f"d{i}", "model": "m1"}, content="word " * 80)
        for i in range(60)
    ]
    results = t2.run_tier2(RUBRIC, cfg, vectors)
    assert len(results) == 8
    assert {r.criterion_id for r in results} == {c.id for c in RUBRIC.by_tier("tier_2_content")}
    assert all(r.passed for r in results), {r.criterion_id: r.message for r in results if not r.passed}
