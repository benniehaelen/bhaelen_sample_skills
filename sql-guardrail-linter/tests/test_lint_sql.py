"""Integration tests for the lint_sql.py CLI (static mode + gates + exit codes)."""

from __future__ import annotations

import json

import pytest

import lint_sql


def _run(args):
    return lint_sql.main(args)


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ----- happy path -----------------------------------------------------------


def test_single_sql_static(tmp_path):
    out = tmp_path / "r.json"
    rc = _run([
        "--sql", "SELECT a, b FROM proj.d.t WHERE x = 1 LIMIT 10",
        "--mode", "static",
        "--output-json", str(out), "--output-md", str(tmp_path / "r.md"),
    ])
    assert rc == 0
    report = _load(out)
    assert report["mode"] == "static"
    assert report["scope"]["source"] == "sql"
    assert len(report["statements"]) == 1


def test_html_output_written(tmp_path):
    out_html = tmp_path / "r.html"
    rc = _run([
        "--sql", "SELECT a FROM proj.d.t LIMIT 1", "--mode", "static",
        "--output-json", str(tmp_path / "r.json"), "--output-md", str(tmp_path / "r.md"),
        "--output-html", str(out_html),
    ])
    assert rc == 0
    assert out_html.exists()
    assert out_html.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


# ----- file batch -----------------------------------------------------------


def test_sql_file_batch(tmp_path):
    sql_file = tmp_path / "queries.sql"
    sql_file.write_text(
        "SELECT a FROM proj.d.t LIMIT 1;\nSELECT * FROM d.t2;\n", encoding="utf-8"
    )
    out = tmp_path / "r.json"
    rc = _run([
        "--sql-file", str(sql_file), "--mode", "static",
        "--output-json", str(out), "--output-md", str(tmp_path / "r.md"),
    ])
    assert rc == 0
    report = _load(out)
    assert report["scope"]["source"] == "sql_file"
    assert report["summary"]["statement_count"] == 2
    # Worst-first ordering: the SELECT * / two-part-table statement ranks first.
    assert report["statements"][0]["score"] <= report["statements"][1]["score"]


# ----- gates ----------------------------------------------------------------


def test_expect_min_score_fails(tmp_path):
    rc = _run([
        "--sql", "SELECT * FROM d.t ORDER BY a", "--mode", "static",
        "--output-json", str(tmp_path / "r.json"), "--output-md", str(tmp_path / "r.md"),
        "--expect-min-score", "95",
    ])
    assert rc == 3


def test_expect_no_violations_at_fails_on_write(tmp_path):
    rc = _run([
        "--sql", "DELETE FROM proj.d.t WHERE id = 1", "--mode", "static",
        "--output-json", str(tmp_path / "r.json"), "--output-md", str(tmp_path / "r.md"),
        "--expect-no-violations-at", "critical",
    ])
    assert rc == 3


def test_expect_no_violations_at_passes_clean(tmp_path):
    rc = _run([
        "--sql", "SELECT a FROM proj.d.t LIMIT 5", "--mode", "static",
        "--output-json", str(tmp_path / "r.json"), "--output-md", str(tmp_path / "r.md"),
        "--expect-no-violations-at", "high",
    ])
    assert rc == 0


def test_expect_max_bytes_requires_cost_aware(tmp_path):
    rc = _run([
        "--sql", "SELECT a FROM proj.d.t", "--mode", "static",
        "--output-json", str(tmp_path / "r.json"), "--output-md", str(tmp_path / "r.md"),
        "--expect-max-bytes", "1GB",
    ])
    assert rc == 2  # static mode cannot satisfy a byte gate


# ----- error handling -------------------------------------------------------


def test_parse_error_exits_2(tmp_path):
    out = tmp_path / "r.json"
    rc = _run([
        "--sql", "SELECT FROM WHERE )(", "--mode", "static",
        "--output-json", str(out), "--output-md", str(tmp_path / "r.md"),
    ])
    assert rc == 2
    # The report is still written so a reviewer can see the parse failure.
    assert out.exists()


def test_bad_rubric_config_exits_2(tmp_path):
    bad = tmp_path / "rubric.json"
    # A partial config is valid (sections fall back to builtin); this one is
    # invalid because the cutoffs are not strictly decreasing after the merge.
    bad.write_text(json.dumps({"grade_cutoffs": {"A": 50, "B": 90}}), encoding="utf-8")
    rc = _run([
        "--sql", "SELECT a FROM proj.d.t", "--mode", "static",
        "--rubric-config", str(bad),
        "--output-json", str(tmp_path / "r.json"), "--output-md", str(tmp_path / "r.md"),
    ])
    assert rc == 2


def test_tenant_registry_drives_sq009(tmp_path):
    registry = tmp_path / "tenants.json"
    registry.write_text(json.dumps({"proj.clinical.encounter": "coid"}), encoding="utf-8")
    out = tmp_path / "r.json"
    rc = _run([
        "--sql", "SELECT encounter_id FROM proj.clinical.encounter WHERE admit_date >= '2026-01-01'",
        "--mode", "static", "--tenant-registry", str(registry),
        "--output-json", str(out), "--output-md", str(tmp_path / "r.md"),
        "--expect-no-violations-at", "critical",
    ])
    # No coid predicate -> SQ-009 critical fail -> gate fails.
    assert rc == 3
    report = _load(out)
    sq009 = [c for c in report["statements"][0]["controls"] if c["id"] == "SQ-009"][0]
    assert sq009["status"] == "fail"


def test_rubric_config_provenance_stamped(tmp_path):
    cfg = tmp_path / "rubric.json"
    cfg.write_text(json.dumps({"controls": {"SQ-011": {"severity": "medium"}}}), encoding="utf-8")
    out = tmp_path / "r.json"
    rc = _run([
        "--sql", "SELECT a FROM d.t LIMIT 1", "--mode", "static",
        "--rubric-config", str(cfg),
        "--output-json", str(out), "--output-md", str(tmp_path / "r.md"),
    ])
    assert rc == 0
    report = _load(out)
    assert report["rubric_config"]["source"].endswith("rubric.json")
    assert report["rubric_config"]["sha256"]
    sq011 = [c for c in report["statements"][0]["controls"] if c["id"] == "SQ-011"][0]
    assert sq011["severity"] == "medium"
