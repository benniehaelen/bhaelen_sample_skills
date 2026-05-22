"""Tier 2 checks: content quality.

Eight checks scored against a materialized sample of vectors (a list of
VectorRecord). The caller iterates the adapter once to build the list, so these
checks do not re-read the store; they share the one sample.

Signature is check(criterion, config, vectors), matching the Tier 1 shape. Each
check degrades to an unknown (partial credit, downgraded severity) when the data
it needs is absent: no embeddings, no content field, or no model field. That is
common, since stores differ in what they carry.

Cost notes. Exact-duplicate detection hashes embeddings. Near-duplicate
detection is pairwise cosine over the sample, computed in row blocks to bound
memory and capped at a configurable vector count (a random subsample is used
above the cap, and the report says so). Embedding distribution runs a small
numpy k-means; no scikit-learn. Token counts are estimated from character length
(roughly four characters per token) because the dependency set has no tokenizer;
the estimate is documented in the output.

Honesty note. c_no_orphan_references checks for missing or empty source
references among vectors that carry a source field. It does not verify that a
referenced source still exists, which would require access this read-only skill
does not have; the output says so.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Callable, Optional

import numpy as np

from adapters.base import StoreConfig, VectorRecord
from rubric import Criterion, Rubric
from scoring import CheckResult

from ._common import CONTENT_KEYS, MODEL_KEYS, SOURCE_KEYS, first_present_key
from ._common import finding as _finding
from ._common import ok as _ok
from ._common import unknown as _unknown

logger = logging.getLogger("vector_store_linter")

_NEAR_DUP_MAX_VECTORS = 10000  # cap on pairwise comparison; subsample above this
_NEAR_DUP_BLOCK = 512
_KMEANS_K = 10
_KMEANS_MIN_VECTORS = 20  # below this, distribution is not assessed reliably
_KMEANS_ITERS = 10
_CHARS_PER_TOKEN = 4  # documented token estimate; no tokenizer dependency


# ----- shared numeric helpers ----------------------------------------------


def _matrix(vectors: list[VectorRecord]) -> Optional[np.ndarray]:
    """Stack embeddings of the most common dimension into a float array."""
    embeddings = [v.embedding for v in vectors if v.embedding]
    if not embeddings:
        return None
    lengths = Counter(len(e) for e in embeddings)
    dimension = lengths.most_common(1)[0][0]
    rows = [e for e in embeddings if len(e) == dimension]
    return np.asarray(rows, dtype=float)


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _non_whitespace_len(text: str) -> int:
    return len("".join(text.split()))


def _present(metadata: dict, field: str) -> bool:
    value = metadata.get(field)
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _kmeans_labels(matrix: np.ndarray, k: int, iters: int = _KMEANS_ITERS, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = matrix.shape[0]
    k = max(1, min(k, n))
    centroids = matrix[rng.choice(n, k, replace=False)].astype(float).copy()
    labels = np.full(n, -1)
    for _ in range(iters):
        distances = (
            (matrix ** 2).sum(1)[:, None]
            - 2 * matrix @ centroids.T
            + (centroids ** 2).sum(1)[None, :]
        )
        new_labels = distances.argmin(1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for j in range(k):
            members = matrix[labels == j]
            if members.shape[0]:
                centroids[j] = members.mean(0)
    return labels


# ----- the eight checks ----------------------------------------------------


def check_no_exact_duplicates(criterion, config, vectors) -> CheckResult:
    """Fraction of vectors that are byte-identical duplicates of another."""
    keys = [tuple(v.embedding) for v in vectors if v.embedding]
    if not keys:
        return _unknown(criterion, "No embeddings available to check for exact duplicates.")
    n = len(keys)
    duplicate_count = n - len(set(keys))
    rate = duplicate_count / n
    max_rate = float(criterion.threshold.get("max_duplicate_rate", 0.01))
    evidence = {"duplicate_count": duplicate_count, "duplicate_rate": round(rate, 4), "sample_size": n}
    if rate <= max_rate:
        return _ok(criterion, f"Exact-duplicate rate {rate:.1%} within the {max_rate:.0%} limit.", evidence=evidence)
    score = 0.5 if rate <= 2 * max_rate else 0.0
    return _finding(criterion, f"Exact-duplicate rate {rate:.1%} exceeds the {max_rate:.0%} limit.", score=score, evidence=evidence)


def check_no_near_duplicates(criterion, config, vectors) -> CheckResult:
    """Fraction of vectors with a cosine neighbor at or above the threshold."""
    matrix = _matrix(vectors)
    if matrix is None or matrix.shape[0] < 2:
        return _unknown(criterion, "Too few embeddings to assess near-duplicates.")
    similarity = float(criterion.threshold.get("similarity_threshold", 0.98))
    max_rate = float(criterion.threshold.get("max_near_duplicate_rate", 0.05))
    cap = int(criterion.threshold.get("max_vectors_for_pairwise", _NEAR_DUP_MAX_VECTORS))
    subsampled = False
    if matrix.shape[0] > cap:
        rng = np.random.default_rng(42)
        matrix = matrix[rng.choice(matrix.shape[0], cap, replace=False)]
        subsampled = True
    normalized = _normalize_rows(matrix)
    n = normalized.shape[0]
    has_near = np.zeros(n, dtype=bool)
    for start in range(0, n, _NEAR_DUP_BLOCK):
        block = normalized[start : start + _NEAR_DUP_BLOCK]
        sims = block @ normalized.T
        for i in range(block.shape[0]):
            sims[i, start + i] = -1.0  # exclude self
        has_near[start : start + block.shape[0]] = (sims >= similarity).any(axis=1)
    rate = float(has_near.mean())
    evidence = {
        "near_duplicate_rate": round(rate, 4),
        "similarity_threshold": similarity,
        "sample_size": n,
        "subsampled": subsampled,
    }
    if rate <= max_rate:
        return _ok(criterion, f"Near-duplicate rate {rate:.1%} within the {max_rate:.0%} limit.", evidence=evidence)
    score = 0.5 if rate <= 2 * max_rate else 0.0
    return _finding(criterion, f"Near-duplicate rate {rate:.1%} exceeds the {max_rate:.0%} limit.", score=score, evidence=evidence)


def check_chunk_size_distribution(criterion, config, vectors) -> CheckResult:
    """Fraction of chunks whose estimated token length is out of band."""
    contents = [v.content for v in vectors if isinstance(v.content, str) and v.content.strip()]
    if not contents:
        return _unknown(criterion, "No chunk content available to assess size distribution.")
    t = criterion.threshold
    min_tokens = int(t.get("min_acceptable_tokens", 50))
    max_tokens = int(t.get("max_acceptable_tokens", 2000))
    max_outlier_rate = float(t.get("max_outlier_rate", 0.10))
    tokens = [_estimate_tokens(c) for c in contents]
    outliers = sum(1 for x in tokens if x < min_tokens or x > max_tokens)
    rate = outliers / len(tokens)
    evidence = {
        "outlier_rate": round(rate, 4),
        "median_estimated_tokens": int(np.median(tokens)),
        "min_acceptable_tokens": min_tokens,
        "max_acceptable_tokens": max_tokens,
        "sample_with_content": len(tokens),
        "note": "token counts estimated from character length",
    }
    if rate <= max_outlier_rate:
        return _ok(criterion, f"Chunk-size outliers {rate:.1%} within the {max_outlier_rate:.0%} limit.", evidence=evidence)
    return _finding(criterion, f"Chunk-size outliers {rate:.1%} exceed the {max_outlier_rate:.0%} limit.", score=max(0.0, 1.0 - rate), evidence=evidence)


def check_no_empty_content(criterion, config, vectors) -> CheckResult:
    """Chunks whose non-whitespace content is under the minimum length."""
    with_content = [v for v in vectors if v.content is not None]
    if not with_content:
        return _unknown(criterion, "No content field available to check for empty chunks.")
    min_chars = int(criterion.threshold.get("min_non_whitespace_chars", 100))
    empty = [v for v in with_content if _non_whitespace_len(v.content or "") < min_chars]
    rate = len(empty) / len(with_content)
    evidence = {
        "empty_count": len(empty),
        "empty_rate": round(rate, 4),
        "min_non_whitespace_chars": min_chars,
        "sample_with_content": len(with_content),
    }
    if not empty:
        return _ok(criterion, "No empty or near-empty chunks found.", evidence=evidence)
    return _finding(
        criterion,
        f"{len(empty)} chunk(s) under {min_chars} non-whitespace characters ({rate:.1%}).",
        score=max(0.0, 1.0 - rate),
        evidence=evidence,
    )


def check_metadata_completeness(criterion, config, vectors) -> CheckResult:
    """Coverage of expected metadata fields across the sample."""
    expected = list(criterion.threshold.get("required_fields") or [])
    if not expected and config.metadata_schema:
        expected = list(config.metadata_schema.keys())
    if not expected:
        return _ok(criterion, "No required metadata fields declared; nothing to verify.", evidence={"expected_fields": []})
    if not vectors:
        return _unknown(criterion, "No vectors sampled to assess metadata completeness.")
    min_coverage = float(criterion.threshold.get("min_field_coverage", 0.90))
    n = len(vectors)
    coverage = {field: sum(1 for v in vectors if _present(v.metadata, field)) / n for field in expected}
    incomplete = {f: round(c, 4) for f, c in coverage.items() if c < min_coverage}
    evidence = {"coverage": {f: round(c, 4) for f, c in coverage.items()}, "min_field_coverage": min_coverage}
    if not incomplete:
        return _ok(criterion, f"All {len(expected)} expected metadata field(s) meet {min_coverage:.0%} coverage.", evidence=evidence)
    score = (len(expected) - len(incomplete)) / len(expected)
    return _finding(
        criterion,
        f"{len(incomplete)} metadata field(s) below {min_coverage:.0%} coverage: {', '.join(sorted(incomplete))}.",
        score=score,
        evidence=evidence,
    )


def check_embedding_distribution(criterion, config, vectors) -> CheckResult:
    """Largest k-means cluster as a fraction of the sample."""
    matrix = _matrix(vectors)
    n = 0 if matrix is None else matrix.shape[0]
    if matrix is None or n < _KMEANS_MIN_VECTORS:
        return _ok(criterion, f"Too few embeddings ({n}) to assess distribution reliably.", score=0.8, evidence={"sample_size": n})
    max_concentration = float(criterion.threshold.get("max_cluster_concentration", 0.30))
    normalized = _normalize_rows(matrix)
    labels = _kmeans_labels(normalized, _KMEANS_K)
    sizes = np.bincount(labels)
    concentration = float(sizes.max() / n)
    evidence = {"max_cluster_concentration": round(concentration, 4), "k": int(min(_KMEANS_K, n)), "sample_size": n}
    if concentration <= max_concentration:
        return _ok(criterion, f"Largest cluster holds {concentration:.0%}, within the {max_concentration:.0%} limit.", evidence=evidence)
    return _finding(
        criterion,
        f"Largest cluster holds {concentration:.0%}, above the {max_concentration:.0%} limit.",
        score=max(0.0, 1.0 - (concentration - max_concentration)),
        evidence=evidence,
    )


def check_no_orphan_references(criterion, config, vectors) -> CheckResult:
    """Vectors missing a source-document reference.

    Detects missing or empty source references. It does not verify that a
    referenced source still exists, which would require external access the
    skill does not have; the message says so.
    """
    if not vectors:
        return _unknown(criterion, "No vectors sampled to check for orphan references.")
    source_field = None
    for v in vectors:
        key = first_present_key(v.metadata, SOURCE_KEYS)
        if key:
            source_field = key
            break
    if source_field is None:
        return _finding(
            criterion,
            "No source-reference field found; orphan detection does not apply. Source existence is not verified.",
            score=0.7,
            downgrade=True,
            evidence={"source_field": None},
        )
    missing = [v for v in vectors if not _present(v.metadata, source_field)]
    rate = len(missing) / len(vectors)
    evidence = {
        "source_field": source_field,
        "missing_source_count": len(missing),
        "missing_rate": round(rate, 4),
        "note": "presence checked; referenced source existence is not verified",
    }
    if not missing:
        return _ok(criterion, f"All sampled vectors carry a source reference in '{source_field}'.", evidence=evidence)
    return _finding(
        criterion,
        f"{len(missing)} vector(s) lack a source reference in '{source_field}' ({rate:.1%}).",
        score=max(0.0, 1.0 - rate),
        evidence=evidence,
    )


def check_embedding_model_homogeneous(criterion, config, vectors) -> CheckResult:
    """Whether all sampled vectors record the same embedding model."""
    if not vectors:
        return _unknown(criterion, "No vectors sampled to check model homogeneity.")
    models: set[str] = set()
    seen_field = False
    for v in vectors:
        key = first_present_key(v.metadata, MODEL_KEYS)
        if key:
            seen_field = True
            value = v.metadata.get(key)
            if value is not None:
                models.add(str(value))
    if not seen_field:
        return _unknown(criterion, "No embedding-model field in metadata; cannot verify homogeneity.", evidence={"distinct_models": []})
    evidence = {"distinct_models": sorted(models), "model_count": len(models)}
    if len(models) <= 1:
        only = next(iter(models), "unknown")
        return _ok(criterion, f"All sampled vectors share one embedding model ({only}).", evidence=evidence)
    return _finding(criterion, f"Multiple embedding models present: {', '.join(sorted(models))}.", score=0.0, evidence=evidence)


TIER2_CHECKS: dict[str, Callable[..., CheckResult]] = {
    "c_no_exact_duplicates": check_no_exact_duplicates,
    "c_no_near_duplicates": check_no_near_duplicates,
    "c_chunk_size_distribution": check_chunk_size_distribution,
    "c_no_empty_content": check_no_empty_content,
    "c_metadata_completeness": check_metadata_completeness,
    "c_embedding_distribution": check_embedding_distribution,
    "c_no_orphan_references": check_no_orphan_references,
    "c_embedding_model_homogeneous": check_embedding_model_homogeneous,
}


def run_tier2(rubric: Rubric, config: StoreConfig, vectors: list[VectorRecord]) -> list[CheckResult]:
    """Run every registered Tier 2 check over a materialized vector sample.

    Args:
        rubric: The rubric whose Tier 2 criteria drive the run.
        config: The store configuration (used by the completeness check).
        vectors: The materialized sample; the adapter is iterated once by the
            caller to build it, and all checks share it.

    Returns:
        One CheckResult per Tier 2 criterion that has a registered check.
    """
    results: list[CheckResult] = []
    for criterion in rubric.by_tier("tier_2_content"):
        check = TIER2_CHECKS.get(criterion.id)
        if check is None:
            logger.warning("No Tier 2 check registered for %r; skipping.", criterion.id)
            continue
        results.append(check(criterion, config, vectors))
    return results
