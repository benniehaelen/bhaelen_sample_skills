#!/usr/bin/env python3
"""Render the three sample scorecards (auto/light/dark) for visual reference.

Run from the skill root:

    python examples/render_sample_scorecard.py

The renderer modules in ``scripts/`` have no BigQuery dependency, so this
script does not need GCP credentials. Re-run whenever you change the rubric
or renderer to refresh the committed HTML samples.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from _rubric import RUBRIC_VERSION, score_table  # noqa: E402
from _scorecard_render import make_html  # noqa: E402


def _good_table() -> dict:
    return {
        "table_id": "my-project.analytics.encounters",
        "description": (
            "Encounter records for inpatient, outpatient, and ED visits. "
            "Grain: one row per encounter version per coid and patient_account_num. "
            "Composite key on (coid, patient_account_num, encounter_version). "
            "Join to patient via foreign key empi_text or medical_record_urn. "
            "Owner: clinical-data-team. Contains PHI; access via curated view only. "
            "Type-2 SCD; filter latest_record_ind=1 for current state. "
            "Loaded from the ADT source system nightly."
        ),
        "labels": {"owner": "clinical-data-team", "phi": "true"},
        "columns": [
            {"name": "encounter_id", "type": "STRING", "mode": "NULLABLE",
             "description": "Stable encounter identifier; foreign key to encounters.id used to join clinical detail tables."},
            {"name": "patient_account_num", "type": "STRING", "mode": "NULLABLE",
             "description": "Account number from the source registration system; combine with coid for uniqueness."},
            {"name": "discharge_disposition_code", "type": "STRING", "mode": "NULLABLE",
             "description": "Coded discharge status from the source ADT system; values map to home, transfer, expired, hospice. May be null when the encounter is still in progress."},
            {"name": "encounter_start_timestamp", "type": "TIMESTAMP", "mode": "NULLABLE",
             "description": "Encounter start time in UTC, ISO 8601 format. Source-native from ADT."},
            {"name": "latest_record_ind", "type": "INT64", "mode": "NULLABLE",
             "description": "Y/N flag indicating the most recent version of the encounter record. Derived from the type-2 SCD load process."},
            {"name": "patient_email", "type": "STRING", "mode": "NULLABLE",
             "description": "Patient email from the source registration system — PII, do not export to non-curated zones.",
             "policy_tags": ["projects/x/locations/us/taxonomies/1/policyTags/2"]},
        ],
    }


def _mid_table() -> dict:
    return {
        "table_id": "my-project.analytics.events",
        "description": (
            "Application events emitted by the patient portal. "
            "Use for session and engagement analysis."
        ),
        "labels": {"owner": "engagement-team"},
        "columns": [
            {"name": "event_id", "type": "STRING", "mode": "NULLABLE",
             "description": "Unique identifier for the event."},
            {"name": "event_timestamp", "type": "TIMESTAMP", "mode": "NULLABLE",
             "description": "When the event happened."},
            {"name": "event_type", "type": "STRING", "mode": "NULLABLE",
             "description": "Type of event."},
            {"name": "session_id", "type": "STRING", "mode": "NULLABLE",
             "description": "Session identifier, joins to sessions table."},
        ],
    }


def _bad_table() -> dict:
    return {
        "table_id": "my-project.analytics.users",
        "description": "Users table.",
        "labels": {},
        "columns": [
            {"name": "user_id", "type": "STRING", "mode": "NULLABLE",
             "description": "string field"},
            {"name": "ssn", "type": "STRING", "mode": "NULLABLE",
             "description": "the ssn field"},
            {"name": "email", "type": "STRING", "mode": "NULLABLE",
             "description": None},
            {"name": "created_date", "type": "DATE", "mode": "NULLABLE",
             "description": "Created date."},
        ],
    }


def build_sample_report() -> dict:
    tables = [score_table(t) for t in (_good_table(), _mid_table(), _bad_table())]
    expectations = [{
        "name": "min_score",
        "threshold": 70,
        "status": "failed" if any(t["score"] < 70 for t in tables) else "passed",
        "failing_tables": [
            {"table_id": t["table_id"], "score": t["score"]}
            for t in tables if t["score"] < 70
        ],
    }]
    return {
        "rubric_version": RUBRIC_VERSION,
        "scored_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "scope": {"dataset": "my-project.analytics"},
        "tables": tables,
        "expectations": expectations,
        "warnings": [],
    }


def main() -> int:
    report = build_sample_report()
    for theme in ("auto", "light", "dark"):
        out = _HERE / f"sample_scorecard_{theme}.html"
        out.write_text(make_html(report, theme=theme), encoding="utf-8")
        print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
