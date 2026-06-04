"""Tests for _validation.py: identifier hygiene, byte/duration parsing, rubric checks."""

from __future__ import annotations

import pytest

import _validation as v


# ----- identifiers ----------------------------------------------------------


def test_split_table_id_valid():
    assert v.split_table_id("my-proj.dataset.table") == ("my-proj", "dataset", "table")


def test_split_table_id_wildcard_table():
    assert v.split_table_id("p.d.events_*") == ("p", "d", "events_*")


@pytest.mark.parametrize("bad", [
    "dataset.table",            # two-part
    "proj.dataset.tbl.extra",   # four-part
    "proj.data set.tbl",        # space in dataset
    "proj.data;set.tbl",        # injection char
    "`proj`.d.t",               # backtick
])
def test_split_table_id_rejects(bad):
    with pytest.raises(ValueError):
        v.split_table_id(bad)


def test_quote_table_and_column():
    assert v.quote_table("p.d.t") == "`p.d.t`"
    assert v.quote_column("coid") == "`coid`"


@pytest.mark.parametrize("bad", ["", "1col", "co id", "co;id", "co`id"])
def test_validate_column_rejects(bad):
    with pytest.raises(ValueError):
        v.validate_column(bad)


# ----- byte parsing ---------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    (1048576, 1048576),
    ("1048576", 1048576),
    ("10GB", 10 * 1000 ** 3),
    ("10GiB", 10 * 1024 ** 3),
    ("50GB", 50 * 1000 ** 3),
    ("1.5G", int(1.5 * 1024 ** 3)),
    ("512", 512),
])
def test_parse_bytes(value, expected):
    assert v.parse_bytes(value) == expected


@pytest.mark.parametrize("bad", ["abc", "10 elephants", "-5", True])
def test_parse_bytes_rejects(bad):
    with pytest.raises(ValueError):
        v.parse_bytes(bad)


# ----- duration parsing -----------------------------------------------------


@pytest.mark.parametrize("value,n,unit,secs", [
    ("7d", 7, "DAY", 7 * 86400),
    ("30m", 30, "MINUTE", 30 * 60),
    ("24h", 24, "HOUR", 24 * 3600),
    ("90s", 90, "SECOND", 90),
])
def test_parse_duration(value, n, unit, secs):
    assert v.parse_duration(value) == (n, unit, secs)


@pytest.mark.parametrize("bad", ["7", "7w", "abc", ""])
def test_parse_duration_rejects(bad):
    with pytest.raises(ValueError):
        v.parse_duration(bad)


# ----- severity helpers -----------------------------------------------------


def test_validate_severity():
    assert v.validate_severity("HIGH") == "high"
    with pytest.raises(ValueError):
        v.validate_severity("blocker")


def test_severity_rank_order():
    assert v.severity_rank("critical") > v.severity_rank("high") > v.severity_rank("medium") > v.severity_rank("low")
    assert v.severity_rank("nonsense") == 0


def test_validate_expect_min_score():
    assert v.validate_expect_min_score(70) == 70
    for bad in (-1, 101):
        with pytest.raises(ValueError):
            v.validate_expect_min_score(bad)


# ----- rubric validation ----------------------------------------------------


def test_validate_rubric_ok(rubric):
    v.validate_rubric(rubric)  # should not raise


def test_validate_rubric_missing_section(rubric):
    del rubric["controls"]
    with pytest.raises(ValueError):
        v.validate_rubric(rubric)


def test_validate_rubric_bad_cutoffs_order(rubric):
    rubric["grade_cutoffs"] = {"A": 80, "B": 90}  # not strictly decreasing
    with pytest.raises(ValueError):
        v.validate_rubric(rubric)


def test_validate_rubric_bad_severity_weight(rubric):
    rubric["severity_weights"]["critical"] = -1
    with pytest.raises(ValueError):
        v.validate_rubric(rubric)


def test_validate_rubric_bad_control_severity(rubric):
    rubric["controls"]["SQ-001"]["severity"] = "blocker"
    with pytest.raises(ValueError):
        v.validate_rubric(rubric)
