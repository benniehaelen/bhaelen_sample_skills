"""Ground-truth loading and validation.

Ground truth is a CSV: one row per query, with required columns query and
relevant_doc_ids (semicolon-separated ids) and optional columns query_type,
notes, and expected_k. Leading comment lines (starting with #) before the
header are parsed into metadata; a synthetic marker there flags a generated
file so the report can label Tier 3 results as derived from synthetic ground
truth.

Parsing is strict and reports errors with the file line number so a malformed
row is easy to find. Uses the stdlib csv module for precise control over line
numbers and quoting.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUIRED_COLUMNS = ("query", "relevant_doc_ids")
OPTIONAL_COLUMNS = ("query_type", "notes", "expected_k")
_TRUE_VALUES = ("true", "yes", "1")


class GroundTruthError(ValueError):
    """Raised when a ground-truth CSV is missing or malformed."""


@dataclass
class GroundTruthQuery:
    """One labeled query and the documents that should retrieve for it."""

    query: str
    relevant_doc_ids: set[str]
    query_type: str | None = None
    notes: str | None = None
    expected_k: int | None = None


@dataclass
class GroundTruth:
    """A parsed ground-truth set."""

    queries: list[GroundTruthQuery]
    is_synthetic: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "GroundTruth":
        """Load ground truth from a CSV file.

        Raises:
            GroundTruthError: if the file is missing or fails validation.
        """
        file_path = Path(path)
        if not file_path.is_file():
            raise GroundTruthError(f"Ground-truth file not found: {file_path}")
        # utf-8-sig strips a byte-order mark if a spreadsheet tool added one.
        return cls.from_text(file_path.read_text(encoding="utf-8-sig"))

    @classmethod
    def from_text(cls, text: str) -> "GroundTruth":
        """Parse ground truth from CSV text."""
        lines = text.splitlines()
        comment_count = 0
        while comment_count < len(lines) and lines[comment_count].lstrip().startswith("#"):
            comment_count += 1
        metadata, is_synthetic = _parse_comments(lines[:comment_count])

        body = "\n".join(lines[comment_count:])
        if not body.strip():
            raise GroundTruthError("Ground-truth file has no header or data rows.")

        reader = csv.reader(io.StringIO(body))
        try:
            header = [h.strip() for h in next(reader)]
        except StopIteration:
            raise GroundTruthError("Ground-truth file has no header row.")

        column_index = {name: idx for idx, name in enumerate(header)}
        missing = [c for c in REQUIRED_COLUMNS if c not in column_index]
        if missing:
            raise GroundTruthError(
                f"Ground-truth file is missing required column(s): {', '.join(missing)}."
            )

        queries: list[GroundTruthQuery] = []
        errors: list[str] = []
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue  # skip blank lines
            line_no = comment_count + reader.line_num
            try:
                queries.append(_build_query(row, column_index, line_no))
            except GroundTruthError as exc:
                errors.append(str(exc))

        if errors:
            raise GroundTruthError("Ground-truth parse errors:\n  " + "\n  ".join(errors))
        if not queries:
            raise GroundTruthError("Ground-truth file has no usable query rows.")

        return cls(queries=queries, is_synthetic=is_synthetic, metadata=metadata)


def _parse_comments(comment_lines: list[str]) -> tuple[dict[str, Any], bool]:
    """Parse leading comment lines into a metadata dict and a synthetic flag."""
    metadata: dict[str, Any] = {}
    is_synthetic = False
    for line in comment_lines:
        text = line.lstrip()
        text = text[1:].strip() if text.startswith("#") else text.strip()
        if ":" not in text:
            continue
        key, _, value = text.partition(":")
        key = key.strip().lower()
        value = value.strip()
        metadata[key] = value
        if key == "synthetic" and value.lower() in _TRUE_VALUES:
            is_synthetic = True
    return metadata, is_synthetic


def _build_query(row: list[str], column_index: dict[str, int], line_no: int) -> GroundTruthQuery:
    def cell(name: str) -> str:
        idx = column_index.get(name)
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    query = cell("query")
    if not query:
        raise GroundTruthError(f"line {line_no}: 'query' is empty.")

    relevant = {part.strip() for part in cell("relevant_doc_ids").split(";") if part.strip()}
    if not relevant:
        raise GroundTruthError(f"line {line_no}: 'relevant_doc_ids' is empty (need at least one id).")

    expected_k: int | None = None
    raw_k = cell("expected_k")
    if raw_k:
        try:
            expected_k = int(raw_k)
        except ValueError:
            raise GroundTruthError(f"line {line_no}: 'expected_k' must be an integer, got {raw_k!r}.")
        if expected_k < 1:
            raise GroundTruthError(f"line {line_no}: 'expected_k' must be 1 or greater, got {expected_k}.")

    return GroundTruthQuery(
        query=query,
        relevant_doc_ids=relevant,
        query_type=cell("query_type") or None,
        notes=cell("notes") or None,
        expected_k=expected_k,
    )
