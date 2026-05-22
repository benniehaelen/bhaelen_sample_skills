"""Synthetic ground-truth generation.

Generates a ground-truth CSV from the store's own content using an LLM: for each
sampled document, it asks the model to write a few questions whose answer is in
that document, then records the document id as the relevant id for those
queries.

This is opt-in and always marked synthetic, both in a comment header (the format
the loader detects) and in each row's notes, because synthetic ground truth has
a known limitation: generated queries tend to reuse the source document's
vocabulary, which inflates retrieval scores. The prompt asks the model to vary
wording to soften this, but it cannot remove the effect. Tier 3 results from
synthetic ground truth should be read as indicative, not authoritative.

Unlike the optional --llm-assist failure classifier, generation cannot fall back
to a non-LLM path: it requires the anthropic SDK and an API key, and raises
GenerationError when they are absent.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("vector_store_linter")

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_DOC_CHARS = 2000  # trim passages so prompts stay small

_LIMITATION_NOTE = (
    "Synthetic ground truth tends to reuse source-document vocabulary, which inflates "
    "retrieval scores. Treat Tier 3 results as indicative, not authoritative."
)

_SYSTEM_PROMPT = (
    "You write evaluation queries for a document retrieval system. Given a passage, write the "
    "requested number of short, natural questions a user might ask whose answer is contained in "
    "the passage. Vary the wording from the passage where you can; do not copy phrases verbatim, "
    "because that would make retrieval trivial. Return only a JSON array of question strings, with "
    "no markdown and no commentary."
)


class GenerationError(Exception):
    """Raised when synthetic generation cannot proceed (no SDK, no key, or no usable content)."""


def generate_queries(
    documents: list[tuple[str, str]],
    queries_per_doc: int,
    *,
    client: Any | None = None,
    model: str | None = None,
) -> list[tuple[str, str]]:
    """Generate (query, doc_id) pairs from documents.

    Args:
        documents: list of (doc_id, content) pairs. Documents without content
            are skipped.
        queries_per_doc: number of queries to request per document.
        client: optional pre-built Anthropic client, used in tests.
        model: optional model override.

    Returns:
        A list of (query, relevant_doc_id) pairs. A document that errors is
        skipped with a warning rather than aborting the run.

    Raises:
        GenerationError: if no client can be built.
    """
    active_client = client or _build_client()
    chosen_model = model or os.environ.get("ANTHROPIC_MODEL") or _DEFAULT_MODEL

    pairs: list[tuple[str, str]] = []
    for doc_id, content in documents:
        if not content or not content.strip():
            continue
        try:
            queries = _generate_for_document(active_client, chosen_model, content, queries_per_doc)
        except Exception as exc:  # noqa: BLE001 - one bad document should not abort the run
            logger.warning("Query generation failed for document %s (%s); skipping.", doc_id, exc)
            continue
        for query in queries[:queries_per_doc]:
            pairs.append((query, doc_id))
    return pairs


def to_csv(pairs: list[tuple[str, str]], metadata: dict[str, Any]) -> str:
    """Render generated pairs as a ground-truth CSV marked synthetic."""
    buffer = io.StringIO()
    buffer.write("# synthetic: true\n")
    buffer.write("# generated_by: vector-store-linter\n")
    buffer.write(f"# generated_at: {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}\n")
    for key in ("store", "index_or_table", "queries_per_doc", "documents_sampled"):
        if metadata.get(key) is not None:
            buffer.write(f"# {key}: {metadata[key]}\n")
    buffer.write(f"# note: {_LIMITATION_NOTE}\n")

    writer = csv.writer(buffer)
    writer.writerow(["query", "relevant_doc_ids", "query_type", "notes"])
    for query, doc_id in pairs:
        writer.writerow([query, doc_id, "synthetic", f"Generated from {doc_id}"])
    return buffer.getvalue()


def _build_client() -> Any:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise GenerationError(
            "Synthetic generation requires the ANTHROPIC_API_KEY environment variable."
        )
    try:
        import anthropic
    except ImportError as exc:
        raise GenerationError(
            "Synthetic generation requires the anthropic SDK. Install it with: pip install anthropic"
        ) from exc
    return anthropic.Anthropic()


def _generate_for_document(client: Any, model: str, content: str, n: int) -> list[str]:
    response = client.messages.create(
        model=model,
        max_tokens=512,
        system=[{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"Number of questions: {n}\nPassage:\n{content[:_MAX_DOC_CHARS]}"}],
    )
    parts = [getattr(block, "text", "") for block in getattr(response, "content", []) or []]
    data = json.loads(_extract_json("\n".join(parts)))
    if not isinstance(data, list):
        raise ValueError("Model response was not a JSON array.")
    return [str(item).strip() for item in data if str(item).strip()]


def _extract_json(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text.strip()
