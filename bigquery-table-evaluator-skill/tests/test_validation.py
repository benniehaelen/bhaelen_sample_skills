from __future__ import annotations

import datetime as dt

import pytest


class TestParseDuration:
    """Coverage for the ``--expect-freshness-within`` duration parser."""

    def test_each_unit(self, validation_module):
        assert validation_module.parse_duration("30s") == dt.timedelta(seconds=30)
        assert validation_module.parse_duration("15m") == dt.timedelta(minutes=15)
        assert validation_module.parse_duration("24h") == dt.timedelta(hours=24)
        assert validation_module.parse_duration("7d") == dt.timedelta(days=7)

    def test_case_insensitive_and_whitespace(self, validation_module):
        assert validation_module.parse_duration("  12H ") == dt.timedelta(hours=12)

    @pytest.mark.parametrize("bad", ["", "10", "10x", "h10", "-5h", "10 days"])
    def test_rejects_garbage(self, validation_module, bad):
        with pytest.raises(ValueError):
            validation_module.parse_duration(bad)


class TestParseNullRateArg:
    """Coverage for the ``--expect-max-null-rate COL=RATE`` parser."""

    def test_basic(self, validation_module):
        assert validation_module.parse_null_rate_arg("user_id=0.05") == ("user_id", 0.05)

    def test_zero_and_one_allowed(self, validation_module):
        assert validation_module.parse_null_rate_arg("c=0")[1] == 0.0
        assert validation_module.parse_null_rate_arg("c=1")[1] == 1.0

    @pytest.mark.parametrize("bad", ["no_equals", "col=2", "col=-0.1", "bad-name=0.5", "=0.5", "col=abc"])
    def test_rejects_garbage(self, validation_module, bad):
        with pytest.raises(ValueError):
            validation_module.parse_null_rate_arg(bad)


class TestValidateColumn:
    """SQL-injection guard for column names interpolated into queries."""

    @pytest.mark.parametrize("good", ["a", "_x", "snake_case", "col1", "X__y"])
    def test_accepts_valid(self, validation_module, good):
        assert validation_module.validate_column(good) == good

    @pytest.mark.parametrize("bad", ["", "1col", "with space", "with-dash", "tick`name", "x;y"])
    def test_rejects_invalid(self, validation_module, bad):
        with pytest.raises(ValueError):
            validation_module.validate_column(bad)


class TestSplitTableId:
    """SQL-injection guard for project.dataset.table identifiers."""

    def test_well_formed(self, validation_module):
        assert validation_module.split_table_id("my-proj.dataset.table") == ("my-proj", "dataset", "table")

    @pytest.mark.parametrize("bad", ["a.b", "a.b.c.d", "weird;.b.c", "a.b.c d"])
    def test_rejects_invalid(self, validation_module, bad):
        with pytest.raises(ValueError):
            validation_module.split_table_id(bad)


class TestValidateWhereClause:
    """Best-effort guard for user-supplied ``--where`` SQL fragments."""

    def test_passes_through_safe(self, validation_module):
        clause = "event_date >= '2026-01-01' AND user_id IS NOT NULL"
        assert validation_module.validate_where_clause(clause) == clause

    def test_none_and_empty_become_none(self, validation_module):
        assert validation_module.validate_where_clause(None) is None
        assert validation_module.validate_where_clause("   ") is None

    @pytest.mark.parametrize(
        "bad",
        [
            "1=1; DROP TABLE x",
            "col = `injected`",
            "col = 1 -- comment",
            "col = 1 /* block */",
            "x */ y",
        ],
    )
    def test_blocks_dangerous_tokens(self, validation_module, bad):
        with pytest.raises(ValueError):
            validation_module.validate_where_clause(bad)

    def test_length_cap(self, validation_module):
        with pytest.raises(ValueError):
            validation_module.validate_where_clause("a" * 2001)
