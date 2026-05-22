"""LLM-assisted failure classifier.

Same interface as the heuristic classifier (classify(case, context) returning a
list of ClassifiedFailure), but uses the Anthropic API to reason about why a
query missed. Triggered by the --llm-assist flag. It references the same failure
mode taxonomy as the heuristic so the two are comparable.

This path is best-effort and never breaks a run. It falls back to the heuristic
classifier, with a log line, when ANTHROPIC_API_KEY is not set, the anthropic
SDK is not installed, or any API or parse error occurs.

The static taxonomy is sent as a cached system block so it is billed once across
all of a run's failing queries rather than on every call. Cost is roughly 0.01
to 0.05 US dollars per failing query depending on how much retrieved content is
included; the model defaults to a small one and is configurable.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .heuristic import (
    ClassificationContext,
    ClassifiedFailure,
    FailureCase,
    _model,
    _source,
)
from .heuristic import classify as heuristic_classify

logger = logging.getLogger("vector_store_linter")

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MIN_CONFIDENCE = 0.5
_MAX_CONTENT_CHARS = 240  # trim retrieved content so prompts stay small

VALID_MODES = (
    "vocabulary_mismatch",
    "semantic_distance",
    "missing_metadata_filter",
    "chunking_artifact",
    "boilerplate_pollution",
    "stale_content",
    "model_drift",
)

_INSTRUCTIONS_PREFIX = (
    "You diagnose why a vector-store retrieval missed. Given a query, the documents that "
    "should have been retrieved, and the documents that actually were, classify the likely "
    "failure mode or modes. Use only this taxonomy:"
)
_INSTRUCTIONS_SUFFIX = (
    "Respond with only a JSON array of objects, each with keys mode, confidence (0 to 1), and "
    "explanation (one sentence). Report only modes you are at least moderately confident about. "
    "Do not invent modes outside the taxonomy. Use plain text, no markdown."
)

# Fallback taxonomy, used only when the caller passes no failure modes (a
# standalone call, for instance). In a normal run the rubric's failure_modes are
# threaded through the context and drive the prompt, so the rubric is the single
# source of truth and this stays in sync with the heuristic detectors.
_DEFAULT_TAXONOMY = (
    ("vocabulary_mismatch", "the query uses different words than the relevant document."),
    ("semantic_distance", "query and relevant document are related but the embedding places them far apart."),
    ("missing_metadata_filter", "the relevant document exists but is out-ranked by less-relevant ones sharing query terms."),
    ("chunking_artifact", "the answer was split across chunks and the wrong chunk retrieved."),
    ("boilerplate_pollution", "a header, footer, or template chunk retrieves against many queries."),
    ("stale_content", "the retrieved chunk references superseded or removed information."),
    ("model_drift", "query and documents were embedded with different models."),
)


def _taxonomy_lines(failure_modes: list[dict]) -> list[tuple[str, str]]:
    """Return (id, description) pairs from the context, or the default taxonomy."""
    if failure_modes:
        return [(fm["id"], fm.get("description", "")) for fm in failure_modes]
    return list(_DEFAULT_TAXONOMY)


def _build_system_prompt(failure_modes: list[dict]) -> str:
    """Assemble the system prompt, listing the rubric's modes when supplied."""
    lines = [f"- {mode_id}: {description}" for mode_id, description in _taxonomy_lines(failure_modes)]
    return _INSTRUCTIONS_PREFIX + "\n" + "\n".join(lines) + "\n" + _INSTRUCTIONS_SUFFIX


class _LLMUnavailable(Exception):
    """Raised when the LLM path cannot be used and the heuristic should run."""


def classify(
    case: FailureCase,
    context: ClassificationContext,
    *,
    client: Any | None = None,
    model: str | None = None,
) -> list[ClassifiedFailure]:
    """Classify a failure with the LLM, falling back to the heuristic on any problem."""
    try:
        active_client = client or _build_client()
    except _LLMUnavailable as exc:
        logger.info("LLM classifier unavailable (%s); using the heuristic classifier.", exc)
        return heuristic_classify(case, context)

    chosen_model = model or os.environ.get("ANTHROPIC_MODEL") or _DEFAULT_MODEL
    valid_modes = {mode_id for mode_id, _ in _taxonomy_lines(context.failure_modes)} or set(VALID_MODES)
    try:
        text = _call(active_client, chosen_model, case, context)
        return _parse(text, valid_modes)
    except Exception as exc:  # noqa: BLE001 - any failure falls back, never breaks the run
        logger.warning("LLM classification failed (%s); falling back to the heuristic.", exc)
        return heuristic_classify(case, context)


def _build_client() -> Any:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise _LLMUnavailable("ANTHROPIC_API_KEY is not set")
    try:
        import anthropic
    except ImportError as exc:
        raise _LLMUnavailable("anthropic SDK is not installed") from exc
    return anthropic.Anthropic()


def _call(client: Any, model: str, case: FailureCase, context: ClassificationContext) -> str:
    system_prompt = _build_system_prompt(context.failure_modes)
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _user_prompt(case, context)}],
    )
    # Concatenate any text blocks in the response.
    parts = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _user_prompt(case: FailureCase, context: ClassificationContext) -> str:
    lines = [f"Query: {case.query}"]
    if case.query_type:
        lines.append(f"Query type: {case.query_type}")
    lines.append(f"Expected document ids: {', '.join(sorted(case.expected_ids)) or '(none)'}")
    if context.metric:
        direction = "distance, lower is closer" if context.score_is_distance else "similarity, higher is closer"
        lines.append(f"Score metric: {context.metric} ({direction})")
    lines.append("Retrieved results (rank order):")
    for i, r in enumerate(case.retrieved[: context.k], start=1):
        snippet = (r.content or "")[:_MAX_CONTENT_CHARS].replace("\n", " ")
        source = _source(r.metadata or {})
        model = _model(r.metadata or {})
        extra = ", ".join(p for p in (f"source={source}" if source else "", f"model={model}" if model else "") if p)
        lines.append(f"  {i}. id={r.doc_id} score={r.score:.3f}" + (f" [{extra}]" if extra else "") + (f" :: {snippet}" if snippet else ""))
    if case.expected_docs:
        lines.append("Expected document content (where available):")
        for eid in sorted(case.expected_ids):
            doc = case.expected_docs.get(eid)
            if doc and doc.get("content"):
                lines.append(f"  {eid}: {str(doc['content'])[:_MAX_CONTENT_CHARS]}")
    return "\n".join(lines)


def _parse(text: str, valid_modes: set[str]) -> list[ClassifiedFailure]:
    payload = _extract_json(text)
    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError("LLM response was not a JSON array.")
    out: list[ClassifiedFailure] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        mode = item.get("mode")
        if mode not in valid_modes:
            continue  # ignore modes outside the taxonomy
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        confidence = max(0.0, min(1.0, confidence))
        if confidence < _MIN_CONFIDENCE:
            continue
        out.append(
            ClassifiedFailure(
                mode=mode,
                confidence=round(confidence, 2),
                explanation=str(item.get("explanation", "")).strip(),
            )
        )
    out.sort(key=lambda c: c.confidence, reverse=True)
    return out


def _extract_json(text: str) -> str:
    """Pull the JSON array out of the response, tolerating code fences or prose."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text.strip()
