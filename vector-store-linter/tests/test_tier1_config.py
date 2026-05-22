"""Unit tests for the Tier 1 configuration checks.

Each of the nine checks is tested for its pass case, its fail or partial case,
and its unknown case (the depended-on field is None, which must downgrade
severity rather than fire at full severity). Criteria come from the shipped
rubric so the thresholds are the real ones. run_tier1 is tested end to end.
"""

from __future__ import annotations

from pathlib import Path

from adapters.base import StoreConfig, VectorRecord
from checks import tier1_config as t1
from rubric import Rubric

RUBRIC = Rubric.load(Path(__file__).resolve().parent.parent / "scripts" / "rubric.yaml")


def crit(criterion_id: str):
    return RUBRIC.by_id(criterion_id)


def config(**overrides) -> StoreConfig:
    base = dict(
        store_type="pinecone",
        vector_count=None,
        dimension=None,
        distance_metric=None,
        index_type=None,
        index_parameters=None,
        replica_count=None,
        shard_count=None,
        refresh_cadence=None,
        metadata_schema=None,
        namespaces=None,
        raw={},
    )
    base.update(overrides)
    return StoreConfig(**base)


def _vec(values) -> VectorRecord:
    return VectorRecord(id="x", embedding=list(values), metadata={}, content=None)


# ----- c_dimension_consistency ---------------------------------------------


def test_dimension_pass():
    r = t1.check_dimension_consistency(crit("c_dimension_consistency"), config(dimension=768))
    assert r.passed and r.score == 1.0 and r.severity == "critical"


def test_dimension_fail_nonpositive():
    r = t1.check_dimension_consistency(crit("c_dimension_consistency"), config(dimension=0))
    assert not r.passed and r.severity == "critical"


def test_dimension_unknown_downgrades():
    r = t1.check_dimension_consistency(crit("c_dimension_consistency"), config(dimension=None))
    assert not r.passed and r.score == 0.5 and r.severity == "warn"  # critical downgraded


# ----- c_distance_metric_appropriate ---------------------------------------


def test_metric_cosine_pass_without_sample():
    r = t1.check_distance_metric_appropriate(crit("c_distance_metric_appropriate"), config(distance_metric="cosine"))
    assert r.passed and r.score == 0.8  # not verified, no sample


def test_metric_cosine_pass_with_normalized_sample():
    vectors = [_vec([1.0, 0.0, 0.0, 0.0]), _vec([0.0, 1.0, 0.0, 0.0])]
    r = t1.check_distance_metric_appropriate(
        crit("c_distance_metric_appropriate"), config(distance_metric="cosine"), vectors
    )
    assert r.passed and r.score == 1.0


def test_metric_dot_product_unnormalized_flagged():
    vectors = [_vec([3.0, 4.0, 0.0, 0.0]), _vec([6.0, 8.0, 0.0, 0.0])]  # norms 5 and 10
    r = t1.check_distance_metric_appropriate(
        crit("c_distance_metric_appropriate"), config(distance_metric="dot_product"), vectors
    )
    assert not r.passed and r.score == 0.4


def test_metric_unknown_downgrades():
    r = t1.check_distance_metric_appropriate(crit("c_distance_metric_appropriate"), config(distance_metric=None))
    assert not r.passed and r.severity == "info"  # warn downgraded


# ----- c_index_type_for_scale ----------------------------------------------


def test_index_flat_at_scale_fails():
    r = t1.check_index_type_for_scale(crit("c_index_type_for_scale"), config(index_type="flat", vector_count=200000))
    assert not r.passed and r.score == 0.0


def test_index_hnsw_too_small_partial():
    r = t1.check_index_type_for_scale(crit("c_index_type_for_scale"), config(index_type="hnsw", vector_count=500))
    assert not r.passed and r.score == 0.5


def test_index_hnsw_at_scale_pass():
    r = t1.check_index_type_for_scale(crit("c_index_type_for_scale"), config(index_type="hnsw", vector_count=50000))
    assert r.passed


def test_index_managed_other_passes_with_note():
    r = t1.check_index_type_for_scale(crit("c_index_type_for_scale"), config(index_type="p1.x1", vector_count=50000))
    assert r.passed and r.score == 0.8


def test_index_unknown_downgrades():
    r = t1.check_index_type_for_scale(crit("c_index_type_for_scale"), config(index_type=None, vector_count=None))
    assert not r.passed and r.score == 0.5 and r.severity == "info"  # warn downgraded


# ----- c_hnsw_parameters_tuned ---------------------------------------------


def test_hnsw_params_pass():
    r = t1.check_hnsw_parameters_tuned(
        crit("c_hnsw_parameters_tuned"),
        config(index_type="hnsw", index_parameters={"M": 32, "ef_search": 100}),
    )
    assert r.passed


def test_hnsw_params_below_floor_partial():
    r = t1.check_hnsw_parameters_tuned(
        crit("c_hnsw_parameters_tuned"),
        config(index_type="hnsw", index_parameters={"M": 8, "ef_search": 20}),
    )
    assert not r.passed and r.score == 0.5


def test_hnsw_params_unknown_when_missing():
    r = t1.check_hnsw_parameters_tuned(
        crit("c_hnsw_parameters_tuned"), config(index_type="hnsw", index_parameters={})
    )
    assert not r.passed and r.score == 0.5 and r.severity == "info"


def test_hnsw_params_not_applicable_for_flat():
    r = t1.check_hnsw_parameters_tuned(crit("c_hnsw_parameters_tuned"), config(index_type="flat"))
    assert r.passed and r.score == 1.0


# ----- c_freshness_configured ----------------------------------------------


def test_freshness_pass():
    r = t1.check_freshness_configured(crit("c_freshness_configured"), config(refresh_cadence="daily"))
    assert r.passed


def test_freshness_unknown_downgrades():
    r = t1.check_freshness_configured(crit("c_freshness_configured"), config(refresh_cadence=None))
    assert not r.passed and r.severity == "info"  # warn downgraded


# ----- c_replication_for_scale ---------------------------------------------


def test_replication_single_at_scale_flagged():
    r = t1.check_replication_for_scale(
        crit("c_replication_for_scale"), config(vector_count=200000, replica_count=1, shard_count=1)
    )
    assert not r.passed and r.score == 0.3


def test_replication_managed_unknown_counts_pass():
    r = t1.check_replication_for_scale(
        crit("c_replication_for_scale"), config(vector_count=200000, replica_count=None, shard_count=None)
    )
    assert r.passed and r.score == 0.9


def test_replication_adequate_pass():
    r = t1.check_replication_for_scale(
        crit("c_replication_for_scale"), config(vector_count=200000, replica_count=3, shard_count=2)
    )
    assert r.passed and r.score == 1.0


def test_replication_unknown_count_downgrades():
    r = t1.check_replication_for_scale(crit("c_replication_for_scale"), config(vector_count=None))
    assert not r.passed and r.score == 0.5 and r.severity == "info"  # info stays info


# ----- c_metadata_schema_declared ------------------------------------------


def test_metadata_schema_pass():
    r = t1.check_metadata_schema_declared(
        crit("c_metadata_schema_declared"), config(metadata_schema={"source": "str", "page": "int"})
    )
    assert r.passed


def test_metadata_schema_unknown_downgrades():
    r = t1.check_metadata_schema_declared(crit("c_metadata_schema_declared"), config(metadata_schema=None))
    assert not r.passed and r.severity == "info"


# ----- c_embedding_model_recorded ------------------------------------------


def test_embedding_model_declared_pass():
    r = t1.check_embedding_model_recorded(
        crit("c_embedding_model_recorded"), config(metadata_schema={"model": "str", "source": "str"})
    )
    assert r.passed and r.evidence["model_fields"] == ["model"]


def test_embedding_model_missing_field_fails():
    r = t1.check_embedding_model_recorded(
        crit("c_embedding_model_recorded"), config(metadata_schema={"source": "str"})
    )
    assert not r.passed and r.score == 0.3


def test_embedding_model_no_schema_unknown():
    r = t1.check_embedding_model_recorded(crit("c_embedding_model_recorded"), config(metadata_schema=None))
    assert not r.passed and r.score == 0.5 and r.severity == "info"


def test_embedding_model_coverage_pass_with_vectors():
    vectors = [
        VectorRecord(id=f"v{i}", embedding=[1.0], metadata={"model": "text-embedding-3-small"}, content=None)
        for i in range(20)
    ]
    r = t1.check_embedding_model_recorded(crit("c_embedding_model_recorded"), config(), vectors)
    assert r.passed and r.evidence["model_field_coverage"] == 1.0


def test_embedding_model_coverage_fail_with_vectors():
    # Half the sampled vectors carry no model field; coverage 0.5 is below 0.95.
    vectors = [
        VectorRecord(id=f"v{i}", embedding=[1.0], metadata=({"model": "m1"} if i < 10 else {"other": 1}), content=None)
        for i in range(20)
    ]
    r = t1.check_embedding_model_recorded(crit("c_embedding_model_recorded"), config(), vectors)
    assert not r.passed and r.evidence["model_field_coverage"] == 0.5


# ----- c_namespace_or_tenant_isolation -------------------------------------


def test_namespace_multiple_pass():
    r = t1.check_namespace_or_tenant_isolation(
        crit("c_namespace_or_tenant_isolation"), config(namespaces=["tenant-a", "tenant-b"])
    )
    assert r.passed and r.score == 1.0


def test_namespace_single_pass_with_note():
    r = t1.check_namespace_or_tenant_isolation(
        crit("c_namespace_or_tenant_isolation"), config(namespaces=[""])
    )
    assert r.passed and r.score == 0.8


def test_namespace_unknown_downgrades():
    r = t1.check_namespace_or_tenant_isolation(
        crit("c_namespace_or_tenant_isolation"), config(namespaces=None)
    )
    assert not r.passed and r.score == 0.5  # info severity stays info


# ----- registry and runner -------------------------------------------------


def test_registry_covers_all_tier1_criteria():
    tier1_ids = {c.id for c in RUBRIC.by_tier("tier_1_config")}
    assert set(t1.TIER1_CHECKS) == tier1_ids


def test_run_tier1_returns_result_per_criterion():
    cfg = config(
        dimension=768,
        distance_metric="cosine",
        index_type="hnsw",
        index_parameters={"M": 32, "ef_search": 100},
        vector_count=50000,
        replica_count=2,
        shard_count=1,
        refresh_cadence="hourly",
        metadata_schema={"model": "str", "source": "str"},
        namespaces=["a", "b"],
    )
    results = t1.run_tier1(RUBRIC, cfg)
    assert len(results) == 9
    assert {r.criterion_id for r in results} == {c.id for c in RUBRIC.by_tier("tier_1_config")}
    # This well-configured store should pass every Tier 1 check.
    assert all(r.passed for r in results)
