"""Failure-mode classification for poorly-retrieving queries."""

from __future__ import annotations

from .heuristic import (
    ClassificationContext,
    ClassifiedFailure,
    FailureCase,
    classify,
    newest_timestamp,
)
from .llm_assisted import classify as llm_classify

__all__ = [
    "ClassificationContext",
    "ClassifiedFailure",
    "FailureCase",
    "classify",
    "llm_classify",
    "newest_timestamp",
]
