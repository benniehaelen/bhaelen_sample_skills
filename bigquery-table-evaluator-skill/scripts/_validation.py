"""Input validation: SQL identifier hygiene and CLI argument parsers.

Pure functions, no third-party dependencies. Every helper that builds SQL or
parses user input belongs here so the SQL-injection invariants live in one
place.
"""

from __future__ import annotations

import datetime as dt
import re

_TABLE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_*$-]+$")
_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_FORBIDDEN_WHERE_TOKENS = ("`", ";", "--", "/*", "*/")


def split_table_id(table_id: str) -> tuple[str, str, str]:
    if not _TABLE_ID_RE.match(table_id):
        raise ValueError("Table must be in project.dataset.table form and contain only safe identifier characters.")
    project, dataset, table = table_id.split(".", 2)
    return project, dataset, table


def quote_table(table_id: str) -> str:
    project, dataset, table = split_table_id(table_id)
    return f"`{project}.{dataset}.{table}`"


def validate_column(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Column name cannot be empty.")
    if not _COLUMN_RE.match(name):
        raise ValueError(f"Unsafe or unsupported column name: {name!r}")
    return name


def quote_column(name: str) -> str:
    return f"`{validate_column(name)}`"


def csv_arg(value: str | None) -> list[str]:
    if not value:
        return []
    return [validate_column(part) for part in value.split(",") if part.strip()]


def parse_duration(value: str) -> dt.timedelta:
    match = _DURATION_RE.match(value)
    if not match:
        raise ValueError(f"Invalid duration {value!r}. Use forms like 30s, 15m, 24h, 7d.")
    return dt.timedelta(seconds=int(match.group(1)) * _DURATION_UNITS[match.group(2).lower()])


def parse_null_rate_arg(value: str) -> tuple[str, float]:
    if "=" not in value:
        raise ValueError(f"--expect-max-null-rate requires COL=RATE form, got {value!r}")
    col, rate = value.split("=", 1)
    col = validate_column(col)
    try:
        rate_f = float(rate)
    except ValueError as exc:
        raise ValueError(f"Invalid rate {rate!r} in --expect-max-null-rate") from exc
    if not 0.0 <= rate_f <= 1.0:
        raise ValueError(f"--expect-max-null-rate rate must be in [0, 1], got {rate_f}")
    return col, rate_f


def validate_where_clause(value: str | None) -> str | None:
    """Best-effort guard for user-supplied WHERE expressions.

    Blocks backticks (identifier escapes), semicolons (statement separators),
    and SQL comments. Does NOT parse the SQL — the caller is still responsible
    for the expression being valid.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > 2000:
        raise ValueError("--where is too long (>2000 characters).")
    for token in _FORBIDDEN_WHERE_TOKENS:
        if token in text:
            raise ValueError(f"--where must not contain {token!r}; identifiers, comments, and statement separators are blocked.")
    return text
