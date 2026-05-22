"""Unit tests for the ground-truth loader.

Covers a valid load (the shipped template and inline text), id parsing, optional
fields, synthetic-marker detection from comment lines, and the validation errors
(missing column, empty query, empty ids, bad expected_k) with line numbers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ground_truth import GroundTruth, GroundTruthError, GroundTruthQuery

_TEMPLATE = Path(__file__).resolve().parent.parent / "examples" / "ground_truth_template.csv"

_VALID = (
    "query,relevant_doc_ids,query_type,notes,expected_k\n"
    '"What is the readmission rate?",doc_1;doc_2,metric,note one,5\n'
    '"How are discharges handled?",doc_3,rule,,\n'
)


# ----- valid loads ---------------------------------------------------------


def test_load_shipped_template():
    gt = GroundTruth.load(_TEMPLATE)
    assert len(gt.queries) == 15
    assert gt.is_synthetic is False
    first = gt.queries[0]
    assert isinstance(first, GroundTruthQuery)
    assert first.relevant_doc_ids == {"doc_142", "doc_891"}
    assert first.query_type == "metric_definition"
    assert first.expected_k is None  # template has no expected_k column


def test_from_text_valid():
    gt = GroundTruth.from_text(_VALID)
    assert len(gt.queries) == 2
    assert gt.queries[0].relevant_doc_ids == {"doc_1", "doc_2"}
    assert gt.queries[0].expected_k == 5
    assert gt.queries[0].notes == "note one"
    # Second row: optional fields blank -> None.
    assert gt.queries[1].query_type == "rule"
    assert gt.queries[1].notes is None
    assert gt.queries[1].expected_k is None


def test_relevant_ids_dedup_and_trim():
    gt = GroundTruth.from_text("query,relevant_doc_ids\nq,doc_1 ; doc_2;doc_1;\n")
    assert gt.queries[0].relevant_doc_ids == {"doc_1", "doc_2"}


def test_blank_lines_skipped():
    text = "query,relevant_doc_ids\nq1,doc_1\n\n   \nq2,doc_2\n"
    gt = GroundTruth.from_text(text)
    assert len(gt.queries) == 2


# ----- synthetic marker and metadata ---------------------------------------


def test_synthetic_marker_detected():
    text = (
        "# synthetic: true\n"
        "# generated_by: vector-store-linter\n"
        "# queries_per_doc: 2\n"
        "query,relevant_doc_ids\nq,doc_1\n"
    )
    gt = GroundTruth.from_text(text)
    assert gt.is_synthetic is True
    assert gt.metadata["generated_by"] == "vector-store-linter"
    assert gt.metadata["queries_per_doc"] == "2"


def test_no_marker_is_not_synthetic():
    gt = GroundTruth.from_text(_VALID)
    assert gt.is_synthetic is False
    assert gt.metadata == {}


# ----- validation errors ---------------------------------------------------


def test_missing_required_column():
    with pytest.raises(GroundTruthError, match="missing required column.*relevant_doc_ids"):
        GroundTruth.from_text("query,notes\nq,note\n")


def test_empty_query_reports_line():
    text = "query,relevant_doc_ids\n,doc_1\n"
    with pytest.raises(GroundTruthError, match=r"line 2: 'query' is empty"):
        GroundTruth.from_text(text)


def test_empty_relevant_ids_reports_line():
    text = "query,relevant_doc_ids\nq1,doc_1\nq2,\n"
    with pytest.raises(GroundTruthError, match=r"line 3: 'relevant_doc_ids' is empty"):
        GroundTruth.from_text(text)


def test_bad_expected_k_reports_line():
    text = "query,relevant_doc_ids,expected_k\nq,doc_1,notanumber\n"
    with pytest.raises(GroundTruthError, match=r"line 2: 'expected_k' must be an integer"):
        GroundTruth.from_text(text)


def test_zero_expected_k_rejected():
    text = "query,relevant_doc_ids,expected_k\nq,doc_1,0\n"
    with pytest.raises(GroundTruthError, match=r"'expected_k' must be 1 or greater"):
        GroundTruth.from_text(text)


def test_line_numbers_account_for_comments():
    # Two comment lines push the offending row to file line 4.
    text = "# synthetic: true\n# note: x\nquery,relevant_doc_ids\n,doc_1\n"
    with pytest.raises(GroundTruthError, match=r"line 4: 'query' is empty"):
        GroundTruth.from_text(text)


def test_no_rows():
    with pytest.raises(GroundTruthError, match="no usable query rows"):
        GroundTruth.from_text("query,relevant_doc_ids\n")


def test_empty_file():
    with pytest.raises(GroundTruthError, match="no header or data rows"):
        GroundTruth.from_text("   \n")


def test_load_missing_file(tmp_path):
    with pytest.raises(GroundTruthError, match="not found"):
        GroundTruth.load(tmp_path / "nope.csv")
