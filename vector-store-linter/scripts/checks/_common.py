"""Shared helpers for the check modules.

Severity downgrade and CheckResult builders used by both the Tier 1 and Tier 2
checks, plus the metadata key lists they share. Keeping these in one module
avoids drift between the tiers in how an unknown field is downgraded.
"""

from __future__ import annotations

from rubric import Criterion
from scoring import CheckResult

# One-step severity downgrade applied when a depended-on field is unknown.
_DOWNGRADE = {"critical": "warn", "fail": "warn", "warn": "info", "info": "info"}

# Metadata keys commonly carrying the embedded text, the embedding model, and a
# source-document reference, in priority order.
CONTENT_KEYS = ("text", "content", "chunk_text", "page_content", "body", "document")
MODEL_KEYS = ("model", "embedding_model", "model_id", "embed_model", "encoder")
SOURCE_KEYS = ("source", "source_id", "source_path", "document_id", "doc_id", "url", "uri", "path")


def downgrade_severity(severity: str) -> str:
    """Return the severity one step lower, used when a field is unknown."""
    return _DOWNGRADE.get(severity, "info")


def ok(criterion: Criterion, message: str, *, score: float = 1.0, evidence: dict | None = None) -> CheckResult:
    """A passing result at the criterion's default severity."""
    return CheckResult(
        criterion_id=criterion.id,
        passed=True,
        score=score,
        severity=criterion.severity,
        evidence=evidence or {},
        message=message,
    )


def finding(
    criterion: Criterion,
    message: str,
    *,
    score: float = 0.0,
    evidence: dict | None = None,
    downgrade: bool = False,
) -> CheckResult:
    """A failing result, optionally with severity downgraded one step."""
    return CheckResult(
        criterion_id=criterion.id,
        passed=False,
        score=score,
        severity=downgrade_severity(criterion.severity) if downgrade else criterion.severity,
        evidence=evidence or {},
        message=message,
    )


def unknown(criterion: Criterion, message: str, *, evidence: dict | None = None) -> CheckResult:
    """A field the check needs was not reported. Partial credit, downgraded severity."""
    return finding(criterion, message, score=0.5, evidence=evidence, downgrade=True)


def first_present_key(mapping: dict, keys: tuple[str, ...]) -> str | None:
    """Return the first key from keys present in mapping (case-insensitive)."""
    lowered = {k.lower(): k for k in mapping}
    for key in keys:
        if key in lowered:
            return lowered[key]
    return None
