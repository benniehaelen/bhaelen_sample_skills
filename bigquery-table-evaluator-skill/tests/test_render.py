from __future__ import annotations

import pytest


class TestSeverityForRate:
    """Null-rate severity-bucket boundaries (zero / ok / warn / bad)."""

    @pytest.mark.parametrize(
        "rate, expected",
        [
            (0, "zero"),
            (0.0, "zero"),
            (0.005, "ok"),
            (0.01, "ok"),
            (0.0101, "warn"),
            (0.10, "warn"),
            (0.1001, "bad"),
            (0.5, "bad"),
            (1.0, "bad"),
        ],
    )
    def test_severity_buckets(self, render_module, rate, expected):
        assert render_module._severity_for_rate(rate) == expected


class TestMakeHtml:
    """Structural coverage for the HTML evaluator dashboard: self-containment, escaping, theming."""

    def _full_report(self):
        return {
            "metadata": {
                "table_id": "p.d.t",
                "num_rows": 12345,
                "num_bytes_human": "1.20 MiB",
                "schema_field_count": 3,
                "table_type": "TABLE",
                "modified": "2026-05-03T12:00:00+00:00",
                "schema": [
                    {"name": "id", "type": "INT64", "mode": "REQUIRED", "description": "row id"},
                    {"name": "name", "type": "STRING", "mode": "NULLABLE", "description": ""},
                    {"name": "ts", "type": "TIMESTAMP", "mode": "NULLABLE", "description": "event time"},
                ],
            },
            "checks": {
                "freshness": {"status": "complete", "rows": [{"max_value": "2026-05-03T11:30:00+00:00"}]},
                "column_profile": {
                    "status": "complete",
                    "rows": [{"scanned_rows": 1000, "id__null_count": 0, "name__null_count": 100, "ts__null_count": 50}],
                    "profiled_columns": ["id", "name", "ts"],
                },
                "partitions": {
                    "status": "complete",
                    "rows": [{"partition_count": 30, "total_logical_bytes": 1234567, "max_partition_bytes": 200000}],
                },
            },
            "expectations": [
                {"name": "min_rows", "status": "passed", "expected_min": 1000, "actual": 12345},
                {"name": "freshness_within", "status": "failed", "max_age": "1h", "age_seconds": 7200, "max_value": "2026-05-03T11:30:00+00:00"},
                {"name": "max_null_rate", "column": "name", "status": "failed", "actual_null_rate": 0.10, "max_null_rate": 0.05},
                {"name": "no_schema_drift", "status": "skipped_no_data", "reason": "--baseline not provided"},
            ],
            "warnings": ["Sample warning &<>\""],
            "schema_drift": {"baseline_path": "old.json", "added": ["new_col"], "removed": [], "changed": []},
            "sample": {"rows": [{"id": 1, "name": "alice"}]},
        }

    def test_emits_doctype_and_closes(self, render_module):
        out = render_module.make_html(self._full_report())
        assert out.startswith("<!doctype html>")
        assert out.rstrip().endswith("</html>")

    def test_includes_table_id_and_metric_values(self, render_module):
        out = render_module.make_html(self._full_report())
        assert "p.d.t" in out
        assert "12,345" in out
        assert "1.20 MiB" in out

    def test_modified_metric_uses_human_format(self, render_module):
        out = render_module.make_html(self._full_report())
        # absolute portion is deterministic; the relative portion depends on "now"
        assert "May 3, 2026 11:30 UTC" in out
        # raw ISO must not appear inside a metric value cell (it may still appear inside pill title=... tooltips)
        assert '<div class="value">2026-05-03T11:30:00+00:00</div>' not in out

    def test_freshness_metric_uses_human_format(self, render_module):
        out = render_module.make_html(self._full_report())
        assert "May 3, 2026 11:30 UTC" in out  # the fixture's freshness max_value

    def test_renders_pills_with_status_classes(self, render_module):
        out = render_module.make_html(self._full_report())
        assert 'class="pill passed"' in out
        assert 'class="pill failed"' in out
        assert 'class="pill skipped_no_data"' in out

    def test_includes_null_rate_chart_only_when_data(self, render_module):
        out = render_module.make_html(self._full_report())
        assert "<svg" in out and "Null rates by column" in out
        # report without column_profile -> no chart
        empty = {"metadata": {"table_id": "p.d.t", "schema": []}, "checks": {}}
        out2 = render_module.make_html(empty)
        assert "<svg" not in out2

    def test_null_rate_chart_uses_severity_gradients(self, render_module):
        report = self._full_report()
        report["checks"]["column_profile"]["rows"] = [
            {
                "scanned_rows": 1000,
                "clean__null_count": 0,        # zero severity
                "low__null_count": 5,          # ok (0.5%)
                "moderate__null_count": 50,    # warn (5%)
                "broken__null_count": 500,     # bad (50%)
            }
        ]
        out = render_module.make_html(report)
        # gradient defs and per-bar fills cover all four severities
        assert 'id="nrg-zero"' in out and 'id="nrg-ok"' in out and 'id="nrg-warn"' in out and 'id="nrg-bad"' in out
        assert 'fill="url(#nrg-bad)"' in out
        assert 'fill="url(#nrg-warn)"' in out
        assert 'fill="url(#nrg-ok)"' in out
        assert 'fill="url(#nrg-zero)"' in out
        # gridlines at standard percentages and a chart legend
        for pct in (25, 50, 75, 100):
            assert f">{pct}%</text>" in out
        assert 'class="chart-legend"' in out
        # absolute count appears alongside percentage
        assert "500 (50.00%)" in out

    def test_distinct_count_chart_renders_when_data_present(self, render_module):
        report = self._full_report()
        # Add APPROX_COUNT_DISTINCT entries to the column profile
        report["checks"]["column_profile"]["rows"][0].update({
            "id__approx_distinct": 1_234_567,
            "name__approx_distinct": 12_000,
            "ts__approx_distinct": 5,
        })
        out = render_module.make_html(report)
        assert "Distinct values by column" in out
        # short-int rendering: 1.2M, 12K, 5
        assert ">1.2M<" in out and ">12K<" in out and ">5<" in out
        assert 'id="dcg-ok"' in out

    def test_distinct_count_chart_omitted_without_data(self, render_module):
        report = self._full_report()
        # No __approx_distinct keys
        out = render_module.make_html(report)
        assert "Distinct values by column" not in out

    def test_escapes_user_strings(self, render_module):
        out = render_module.make_html(self._full_report())
        # the warning contained &<>" — must appear escaped, not raw
        assert "&amp;&lt;&gt;&quot;" in out
        assert "Sample warning &<>\"" not in out

    def test_metadata_only_report_renders(self, render_module):
        report = {"metadata": {"table_id": "p.d.t", "num_rows": 0, "schema": [], "schema_field_count": 0}}
        out = render_module.make_html(report)
        assert out.startswith("<!doctype html>")
        assert "<svg" not in out  # no profile = no chart
        assert "Expectations" not in out  # none provided

    def test_theme_auto_emits_both_palettes_and_media_query(self, render_module):
        out = render_module.make_html(self._full_report(), theme="auto")
        assert "@media (prefers-color-scheme: dark)" in out
        # both palettes' anchor colors present
        assert "#ffffff" in out and "#0f172a" in out
        assert 'name="color-scheme" content="light dark"' in out

    def test_theme_light_omits_dark_block(self, render_module):
        out = render_module.make_html(self._full_report(), theme="light")
        assert "@media (prefers-color-scheme: dark)" not in out
        assert "#ffffff" in out
        # dark-only token value should not appear
        assert "#020617" not in out
        assert 'name="color-scheme" content="light"' in out

    def test_theme_dark_omits_light_block(self, render_module):
        out = render_module.make_html(self._full_report(), theme="dark")
        assert "@media (prefers-color-scheme: dark)" not in out
        assert "#0f172a" in out
        # light-only token value should not appear
        assert "#f9fafb" not in out
        assert 'name="color-scheme" content="dark"' in out

    def test_theme_unknown_raises(self, render_module):
        with pytest.raises(ValueError):
            render_module.make_html(self._full_report(), theme="solarized")

    def test_no_javascript_in_any_theme(self, render_module):
        for theme in ("auto", "light", "dark"):
            out = render_module.make_html(self._full_report(), theme=theme)
            assert "<script" not in out.lower()
            assert "javascript:" not in out.lower()
            assert " onclick=" not in out.lower()

    def test_drift_section_renders_changes(self, render_module):
        report = self._full_report()
        report["schema_drift"] = {
            "baseline_path": "old.json",
            "added": ["new_col"],
            "removed": ["gone_col"],
            "changed": [{"name": "id", "type": {"baseline": "INT64", "current": "STRING"}}],
        }
        out = render_module.make_html(report)
        assert "new_col" in out and "gone_col" in out and "INT64" in out and "STRING" in out
