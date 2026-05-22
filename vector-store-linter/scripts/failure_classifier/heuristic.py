"""Heuristic failure classifier.

For a query that retrieved poorly, classify the likely failure mode using only
the data already on hand: the query text, the retrieved results (ids, scores,
metadata, content), the expected document ids, and, when available, the expected
documents' content and metadata. No API key and no model are required.

Each detector returns a confidence in [0, 1] and a one-line explanation.
Detectors degrade to 0 (and are not reported) when the data they need is
absent, which is common: scores are interpretable only for cosine metrics,
expected-document content may be unavailable, and metadata fields differ by
store. False positives are tolerable; false confidence is not, so confidences
are deliberately modest and only those at or above 0.5 are reported.

Score direction. Pinecone returns a similarity (higher is closer) and BigQuery
and pgvector return a distance (lower is closer). The context carries the metric
and a score_is_distance flag so semantic_distance reasons about a normalized
similarity rather than a raw score.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from adapters.base import RetrievalResult
from checks._common import MODEL_KEYS, SOURCE_KEYS, first_present_key

_MIN_CONFIDENCE = 0.5
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "by",
    "from", "is", "are", "was", "were", "be", "how", "what", "which", "do", "does",
    "i", "we", "our", "my", "this", "that", "it", "as", "at", "if",
})
_DATE_KEYS = ("timestamp", "date", "created_at", "updated_at", "modified_at",
              "published_at", "last_modified", "ingested_at")


@dataclass
class FailureCase:
    """One failing query and everything known about its retrieval."""

    query: str
    expected_ids: set[str]
    retrieved: list[RetrievalResult]
    expected_docs: dict[str, dict] = field(default_factory=dict)  # id -> {content, metadata}
    query_type: str | None = None


@dataclass
class ClassificationContext:
    """Cross-query context the detectors need."""

    appearance_counts: dict[str, int] = field(default_factory=dict)  # doc id -> queries it appeared in
    query_count: int = 0
    k: int = 10
    metric: str | None = None  # cosine / l2 / dot_product
    score_is_distance: bool = False
    newest_timestamp: Any = None  # datetime/date/iso string, or None
    thresholds: dict[str, Any] = field(default_factory=dict)
    # The rubric's failure-mode taxonomy ({id, name, description}), used by the
    # LLM classifier so the rubric is the single source. Empty means use the
    # built-in default taxonomy.
    failure_modes: list[dict] = field(default_factory=list)


@dataclass
class ClassifiedFailure:
    """One classified failure mode for a query."""

    mode: str
    confidence: float
    explanation: str


def classify(case: FailureCase, context: ClassificationContext) -> list[ClassifiedFailure]:
    """Return the failure modes detected for a case, confidence-sorted.

    Only modes with confidence at or above 0.5 are returned.
    """
    detectors: list[tuple[str, Callable[[FailureCase, ClassificationContext], tuple[float, str]]]] = [
        ("vocabulary_mismatch", _detect_vocabulary_mismatch),
        ("semantic_distance", _detect_semantic_distance),
        ("missing_metadata_filter", _detect_missing_metadata_filter),
        ("chunking_artifact", _detect_chunking_artifact),
        ("boilerplate_pollution", _detect_boilerplate_pollution),
        ("stale_content", _detect_stale_content),
        ("model_drift", _detect_model_drift),
    ]
    out: list[ClassifiedFailure] = []
    for mode, detector in detectors:
        confidence, explanation = detector(case, context)
        if confidence >= _MIN_CONFIDENCE:
            out.append(ClassifiedFailure(mode=mode, confidence=round(confidence, 2), explanation=explanation))
    out.sort(key=lambda c: c.confidence, reverse=True)
    return out


# ----- detectors -----------------------------------------------------------


def _detect_vocabulary_mismatch(case, context) -> tuple[float, str]:
    query_words = _words(case.query)
    if not query_words or not case.expected_docs:
        return 0.0, ""
    overlaps = [
        _coverage(query_words, _words(doc.get("content")))
        for doc in (case.expected_docs.get(eid) for eid in case.expected_ids)
        if doc
    ]
    if not overlaps:
        return 0.0, ""
    best = max(overlaps)
    if best >= 0.25:
        return 0.0, ""
    confidence = min(0.9, 0.5 + (0.25 - best) * 1.6)
    return confidence, f"Query shares only {best:.0%} of its terms with the relevant document, a vocabulary gap."


def _detect_semantic_distance(case, context) -> tuple[float, str]:
    if not case.retrieved:
        return 0.0, ""
    similarity = _to_similarity(case.retrieved[0].score, context.metric, context.score_is_distance)
    if similarity is None:
        return 0.0, ""
    threshold = float(context.thresholds.get("min_similarity", 0.5))
    if similarity >= threshold:
        return 0.0, ""
    confidence = min(0.9, 0.5 + (threshold - similarity))
    return confidence, (
        f"Best retrieved result has similarity {similarity:.2f}, below {threshold:.2f}; "
        "the query may sit far from the content in embedding space."
    )


def _detect_missing_metadata_filter(case, context) -> tuple[float, str]:
    top_ids = {r.doc_id for r in case.retrieved[: context.k]}
    if not case.retrieved or (case.expected_ids & top_ids):
        return 0.0, ""
    query_words = _words(case.query)
    if not query_words:
        return 0.0, ""
    overlaps = [_coverage(query_words, _words(r.content)) for r in case.retrieved[: context.k] if r.content]
    if not overlaps:
        return 0.0, ""
    strongest = max(overlaps)
    if strongest < 0.4:
        return 0.0, ""
    confidence = min(0.8, 0.4 + strongest * 0.5)
    return confidence, (
        "Relevant document is absent from the top results while lexically similar documents rank; "
        "a metadata filter may be missing."
    )


def _detect_chunking_artifact(case, context) -> tuple[float, str]:
    expected_sources = set()
    for eid in case.expected_ids:
        doc = case.expected_docs.get(eid)
        if doc:
            source = _source(doc.get("metadata") or {})
            if source:
                expected_sources.add(source)
    if not expected_sources:
        return 0.0, ""
    for r in case.retrieved[: context.k]:
        if r.doc_id in case.expected_ids:
            continue
        source = _source(r.metadata or {})
        if source and source in expected_sources:
            return 0.7, (
                f"A different chunk of the same source document ({source}) was retrieved "
                "instead of the relevant chunk."
            )
    return 0.0, ""


def _detect_boilerplate_pollution(case, context) -> tuple[float, str]:
    if not case.retrieved or context.query_count <= 0:
        return 0.0, ""
    threshold = float(context.thresholds.get("boilerplate_appearance_rate", 0.15))
    worst_doc = None
    worst_rate = 0.0
    for r in case.retrieved[: context.k]:
        rate = context.appearance_counts.get(r.doc_id, 0) / context.query_count
        if rate > worst_rate:
            worst_rate, worst_doc = rate, r.doc_id
    if worst_rate <= threshold:
        return 0.0, ""
    confidence = min(0.85, 0.5 + (worst_rate - threshold))
    return confidence, (
        f"Document {worst_doc} appears in {worst_rate:.0%} of query results; "
        "boilerplate or template content may be polluting retrieval."
    )


def _detect_stale_content(case, context) -> tuple[float, str]:
    newest = _coerce_datetime(context.newest_timestamp)
    if newest is None:
        return 0.0, ""
    age_days = int(context.thresholds.get("stale_age_days", 365))
    for r in case.retrieved[: context.k]:
        timestamp = _doc_timestamp(r.metadata or {})
        if timestamp and (newest - timestamp).days > age_days:
            return 0.6, (
                f"A retrieved chunk dated {timestamp.date().isoformat()} trails the newest content "
                f"({newest.date().isoformat()}) by over {age_days} days; it may be stale."
            )
    return 0.0, ""


def _detect_model_drift(case, context) -> tuple[float, str]:
    models = set()
    for r in case.retrieved[: context.k]:
        model = _model(r.metadata or {})
        if model:
            models.add(model)
    for eid in case.expected_ids:
        doc = case.expected_docs.get(eid)
        if doc:
            model = _model(doc.get("metadata") or {})
            if model:
                models.add(model)
    if len(models) > 1:
        return 0.7, (
            f"More than one embedding model seen across the query results and documents "
            f"({', '.join(sorted(models))}); embeddings may not be comparable."
        )
    return 0.0, ""


# ----- helpers -------------------------------------------------------------


def _words(text: Any) -> set[str]:
    if not isinstance(text, str):
        return set()
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOP and len(w) > 2}


def _coverage(query_words: set[str], doc_words: set[str]) -> float:
    """Fraction of query words present in the document."""
    if not query_words or not doc_words:
        return 0.0
    return len(query_words & doc_words) / len(query_words)


def _to_similarity(score: float, metric: str | None, score_is_distance: bool) -> float | None:
    """Convert a raw score to a cosine similarity in [0, 1], or None if not interpretable."""
    if metric != "cosine":
        return None  # L2 and dot product are not cleanly thresholdable here
    similarity = (1.0 - score) if score_is_distance else score
    return max(0.0, min(1.0, similarity))


def _source(metadata: dict) -> str | None:
    key = first_present_key(metadata, SOURCE_KEYS)
    if key is None:
        return None
    value = metadata.get(key)
    return str(value) if value not in (None, "") else None


def _model(metadata: dict) -> str | None:
    key = first_present_key(metadata, MODEL_KEYS)
    if key is None:
        return None
    value = metadata.get(key)
    return str(value) if value not in (None, "") else None


def newest_timestamp(metadatas) -> dt.datetime | None:
    """Return the latest timestamp found across metadata dicts, or None.

    Used to give the stale-content detector a reference for what counts as
    current, derived from the sampled vectors' date metadata.
    """
    newest: dt.datetime | None = None
    for metadata in metadatas:
        timestamp = _doc_timestamp(metadata or {})
        if timestamp and (newest is None or timestamp > newest):
            newest = timestamp
    return newest


def _doc_timestamp(metadata: dict) -> dt.datetime | None:
    key = first_present_key(metadata, _DATE_KEYS)
    if key is None:
        return None
    return _coerce_datetime(metadata.get(key))


def _coerce_datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None
