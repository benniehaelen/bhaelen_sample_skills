"""Tests for _parser.py: statement typing, tables, joins, projections, splitting."""

from __future__ import annotations

import pytest

from _parser import parse_statement, split_statements


# ----- statement type -------------------------------------------------------


@pytest.mark.parametrize("sql,expected", [
    ("SELECT 1 FROM t", "SELECT"),
    ("WITH c AS (SELECT 1 AS x) SELECT x FROM c", "SELECT"),
    ("SELECT 1 UNION ALL SELECT 2", "SELECT"),
    ("INSERT INTO t (a) VALUES (1)", "INSERT"),
    ("UPDATE t SET a = 1 WHERE id = 2", "UPDATE"),
    ("DELETE FROM t WHERE id = 2", "DELETE"),
    ("DROP TABLE t", "DROP"),
    ("CREATE TABLE t (a INT64)", "CREATE"),
    ("TRUNCATE TABLE t", "TRUNCATE"),
])
def test_statement_type(sql, expected):
    assert parse_statement(sql).statement_type == expected


def test_read_only_flag():
    assert parse_statement("SELECT 1 FROM t").is_read_only_select
    assert not parse_statement("DROP TABLE t").is_read_only_select


def test_parse_error_is_captured_not_raised():
    stmt = parse_statement("SELECT FROM WHERE )(")
    assert not stmt.ok
    assert stmt.parse_error


# ----- statement count ------------------------------------------------------


def test_stacked_statements_counted():
    stmt = parse_statement("SELECT 1 FROM t; DROP TABLE x")
    assert stmt.statement_count == 2
    assert stmt.statement_type == "SELECT"  # first statement


# ----- tables ---------------------------------------------------------------


def test_table_qualification():
    stmt = parse_statement("SELECT a FROM proj.ds.tbl")
    assert len(stmt.tables) == 1
    assert stmt.tables[0].written == "proj.ds.tbl"
    assert stmt.tables[0].is_fully_qualified


def test_two_part_table_not_fully_qualified():
    stmt = parse_statement("SELECT a FROM ds.tbl")
    assert stmt.tables[0].written == "ds.tbl"
    assert not stmt.tables[0].is_fully_qualified


def test_cte_not_treated_as_base_table():
    sql = "WITH recent AS (SELECT id FROM proj.d.events) SELECT id FROM recent"
    stmt = parse_statement(sql)
    names = [t.written for t in stmt.tables]
    assert "proj.d.events" in names
    assert "recent" not in names


def test_references_helper_is_case_insensitive():
    stmt = parse_statement("SELECT a FROM Proj.DS.Tbl")
    assert stmt.references("proj.ds.tbl")


# ----- projections ----------------------------------------------------------


def test_select_star_detected():
    assert parse_statement("SELECT * FROM proj.d.t").select_star
    assert parse_statement("SELECT t.* FROM proj.d.t t").select_star


def test_count_star_is_not_select_star():
    stmt = parse_statement("SELECT COUNT(*) FROM proj.d.t")
    assert not stmt.select_star


def test_projected_columns():
    stmt = parse_statement("SELECT encounter_id, ssn FROM proj.d.t")
    assert stmt.projected_columns == {"encounter_id", "ssn"}


# ----- where / limit / order ------------------------------------------------


def test_where_columns_and_constrains():
    stmt = parse_statement("SELECT a FROM proj.d.t WHERE coid = 5 AND admit_date >= '2026-01-01'")
    assert stmt.has_where
    assert stmt.where_constrains("coid")
    assert stmt.where_constrains("admit_date")
    assert not stmt.where_constrains("other")


def test_limit_and_order():
    stmt = parse_statement("SELECT a FROM proj.d.t ORDER BY a LIMIT 10")
    assert stmt.has_limit
    assert stmt.has_order_by


# ----- aggregation ----------------------------------------------------------


@pytest.mark.parametrize("sql,is_agg", [
    ("SELECT COUNT(*) FROM proj.d.t", True),
    ("SELECT coid, COUNT(*) FROM proj.d.t GROUP BY coid", True),
    ("SELECT a, b FROM proj.d.t", False),
    ("SELECT MAX(x), a FROM proj.d.t GROUP BY a", True),
])
def test_is_aggregation(sql, is_agg):
    assert parse_statement(sql).is_aggregation == is_agg


# ----- joins ----------------------------------------------------------------


def test_inner_join_with_on_is_clean():
    sql = "SELECT a.x FROM proj.d.a a JOIN proj.d.b b ON a.id = b.id"
    stmt = parse_statement(sql)
    assert stmt.join_count == 1
    assert not stmt.cartesian_evidence


def test_cross_join_flagged():
    stmt = parse_statement("SELECT a.x FROM proj.d.a a CROSS JOIN proj.d.b b")
    assert "CROSS JOIN" in stmt.cartesian_evidence


def test_comma_join_without_predicate_flagged():
    stmt = parse_statement("SELECT a.x FROM proj.d.a a, proj.d.b b")
    assert stmt.cartesian_evidence


def test_comma_join_with_where_equality_tolerated():
    stmt = parse_statement("SELECT a.x FROM proj.d.a a, proj.d.b b WHERE a.id = b.id")
    assert not stmt.cartesian_evidence


# ----- splitting ------------------------------------------------------------


def test_split_statements_basic():
    text = "SELECT 1 FROM t;\nSELECT 2 FROM u;\n"
    assert split_statements(text) == ["SELECT 1 FROM t", "SELECT 2 FROM u"]


def test_split_ignores_semicolons_in_strings_and_comments():
    text = "SELECT ';' AS s FROM t; -- a; comment\nSELECT 2 FROM u"
    parts = split_statements(text)
    assert len(parts) == 2
    assert parts[0].startswith("SELECT ';'")


def test_split_drops_comment_only_segments():
    text = "SELECT 1 FROM t;\n-- trailing comment only\n"
    assert split_statements(text) == ["SELECT 1 FROM t"]
