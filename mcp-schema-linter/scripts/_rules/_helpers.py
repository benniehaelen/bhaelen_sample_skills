"""Shared helpers for rule modules.

Pure functions. Each rule module imports what it needs from here so the
parameter walk, sibling detection, verb classification, and evidence
trimming logic live in one place.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Iterator

from _tokenizer import count_tokens

_WORD_RE = re.compile(r"[A-Za-z0-9]+")

# Filtered out before Jaccard sibling detection so common connectives do
# not inflate the similarity score on short descriptions.
_STOP: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "by", "from", "is", "are", "was", "were", "be", "been", "this", "that",
    "it", "its", "as", "at", "if", "then", "but", "not", "use", "tool",
})


def count_in(text: str, rubric: dict[str, Any]) -> int:
    """Tokenize ``text`` under the rubric's chosen tokenizer."""
    return count_tokens(text, rubric.get("tokenizer", "cl100k_base"))


def iter_parameters(schema: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(name, prop_schema)`` for each top-level property.

    Does not recurse into nested objects. Rubric thresholds are calibrated
    against top-level parameters as they appear in the model's input
    schema; nested rule support is out of scope for v1.
    """
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return
    for name, sub in props.items():
        if isinstance(sub, dict):
            yield name, sub


def name_segments(name: str) -> list[str]:
    """Split a tool name on underscores and return lowercase segments."""
    return [seg for seg in (name or "").lower().split("_") if seg]


def starts_with_verb(name: str, verb_list: Iterable[str]) -> bool:
    """True if the first underscore-segment of ``name`` is in ``verb_list``."""
    segs = name_segments(name)
    return bool(segs) and segs[0] in {v.lower() for v in verb_list}


def is_mutating(tool: dict[str, Any], rubric: dict[str, Any]) -> bool:
    """Best-effort classification of whether a tool changes state.

    Returns True if the name's first segment matches one of the rubric's
    ``destructive_verbs``, OR a destructive verb appears in the first
    ~120 chars of the description. The rubric's ``read_only_verbs`` list
    takes precedence: a tool whose name starts with a read-only verb is
    never classified as mutating.
    """
    destructive = rubric.get("destructive_verbs", [])
    read_only = rubric.get("read_only_verbs", [])
    name = tool.get("name", "") or ""
    if starts_with_verb(name, read_only):
        return False
    if starts_with_verb(name, destructive):
        return True
    desc = (tool.get("description") or "").lower()
    head_words = set(_WORD_RE.findall(desc[:120]))
    return any(v.lower() in head_words for v in destructive)


def description_words(text: str) -> set[str]:
    """Lowercase non-stop word tokens, used for Jaccard sibling detection."""
    return {
        w for w in (s.lower() for s in _WORD_RE.findall(text or ""))
        if w not in _STOP and len(w) > 2
    }


def description_jaccard(a: str, b: str) -> float:
    """Jaccard similarity of stop-filtered word sets from two descriptions."""
    sa, sb = description_words(a), description_words(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def find_name_siblings(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> list[dict[str, Any]]:
    """Tools sharing a near-synonym verb AND a common non-verb segment.

    Example: ``get_user`` and ``fetch_user`` are name-siblings because
    ``get``/``fetch`` are in the same near-synonym group and ``user`` is
    a shared segment. ``get_user`` and ``create_invoice`` are not.
    """
    synonym_groups = rubric.get("near_synonym_pairs", [])
    synonym_lookup: dict[str, set[str]] = {}
    for group in synonym_groups:
        members = {m.lower() for m in group}
        for m in members:
            synonym_lookup[m] = members

    my_segs = name_segments(tool.get("name", ""))
    if not my_segs:
        return []
    my_verb = my_segs[0]
    my_rest = set(my_segs[1:])

    siblings: list[dict[str, Any]] = []
    for other in catalog.get("tools", []):
        if other is tool or other.get("name") == tool.get("name"):
            continue
        their_segs = name_segments(other.get("name", ""))
        if not their_segs:
            continue
        their_verb = their_segs[0]
        their_rest = set(their_segs[1:])
        same_verb_group = (
            my_verb == their_verb
            or (my_verb in synonym_lookup and their_verb in synonym_lookup.get(my_verb, set()))
        )
        if same_verb_group and (my_rest & their_rest):
            siblings.append(other)
    return siblings


def find_overlap_siblings(tool: dict[str, Any], catalog: dict[str, Any], rubric: dict[str, Any]) -> list[dict[str, Any]]:
    """Tools whose descriptions Jaccard-overlap above the rubric threshold."""
    threshold = float(rubric.get("siblings_similarity_threshold", 0.7))
    my_desc = tool.get("description", "")
    out: list[dict[str, Any]] = []
    for other in catalog.get("tools", []):
        if other is tool or other.get("name") == tool.get("name"):
            continue
        if description_jaccard(my_desc, other.get("description", "")) >= threshold:
            out.append(other)
    return out


def truncate_evidence(text: str, rubric: dict[str, Any]) -> str:
    """Single-line evidence trimmed to ``thresholds.evidence_max_chars``."""
    max_chars = int(rubric.get("thresholds", {}).get("evidence_max_chars", 120))
    flat = " ".join((text or "").split())
    if len(flat) <= max_chars:
        return flat
    return flat[: max_chars - 1] + "..."


def estimated_midpoint(rubric: dict[str, Any], low_key: str, high_key: str) -> int:
    """Midpoint of a ``thresholds`` cost range; used for Tier 2 estimates."""
    t = rubric.get("thresholds", {})
    low = int(t.get(low_key, 0))
    high = int(t.get(high_key, low))
    return (low + high) // 2
