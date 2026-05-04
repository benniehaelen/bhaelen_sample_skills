#!/usr/bin/env python3
"""Render the three sample dashboards (auto/light/dark) for visual reference.

Run from the skill root:

    python examples/render_sample_dashboard.py

The renderer modules in ``scripts/`` have no BigQuery dependency, so this
script does not need GCP credentials or the ``google-cloud-bigquery`` library.
The HTML output is illustrative — re-run this whenever you change the renderer
in ``scripts/_render.py``.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from _render import make_html  # noqa: E402  (sys.path edited just above)


def _build_sample_report() -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    fresh = (now - dt.timedelta(minutes=45)).isoformat()
    modified = (now - dt.timedelta(hours=3, minutes=20)).isoformat()
    created = (now - dt.timedelta(days=180)).isoformat()
    return {
        "metadata": {
            "table_id": "my-project.analytics.events",
            "num_rows": 1234567,
            "num_bytes_human": "852.13 MiB",
            "schema_field_count": 6,
            "table_type": "TABLE",
            "modified": modified,
            "created": created,
            "schema": [
                {"name": "event_id", "type": "STRING", "mode": "REQUIRED", "description": "unique event id"},
                {"name": "user_id", "type": "STRING", "mode": "NULLABLE", "description": ""},
                {"name": "event_date", "type": "DATE", "mode": "REQUIRED", "description": "partition column"},
                {"name": "event_timestamp", "type": "TIMESTAMP", "mode": "REQUIRED", "description": "event ingest time"},
                {"name": "referrer", "type": "STRING", "mode": "NULLABLE", "description": ""},
                {"name": "amount", "type": "NUMERIC", "mode": "NULLABLE", "description": ""},
            ],
        },
        "checks": {
            "partitions": {
                "status": "complete",
                "rows": [{
                    "partition_count": 30, "empty_partition_count": 2,
                    "total_logical_bytes": 893524000, "max_partition_bytes": 45000000,
                }],
            },
            "freshness": {"status": "complete", "rows": [{"max_value": fresh}]},
            "duplicate_keys": {"status": "complete", "rows": [{"duplicate_key_groups": 3, "duplicate_excess_rows": 5}]},
            "column_profile": {
                "status": "complete",
                "profiled_columns": ["event_id", "user_id", "event_date", "event_timestamp", "referrer", "amount"],
                "rows": [{
                    "scanned_rows": 1234567,
                    "event_id__null_count": 0, "event_id__approx_distinct": 1234500,
                    "user_id__null_count": 12340, "user_id__approx_distinct": 847000,
                    "event_date__null_count": 0, "event_date__approx_distinct": 30,
                    "event_timestamp__null_count": 0, "event_timestamp__approx_distinct": 500000,
                    "referrer__null_count": 246913, "referrer__approx_distinct": 10500,
                    "amount__null_count": 3700, "amount__approx_distinct": 5000,
                }],
            },
        },
        "expectations": [
            {"name": "min_rows", "status": "passed", "expected_min": 1000, "actual": 1234567},
            {"name": "zero_duplicates", "status": "failed", "duplicate_excess_rows": 5},
            {"name": "freshness_within", "status": "failed", "max_age": "15m", "age_seconds": 2700, "max_value": fresh},
            {"name": "max_null_rate", "column": "user_id", "status": "passed", "actual_null_rate": 0.01, "max_null_rate": 0.05},
            {"name": "max_null_rate", "column": "referrer", "status": "failed", "actual_null_rate": 0.20, "max_null_rate": 0.05},
            {"name": "no_schema_drift", "status": "passed", "added": 0, "removed": 0, "changed": 0},
        ],
        "warnings": [
            "Data checks were not run for the partition_filter check; estimate exceeded scan cap.",
        ],
        "schema_drift": {"baseline_path": "previous_report.json", "added": [], "removed": [], "changed": []},
        "sample": {"rows": [
            {"event_id": "e_1", "user_id": "u_42", "event_date": "2026-05-03", "referrer": "https://example.com", "amount": 19.99},
        ]},
    }


def main() -> int:
    report = _build_sample_report()
    skill_root = _HERE.parent
    for theme in ("auto", "light", "dark"):
        target = _HERE / f"sample_dashboard_{theme}.html"
        target.write_text(make_html(report, theme=theme), encoding="utf-8")
        print(f"wrote {target.relative_to(skill_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
