"""Tests for helpers that live in scripts/evaluate_bigquery_table.py itself
(the BigQuery-aware CLI), as opposed to the renderer/validation/expectations
modules which have their own test files.
"""

from __future__ import annotations


class TestWhereSuffix:
    def test_appends_when_present(self, script_module):
        assert script_module._where_suffix("x = 1") == "\nWHERE (x = 1)"

    def test_empty_when_absent(self, script_module):
        assert script_module._where_suffix(None) == ""
        assert script_module._where_suffix("") == ""
