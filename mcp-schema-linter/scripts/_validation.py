"""Input and rubric validation for the MCP schema linter.

Pure functions, no third-party deps. Keeps CLI parsing hygiene and the
post-merge rubric sanity checks in one place.
"""

from __future__ import annotations

from typing import Any

# Sections we expect a well-formed rubric (builtin or merged) to expose.
# Optional rubric sections (e.g. ``near_synonym_pairs``) are not listed.
_REQUIRED_RUBRIC_KEYS = (
    "grade_cutoffs",
    "thresholds",
    "boilerplate_openers",
    "destructive_verbs",
    "read_only_verbs",
)


def validate_rubric(rubric: dict[str, Any]) -> None:
    """Lightweight sanity checks on the merged rubric.

    Raises ``ValueError`` with an actionable message if a required section
    is missing or has the wrong type. Field-level checks are intentionally
    light: most rubric keys are optional and rules fall back to defaults
    when a value is missing, so a strict schema would over-constrain
    legitimate user configs.
    """
    if not isinstance(rubric, dict):
        raise ValueError("Rubric must be a JSON object at the top level.")
    for key in _REQUIRED_RUBRIC_KEYS:
        if key not in rubric:
            raise ValueError(f"Rubric is missing required section: {key!r}")
    cutoffs = rubric.get("grade_cutoffs")
    if not isinstance(cutoffs, dict) or not cutoffs:
        raise ValueError("grade_cutoffs must be a non-empty object.")
    for grade, score in cutoffs.items():
        if not isinstance(score, int) or not 0 <= score <= 100:
            raise ValueError(f"grade_cutoffs[{grade!r}] must be an int in [0, 100], got {score!r}.")
    thresholds = rubric.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("thresholds must be an object.")


def validate_server_url(url: str) -> str:
    """Basic scheme check for ``--server-url``."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("--server-url cannot be empty.")
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("--server-url must start with http:// or https://")
    return url


def validate_expect_min_score(value: int) -> int:
    """Range check for ``--expect-min-score``."""
    if not isinstance(value, int):
        raise ValueError(f"--expect-min-score must be an integer, got {value!r}.")
    if not 0 <= value <= 100:
        raise ValueError(f"--expect-min-score must be in [0, 100], got {value}.")
    return value


def validate_expect_max_tokens(value: int) -> int:
    """Range check for ``--expect-max-tokens``."""
    if not isinstance(value, int):
        raise ValueError(f"--expect-max-tokens must be an integer, got {value!r}.")
    if value < 0:
        raise ValueError(f"--expect-max-tokens must be non-negative, got {value}.")
    return value
