"""Renderer tests: Markdown stable shape + HTML structural elements."""

from __future__ import annotations

import pytest


@pytest.fixture
def sample_report():
    return {
        "rubric_version": "1.0",
        "scored_at": "2026-05-04T12:00:00+00:00",
        "scope": {"dataset": "my-project.analytics"},
        "tables": [
            {
                "table_id": "my-project.analytics.events",
                "score": 88,
                "grade": "B",
                "table_metadata": {
                    "description": "Application events emitted by the patient portal.\nUsed for engagement analysis.",
                    "labels": {"owner": "engagement-team"},
                    "points": 14, "max": 16,
                    "criteria": [
                        {"name": "business_description", "points": 2, "max": 2, "passed": True, "evidence": "Encounter records..."},
                        {"name": "grain_statement", "points": 2, "max": 2, "passed": True, "evidence": "one row per..."},
                        {"name": "primary_keys", "points": 2, "max": 2, "passed": True, "evidence": "..."},
                        {"name": "join_guidance", "points": 2, "max": 2, "passed": True, "evidence": "..."},
                        {"name": "ownership", "points": 2, "max": 2, "passed": True, "evidence": "label `owner=...`"},
                        {"name": "sensitivity", "points": 2, "max": 2, "passed": True, "evidence": "PHI"},
                        {"name": "history_rule", "points": 1, "max": 2, "passed": False, "evidence": "Use latest_record_ind"},
                        {"name": "lineage", "points": 1, "max": 2, "passed": False, "evidence": ""},
                    ],
                },
                "column_metadata": {
                    "mean_normalized": 0.86, "column_count": 2,
                    "columns": [
                        {
                            "name": "encounter_id", "type": "STRING", "mode": "NULLABLE",
                            "description": "Stable encounter identifier.",
                            "points": 4, "max": 4,
                            "criteria": [
                                {"name": "has_description", "points": 2, "max": 2, "passed": True, "evidence": "..."},
                                {"name": "not_type_echo", "points": 2, "max": 2, "passed": True, "evidence": "..."},
                            ],
                        },
                        {
                            "name": "event_timestamp", "type": "TIMESTAMP", "mode": "NULLABLE",
                            "description": "When the event occurred.",
                            "points": 4, "max": 6,
                            "criteria": [
                                {"name": "has_description", "points": 2, "max": 2, "passed": True, "evidence": "..."},
                                {"name": "not_type_echo", "points": 2, "max": 2, "passed": True, "evidence": "..."},
                                {"name": "units_or_format", "points": 0, "max": 2, "passed": False, "evidence": ""},
                            ],
                        },
                    ],
                },
                "issues": [
                    "No current-state vs. history rule.",
                    "Source system / lineage not mentioned.",
                ],
            },
            {
                "table_id": "my-project.analytics.users",
                "score": 45,
                "grade": "F",
                "table_metadata": {
                    "points": 4, "max": 16,
                    "criteria": [
                        {"name": "business_description", "points": 1, "max": 2, "passed": False, "evidence": "Users table."},
                        {"name": "grain_statement", "points": 0, "max": 2, "passed": False, "evidence": ""},
                        {"name": "primary_keys", "points": 0, "max": 2, "passed": False, "evidence": ""},
                        {"name": "join_guidance", "points": 0, "max": 2, "passed": False, "evidence": ""},
                        {"name": "ownership", "points": 0, "max": 2, "passed": False, "evidence": ""},
                        {"name": "sensitivity", "points": 1, "max": 2, "passed": False, "evidence": ""},
                        {"name": "history_rule", "points": 1, "max": 2, "passed": False, "evidence": ""},
                        {"name": "lineage", "points": 1, "max": 2, "passed": False, "evidence": ""},
                    ],
                },
                "column_metadata": {
                    "mean_normalized": 0.50, "column_count": 1,
                    "columns": [
                        {
                            "name": "user_id", "type": "STRING", "mode": "NULLABLE",
                            "description": "id field",
                            "points": 2, "max": 4,
                            "criteria": [
                                {"name": "has_description", "points": 2, "max": 2, "passed": True, "evidence": "id field"},
                                {"name": "not_type_echo", "points": 0, "max": 2, "passed": False, "evidence": "id field"},
                            ],
                        },
                    ],
                },
                "issues": ["Column `user_id` description just echoes the type/name."],
            },
        ],
        "warnings": [],
    }


class TestMarkdown:
    def test_top_level_header(self, render_module, sample_report):
        md = render_module.make_markdown(sample_report)
        assert md.startswith("# Metadata Scorecard\n")

    def test_includes_scope(self, render_module, sample_report):
        md = render_module.make_markdown(sample_report)
        assert "my-project.analytics" in md

    def test_tables_sorted_worst_first(self, render_module, sample_report):
        md = render_module.make_markdown(sample_report)
        users_idx = md.index("my-project.analytics.users")
        events_idx = md.index("my-project.analytics.events")
        assert users_idx < events_idx

    def test_includes_score_and_grade(self, render_module, sample_report):
        md = render_module.make_markdown(sample_report)
        assert "88/100 (B)" in md
        assert "45/100 (F)" in md

    def test_criterion_status_labels(self, render_module, sample_report):
        md = render_module.make_markdown(sample_report)
        assert "[pass]" in md and "[fail]" in md and "[partial]" in md

    def test_empty_report_does_not_crash(self, render_module):
        md = render_module.make_markdown({
            "rubric_version": "1.0", "scope": {"tables": []}, "tables": [],
        })
        assert md.startswith("# Metadata Scorecard\n")

    def test_table_description_rendered_as_blockquote(self, render_module, sample_report):
        md = render_module.make_markdown(sample_report)
        # The events table description has two lines
        assert "> Application events emitted by the patient portal." in md
        assert "> Used for engagement analysis." in md

    def test_missing_table_description_marked_as_none(self, render_module):
        md = render_module.make_markdown({
            "rubric_version": "1.0", "scope": {"tables": ["p.d.t"]},
            "tables": [{
                "table_id": "p.d.t", "score": 50, "grade": "F",
                "table_metadata": {"description": None, "labels": {}, "points": 0, "max": 16, "criteria": []},
                "column_metadata": {"mean_normalized": 0.0, "column_count": 0, "columns": []},
                "issues": [],
            }],
        })
        assert "_(none)_" in md

    def test_table_labels_rendered(self, render_module, sample_report):
        md = render_module.make_markdown(sample_report)
        assert "`owner=engagement-team`" in md

    def test_column_table_includes_description_column(self, render_module, sample_report):
        md = render_module.make_markdown(sample_report)
        assert "| Column | Type | Score | Description | Failing criteria |" in md
        assert "Stable encounter identifier." in md

    def test_pipe_in_evidence_is_escaped(self, render_module):
        md = render_module.make_markdown({
            "rubric_version": "1.0",
            "scope": {"tables": ["p.d.t"]},
            "tables": [{
                "table_id": "p.d.t", "score": 50, "grade": "F",
                "table_metadata": {"points": 0, "max": 16, "criteria": [
                    {"name": "x", "points": 0, "max": 2, "passed": False,
                     "evidence": "a | b | c"},
                ]},
                "column_metadata": {"mean_normalized": 0.0, "column_count": 0, "columns": []},
                "issues": [],
            }],
        })
        # Verify the pipe is escaped (so it doesn't break the markdown table)
        assert "a \\| b \\| c" in md


class TestHtml:
    def test_self_contained(self, render_module, sample_report):
        h = render_module.make_html(sample_report)
        assert "<style>" in h and "</style>" in h
        assert "<script" not in h
        assert "<link" not in h

    def test_doctype_present(self, render_module, sample_report):
        h = render_module.make_html(sample_report)
        assert h.startswith("<!DOCTYPE html>")

    def test_score_and_grade_rendered(self, render_module, sample_report):
        h = render_module.make_html(sample_report)
        assert ">88<" in h
        assert "grade-B" in h
        assert "grade-F" in h

    def test_pill_status_classes_present(self, render_module, sample_report):
        h = render_module.make_html(sample_report)
        assert "pill pass" in h
        assert "pill fail" in h
        assert "pill partial" in h

    def test_html_escapes_table_id(self, render_module):
        h = render_module.make_html({
            "rubric_version": "1.0",
            "scope": {"tables": ["a&b.c.d<e>"]},
            "tables": [{
                "table_id": "a&b.c.d<e>", "score": 10, "grade": "F",
                "table_metadata": {"points": 0, "max": 16, "criteria": []},
                "column_metadata": {"mean_normalized": 0.0, "column_count": 0, "columns": []},
                "issues": [],
            }],
        })
        assert "a&amp;b.c.d&lt;e&gt;" in h

    def test_theme_auto_includes_both_palettes(self, render_module, sample_report):
        h = render_module.make_html(sample_report, theme="auto")
        assert "prefers-color-scheme: dark" in h

    def test_theme_light_excludes_dark_palette(self, render_module, sample_report):
        h = render_module.make_html(sample_report, theme="light")
        assert "prefers-color-scheme: dark" not in h

    def test_invalid_theme_raises(self, render_module, sample_report):
        with pytest.raises(ValueError):
            render_module.make_html(sample_report, theme="solarized")

    def test_table_description_panel_present(self, render_module, sample_report):
        h = render_module.make_html(sample_report)
        assert 'class="desc-panel"' in h
        assert "Application events emitted by the patient portal." in h
        # Open by default
        assert '<details class="desc-panel" open>' in h

    def test_table_label_chips_rendered(self, render_module, sample_report):
        h = render_module.make_html(sample_report)
        assert 'class="label-chip"' in h
        assert "engagement-team" in h

    def test_column_description_in_collapsible(self, render_module, sample_report):
        h = render_module.make_html(sample_report)
        assert 'class="col-desc"' in h
        assert "Stable encounter identifier." in h

    def test_missing_description_marked_empty(self, render_module):
        h = render_module.make_html({
            "rubric_version": "1.0", "scope": {"tables": ["p.d.t"]},
            "tables": [{
                "table_id": "p.d.t", "score": 50, "grade": "F",
                "table_metadata": {"description": None, "labels": {}, "points": 0, "max": 16, "criteria": []},
                "column_metadata": {"mean_normalized": 0.0, "column_count": 1, "columns": [{
                    "name": "x", "type": "STRING", "mode": "NULLABLE", "description": None,
                    "points": 0, "max": 4, "criteria": [],
                }]},
                "issues": [],
            }],
        })
        assert 'desc-body empty' in h
        assert 'col-desc empty' in h
        assert "(no description)" in h
