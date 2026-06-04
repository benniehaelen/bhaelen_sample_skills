"""Input and rubric validation for the SQL guardrail linter.

Pure functions, no third-party deps. Keeps identifier hygiene, human-byte
parsing, duration parsing, and the post-merge rubric sanity checks in one
place. Every helper that interpolates a user-supplied identifier into SQL
belongs here so the injection invariants live in one location.
"""

from __future__ import annotations

import re
from typing import Any

# Per operating rule 2 in SKILL.md. A fully qualified BigQuery table id is
# project.dataset.table; the project allows hyphens, the dataset is plain
# identifier characters, and the table may carry wildcard / suffix markers.
_PROJECT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_DATASET_RE = re.compile(r"^[A-Za-z0-9_]+$")
_TABLE_RE = re.compile(r"^[A-Za-z0-9_*$-]+$")
_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_DURATION_UNITS = {"s": ("SECOND", 1), "m": ("MINUTE", 60), "h": ("HOUR", 3600), "d": ("DAY", 86400)}

_BYTES_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGTP]?I?B?)\s*$", re.IGNORECASE)
_BYTE_UNITS = {
    "": 1, "B": 1,
    "KB": 1000, "KIB": 1024, "K": 1024,
    "MB": 1000 ** 2, "MIB": 1024 ** 2, "M": 1024 ** 2,
    "GB": 1000 ** 3, "GIB": 1024 ** 3, "G": 1024 ** 3,
    "TB": 1000 ** 4, "TIB": 1024 ** 4, "T": 1024 ** 4,
    "PB": 1000 ** 5, "PIB": 1024 ** 5, "P": 1024 ** 5,
}

_SEVERITIES = ("critical", "high", "medium", "low")
_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}

_REQUIRED_RUBRIC_KEYS = (
    "severity_weights",
    "grade_cutoffs",
    "controls",
)


# ----- identifier hygiene ---------------------------------------------------


def split_table_id(table_id: str) -> tuple[str, str, str]:
    """Validate and split ``project.dataset.table`` into its three parts.

    Raises ``ValueError`` for any input that does not match the strict
    component regexes. Callers are then safe to interpolate the parts into
    SQL when wrapped in backticks (see ``quote_table``).
    """
    if not isinstance(table_id, str):
        raise ValueError("Table id must be a string.")
    parts = table_id.strip().split(".")
    if len(parts) != 3:
        raise ValueError(f"Table must be in project.dataset.table form, got {table_id!r}.")
    project, dataset, table = parts
    if not _PROJECT_RE.match(project):
        raise ValueError(f"Unsafe project identifier: {project!r}")
    if not _DATASET_RE.match(dataset):
        raise ValueError(f"Unsafe dataset identifier: {dataset!r}")
    if not _TABLE_RE.match(table):
        raise ValueError(f"Unsafe table identifier: {table!r}")
    return project, dataset, table


def quote_table(table_id: str) -> str:
    """Backtick-quoted table reference safe to embed in SQL."""
    project, dataset, table = split_table_id(table_id)
    return f"`{project}.{dataset}.{table}`"


def validate_column(name: str) -> str:
    """Trim and validate a column name; return it unchanged."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Column name cannot be empty.")
    if not _COLUMN_RE.match(name):
        raise ValueError(f"Unsafe or unsupported column name: {name!r}")
    return name


def quote_column(name: str) -> str:
    """Backtick-quoted column reference safe to embed in SQL."""
    return f"`{validate_column(name)}`"


# ----- human value parsing --------------------------------------------------


def parse_bytes(value: Any) -> int:
    """Parse a byte count as an int or a human value like ``50GB`` / ``10 GiB``.

    Decimal suffixes (KB/MB/GB) use powers of 1000; binary suffixes (KiB/
    MiB/GiB) and the bare K/M/G shorthands use powers of 1024. A plain
    integer passes through. Raises ``ValueError`` for anything else.
    """
    if isinstance(value, bool):
        raise ValueError("Byte ceiling cannot be a boolean.")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("Byte ceiling must be non-negative.")
        return value
    if isinstance(value, float):
        if value < 0:
            raise ValueError("Byte ceiling must be non-negative.")
        return int(value)
    text = str(value).strip()
    match = _BYTES_RE.match(text)
    if not match:
        raise ValueError(f"Invalid byte value {value!r}. Use forms like 1048576, 10GB, 50GiB.")
    number, unit = match.group(1), match.group(2).upper()
    if unit not in _BYTE_UNITS:
        raise ValueError(f"Unknown byte unit in {value!r}.")
    return int(float(number) * _BYTE_UNITS[unit])


def parse_duration(value: str) -> tuple[int, str, int]:
    """Parse a window like ``7d`` / ``30m`` into ``(n, bigquery_unit, seconds)``.

    Returns the integer count, the BigQuery ``INTERVAL`` unit keyword
    (``SECOND`` / ``MINUTE`` / ``HOUR`` / ``DAY``), and the equivalent
    second count. Raises ``ValueError`` for anything that does not match.
    """
    match = _DURATION_RE.match(value or "")
    if not match:
        raise ValueError(f"Invalid duration {value!r}. Use forms like 30s, 15m, 24h, 7d.")
    n = int(match.group(1))
    unit_kw, mult = _DURATION_UNITS[match.group(2).lower()]
    return n, unit_kw, n * mult


# ----- expectation argument validation --------------------------------------


def validate_expect_min_score(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"--expect-min-score must be an integer, got {value!r}.")
    if not 0 <= value <= 100:
        raise ValueError(f"--expect-min-score must be in [0, 100], got {value}.")
    return value


def validate_severity(value: str) -> str:
    """Validate a severity name for ``--expect-no-violations-at``."""
    v = (value or "").strip().lower()
    if v not in _SEVERITIES:
        raise ValueError(f"Severity must be one of {', '.join(_SEVERITIES)}, got {value!r}.")
    return v


def severity_rank(severity: str) -> int:
    """Numeric ordering for severities (critical highest). Unknown -> 0."""
    return _SEVERITY_RANK.get((severity or "").lower(), 0)


# ----- rubric validation ----------------------------------------------------


def validate_rubric(rubric: dict[str, Any]) -> None:
    """Lightweight sanity checks on the merged rubric.

    Raises ``ValueError`` with an actionable message if a required section
    is missing or malformed. Most keys are optional and controls fall back
    to defaults, so the checks stay deliberately light.
    """
    if not isinstance(rubric, dict):
        raise ValueError("Rubric must be a JSON object at the top level.")
    for key in _REQUIRED_RUBRIC_KEYS:
        if key not in rubric:
            raise ValueError(f"Rubric is missing required section: {key!r}")

    weights = rubric.get("severity_weights")
    if not isinstance(weights, dict) or not weights:
        raise ValueError("severity_weights must be a non-empty object.")
    for sev in _SEVERITIES:
        if sev not in weights:
            raise ValueError(f"severity_weights is missing {sev!r}.")
        w = weights[sev]
        if not isinstance(w, (int, float)) or isinstance(w, bool) or w < 0:
            raise ValueError(f"severity_weights[{sev!r}] must be a non-negative number, got {w!r}.")

    cutoffs = rubric.get("grade_cutoffs")
    if not isinstance(cutoffs, dict) or not cutoffs:
        raise ValueError("grade_cutoffs must be a non-empty object.")
    last = 101
    for grade in ("A", "B", "C", "D"):
        if grade not in cutoffs:
            continue
        score = cutoffs[grade]
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
            raise ValueError(f"grade_cutoffs[{grade!r}] must be an int in [0, 100], got {score!r}.")
        if score >= last:
            raise ValueError("grade_cutoffs must be strictly decreasing from A to D.")
        last = score

    controls = rubric.get("controls")
    if not isinstance(controls, dict) or not controls:
        raise ValueError("controls must be a non-empty object.")
    for cid, meta in controls.items():
        if not isinstance(meta, dict):
            raise ValueError(f"controls[{cid!r}] must be an object.")
        sev = meta.get("severity")
        if sev is not None and (sev not in _SEVERITIES):
            raise ValueError(f"controls[{cid!r}].severity must be one of {', '.join(_SEVERITIES)}.")

    ceiling = rubric.get("max_bytes_ceiling")
    if ceiling is not None:
        parse_bytes(ceiling)  # raises ValueError if malformed
