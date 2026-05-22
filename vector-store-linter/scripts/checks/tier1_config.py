"""Tier 1 checks: configuration and shape.

Nine checks scored against a StoreConfig. Eight are pure configuration. One,
c_distance_metric_appropriate, also reads a small sample of vectors (when one is
supplied) to verify embedding normalization; it degrades to a config-only
judgment when no sample is available.

Each check has the signature check(criterion, config, vectors=None) and returns
a CheckResult. The criterion carries the id, the default severity, and the
thresholds, so the rubric stays the single source of truth. When a field a check
depends on is None (the store did not report it), the check tolerates the
unknown and downgrades its severity one step rather than firing at full
severity, following the project rule that an unreported field is not the store's
fault.

TIER1_CHECKS maps each criterion id to its check function. run_tier1 applies the
registered checks for every Tier 1 criterion in a rubric.

Several thresholds in this tier are domain judgments calibrated to the rubric
defaults (the normalization tolerance, the dot-product normalization
expectation, the scale and parameter cutoffs). They are read from the rubric so
they can be overridden per deployment.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import numpy as np

from adapters.base import StoreConfig, VectorRecord
from rubric import Criterion, Rubric
from scoring import CheckResult

from ._common import MODEL_KEYS as _MODEL_METADATA_KEYS
from ._common import finding as _finding
from ._common import first_present_key
from ._common import ok as _ok
from ._common import unknown as _unknown

logger = logging.getLogger("vector_store_linter")


def _index_family(index_type: Optional[str]) -> Optional[str]:
    """Classify an index_type string into flat, hnsw, ivf, or other; None if absent."""
    if not index_type:
        return None
    lowered = index_type.lower()
    for family in ("flat", "hnsw", "ivf"):
        if family in lowered:
            return family
    return "other"


def _normalized_fraction(vectors: list[VectorRecord], tolerance: float) -> Optional[float]:
    """Fraction of sampled vectors whose L2 norm is within tolerance of 1.0.

    Returns None when no embeddings are present in the sample.
    """
    norms = [float(np.linalg.norm(v.embedding)) for v in vectors if v.embedding]
    if not norms:
        return None
    arr = np.asarray(norms)
    return float(np.mean(np.abs(arr - 1.0) <= tolerance))


# ----- the nine checks -----------------------------------------------------


def check_dimension_consistency(criterion, config, vectors=None) -> CheckResult:
    """A single declared dimension means every vector shares it.

    Stores that fix the dimension at the index level (Pinecone, pgvector) cannot
    hold mixed dimensions once a dimension is declared, so a reported dimension
    is sufficient at the configuration layer.
    """
    if config.dimension is None:
        return _unknown(
            criterion,
            "Dimension not reported by the store; cannot confirm consistency.",
            evidence={"dimension": None},
        )
    if config.dimension <= 0:
        return _finding(
            criterion,
            f"Reported dimension {config.dimension} is not a positive value.",
            evidence={"dimension": config.dimension},
        )
    return _ok(
        criterion,
        f"Store declares a single dimension of {config.dimension}.",
        evidence={"dimension": config.dimension},
    )


def check_distance_metric_appropriate(criterion, config, vectors=None) -> CheckResult:
    """Distance metric should match embedding normalization.

    Cosine is safe regardless of normalization. L2 is appropriate for either,
    though cosine is the more conventional choice on normalized embeddings. Dot
    product on unnormalized embeddings favors high-magnitude vectors, which is
    occasionally intended but more often a mistake, so it is flagged when the
    sample is mostly unnormalized. Without a vector sample the check confirms the
    metric is a recognized value and notes that normalization was not verified.
    """
    metric = config.distance_metric
    if metric is None:
        return _unknown(criterion, "Distance metric not reported; cannot assess appropriateness.")

    tolerance = float(criterion.threshold.get("normalization_tolerance", 0.05))
    fraction = _normalized_fraction(vectors, tolerance) if vectors else None
    evidence: dict[str, Any] = {"distance_metric": metric, "normalized_fraction": fraction}

    if fraction is None:
        return _ok(
            criterion,
            f"Metric is {metric}; normalization not verified (no vector sample read).",
            score=0.8,
            evidence=evidence,
        )

    if metric == "cosine":
        return _ok(criterion, "Cosine metric is appropriate for these embeddings.", evidence=evidence)
    if metric == "l2":
        if fraction >= 0.9:
            return _ok(
                criterion,
                "L2 metric works here, though cosine is more conventional for normalized embeddings.",
                score=0.9,
                evidence=evidence,
            )
        return _ok(criterion, "L2 metric is appropriate for unnormalized embeddings.", evidence=evidence)
    if metric == "dot_product":
        if fraction >= 0.9:
            return _ok(
                criterion,
                "Dot product on normalized embeddings is equivalent to cosine and is appropriate.",
                evidence=evidence,
            )
        return _finding(
            criterion,
            f"Dot product with mostly unnormalized embeddings ({fraction:.0%} normalized) favors "
            "high-magnitude vectors; confirm this is intended.",
            score=0.4,
            evidence=evidence,
        )
    return _ok(
        criterion,
        f"Metric {metric!r} is not a standard value; normalization is {fraction:.0%}.",
        score=0.6,
        evidence=evidence,
    )


def check_index_type_for_scale(criterion, config, vectors=None) -> CheckResult:
    """Index type should match the vector count.

    A flat index is too slow above the flat cap; HNSW below the HNSW floor is
    heavier than the scale needs. Managed index types that do not map to a known
    family cannot be assessed for scale fit and pass with a note.
    """
    family = _index_family(config.index_type)
    count = config.vector_count
    flat_max = int(criterion.threshold.get("flat_max_vectors", 100000))
    hnsw_min = int(criterion.threshold.get("hnsw_min_vectors", 1000))
    evidence = {"index_type": config.index_type, "vector_count": count}

    if family is None or count is None:
        return _unknown(
            criterion,
            "Index type or vector count not reported; cannot assess scale fit.",
            evidence=evidence,
        )
    if family == "flat" and count > flat_max:
        return _finding(
            criterion,
            f"Flat index holds {count} vectors, above the {flat_max} flat limit; use HNSW or IVF.",
            evidence=evidence,
        )
    if family == "hnsw" and count < hnsw_min:
        return _finding(
            criterion,
            f"HNSW index at {count} vectors is heavier than needed below {hnsw_min}; flat is simpler.",
            score=0.5,
            evidence=evidence,
        )
    if family == "other":
        return _ok(
            criterion,
            f"Index type {config.index_type!r} is managed; scale fit is handled by the store.",
            score=0.8,
            evidence=evidence,
        )
    return _ok(criterion, f"Index type {family} suits {count} vectors.", evidence=evidence)


def check_hnsw_parameters_tuned(criterion, config, vectors=None) -> CheckResult:
    """HNSW parameters should clear sensible minimums.

    Applies only to HNSW indexes. For other or managed index types the criterion
    does not apply and passes with a note rather than penalizing the store.
    """
    family = _index_family(config.index_type)
    if family != "hnsw":
        return _ok(
            criterion,
            "Not an HNSW index; parameter tuning does not apply.",
            evidence={"index_type": config.index_type},
        )
    params = config.index_parameters or {}
    ef_min = int(criterion.threshold.get("ef_min", 50))
    m_min = int(criterion.threshold.get("m_min", 16))
    ef = params.get("ef_search", params.get("ef_construction", params.get("ef")))
    m = params.get("M", params.get("m"))
    evidence = {"ef": ef, "m": m, "ef_min": ef_min, "m_min": m_min}

    if ef is None and m is None:
        return _unknown(criterion, "HNSW parameters not reported; cannot assess tuning.", evidence=evidence)
    below = []
    if ef is not None and ef < ef_min:
        below.append(f"ef {ef} below {ef_min}")
    if m is not None and m < m_min:
        below.append(f"M {m} below {m_min}")
    if below:
        return _finding(
            criterion,
            "HNSW parameters look like defaults or are under the recommended floor: "
            + "; ".join(below) + ".",
            score=0.5,
            evidence=evidence,
        )
    return _ok(criterion, "HNSW parameters clear the recommended minimums.", evidence=evidence)


def check_freshness_configured(criterion, config, vectors=None) -> CheckResult:
    """A documented refresh cadence keeps the store aligned with its source.

    No store API reports this reliably, so an absent cadence is treated as
    unknown and downgraded rather than failed.
    """
    if config.refresh_cadence:
        return _ok(
            criterion,
            f"Refresh cadence recorded as {config.refresh_cadence}.",
            evidence={"refresh_cadence": config.refresh_cadence},
        )
    return _unknown(
        criterion,
        "No refresh cadence recorded. Document the refresh interval and freshness expectation.",
        evidence={"refresh_cadence": None},
    )


def check_replication_for_scale(criterion, config, vectors=None) -> CheckResult:
    """Production-scale stores need more than a single shard and replica.

    Managed stores that autoscale do not report replica or shard counts; those
    pass with a note. A single replica and shard at production scale is flagged.
    """
    count = config.vector_count
    threshold = int(criterion.threshold.get("production_vector_threshold", 100000))
    evidence = {
        "vector_count": count,
        "replica_count": config.replica_count,
        "shard_count": config.shard_count,
    }
    if count is None:
        return _unknown(criterion, "Vector count not reported; cannot assess replication need.", evidence=evidence)
    if config.replica_count is None and config.shard_count is None:
        return _ok(
            criterion,
            "Replica and shard counts not reported; assumed managed or autoscaled.",
            score=0.9,
            evidence=evidence,
        )
    if count > threshold and (config.replica_count or 1) <= 1 and (config.shard_count or 1) <= 1:
        return _finding(
            criterion,
            f"Single replica and shard at {count} vectors (above {threshold}); consider scaling out.",
            score=0.3,
            evidence=evidence,
        )
    return _ok(criterion, "Replication and sharding are adequate for the reported scale.", evidence=evidence)


def check_metadata_schema_declared(criterion, config, vectors=None) -> CheckResult:
    """A declared metadata schema is what makes filter-based retrieval usable."""
    schema = config.metadata_schema
    if schema:
        return _ok(
            criterion,
            f"Metadata schema declares {len(schema)} field(s).",
            evidence={"field_count": len(schema)},
        )
    return _unknown(
        criterion,
        "No metadata schema declared. Filter-based retrieval depends on a consistent, documented schema.",
        evidence={"metadata_schema": None},
    )


def check_embedding_model_recorded(criterion, config, vectors=None) -> CheckResult:
    """The embedding model should be identifiable from vector metadata.

    When a vector sample is available, this measures the fraction of vectors
    that record a model-identifying field and compares it to min_coverage. When
    no sample is available (a configuration audit), it falls back to checking
    that the declared metadata schema includes a model field.
    """
    min_coverage = float(criterion.threshold.get("min_coverage", 0.95))

    if vectors:
        present = sum(
            1 for v in vectors
            if (key := first_present_key(v.metadata, _MODEL_METADATA_KEYS)) and v.metadata.get(key) not in (None, "")
        )
        coverage = present / len(vectors)
        evidence = {"model_field_coverage": round(coverage, 4), "min_coverage": min_coverage, "sample_size": len(vectors)}
        if coverage >= min_coverage:
            return _ok(
                criterion,
                f"{coverage:.0%} of sampled vectors record an embedding model (min {min_coverage:.0%}).",
                evidence=evidence,
            )
        return _finding(
            criterion,
            f"Only {coverage:.0%} of sampled vectors record an embedding model (min {min_coverage:.0%}); "
            "silent model drift would be invisible.",
            score=round(coverage, 4),
            evidence=evidence,
        )

    schema = config.metadata_schema
    if not schema:
        return _unknown(
            criterion,
            "No vector sample and no metadata schema, so an embedding-model field cannot be confirmed.",
            evidence={"metadata_schema": None},
        )
    declared = [key for key in schema if key.lower() in _MODEL_METADATA_KEYS]
    if declared:
        return _ok(
            criterion,
            f"Metadata schema declares an embedding-model field ({declared[0]}); coverage not verified.",
            score=0.8,
            evidence={"model_fields": declared},
        )
    return _finding(
        criterion,
        "Metadata schema is declared but has no embedding-model field; model drift would be silent.",
        score=0.3,
        evidence={"schema_fields": list(schema)},
    )


def check_namespace_or_tenant_isolation(criterion, config, vectors=None) -> CheckResult:
    """Multiple namespaces indicate tenant isolation is in use.

    A single namespace is correct for a single-tenant store and a risk for a
    multi-tenant one, and configuration alone cannot tell which, so a single
    namespace passes with a note rather than a penalty.
    """
    namespaces = config.namespaces
    if namespaces is None:
        return _unknown(
            criterion,
            "Namespace configuration not reported; cannot assess tenant isolation.",
            evidence={"namespaces": None},
        )
    meaningful = [n for n in namespaces if n]
    if len(meaningful) > 1:
        return _ok(
            criterion,
            f"{len(meaningful)} namespaces in use, consistent with tenant isolation.",
            evidence={"namespace_count": len(meaningful)},
        )
    return _ok(
        criterion,
        "Single namespace in use. Confirm this store serves a single tenant.",
        score=0.8,
        evidence={"namespace_count": len(meaningful)},
    )


TIER1_CHECKS: dict[str, Callable[..., CheckResult]] = {
    "c_dimension_consistency": check_dimension_consistency,
    "c_distance_metric_appropriate": check_distance_metric_appropriate,
    "c_index_type_for_scale": check_index_type_for_scale,
    "c_hnsw_parameters_tuned": check_hnsw_parameters_tuned,
    "c_freshness_configured": check_freshness_configured,
    "c_replication_for_scale": check_replication_for_scale,
    "c_metadata_schema_declared": check_metadata_schema_declared,
    "c_embedding_model_recorded": check_embedding_model_recorded,
    "c_namespace_or_tenant_isolation": check_namespace_or_tenant_isolation,
}


def run_tier1(
    rubric: Rubric,
    config: StoreConfig,
    vectors: list[VectorRecord] | None = None,
) -> list[CheckResult]:
    """Run every registered Tier 1 check for the rubric against a store config.

    Args:
        rubric: The rubric whose Tier 1 criteria drive the run.
        config: The store configuration to check.
        vectors: Optional small sample, used only by the normalization check.

    Returns:
        One CheckResult per Tier 1 criterion that has a registered check.
    """
    results: list[CheckResult] = []
    for criterion in rubric.by_tier("tier_1_config"):
        check = TIER1_CHECKS.get(criterion.id)
        if check is None:
            logger.warning("No Tier 1 check registered for %r; skipping.", criterion.id)
            continue
        results.append(check(criterion, config, vectors))
    return results
