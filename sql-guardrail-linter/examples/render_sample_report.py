#!/usr/bin/env python3
"""Render the bundled example guardrail report (no BigQuery, no network).

Builds a cost_aware-style report by parsing a handful of statements and
injecting table metadata and dry-run byte estimates directly, then writes:

- ``sample_report.json``  the report dict (the contract render_report consumes),
- ``sample_report.md``    the Markdown report,
- ``sample_report_auto.html`` / ``_light.html`` / ``_dark.html``.

Run from the skill root after changing the renderer or scoring:

    python examples/render_sample_report.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from _parser import parse_statement  # noqa: E402
from _controls import LintContext  # noqa: E402
from _scoring import build_report, score_statement  # noqa: E402
from render_report import make_html, make_markdown  # noqa: E402

# A frozen timestamp keeps the committed sample stable across re-renders.
LINTED_AT = "2026-06-03T16:20:00+00:00"
CEILING = 10 * 1024 ** 3  # 10 GiB

TENANT_REGISTRY = {"acme-prod.clinical.encounter": "coid"}
PII_POLICY = {"acme-prod.clinical.encounter": ["ssn", "patient_name"]}
TABLE_METADATA = {
    "acme-prod.clinical.encounter": {
        "partition_field": "admit_date",
        "pii_columns": {"ssn", "patient_name"},
    },
    "acme-prod.reference.facility": {"partition_field": "", "pii_columns": set()},
}

# (sql, estimated_bytes) pairs. estimated_bytes stands in for a dry-run.
CASES = [
    (
        "SELECT coid, COUNT(*) AS encounters FROM acme-prod.clinical.encounter "
        "WHERE admit_date >= '2026-01-01' AND coid = @caller_coid GROUP BY coid",
        2 * 1024 ** 3,
    ),
    (
        "SELECT ssn, patient_name, admit_date FROM acme-prod.clinical.encounter "
        "WHERE admit_date >= '2026-05-01' ORDER BY admit_date",
        41 * 1024 ** 3,
    ),
    (
        "SELECT a.encounter_id, b.facility_name FROM acme-prod.clinical.encounter a "
        "CROSS JOIN acme-prod.reference.facility b",
        12 * 1024 ** 3,
    ),
    (
        "DELETE FROM acme-prod.clinical.encounter WHERE admit_date < '2020-01-01'",
        None,
    ),
]


def build() -> dict:
    rubric = json.loads((_ROOT / "rubric.json").read_text(encoding="utf-8"))
    cards = []
    for index, (sql, est) in enumerate(CASES, start=1):
        stmt = parse_statement(sql)
        table_metadata = {
            t.written.lower(): TABLE_METADATA.get(t.written, {"partition_field": "", "pii_columns": set()})
            for t in stmt.tables
        }
        ctx = LintContext(
            mode="cost_aware",
            max_bytes_ceiling=CEILING,
            tenant_registry=TENANT_REGISTRY,
            pii_policy=PII_POLICY,
            pii_allowlist=set(),
            table_metadata=table_metadata,
            estimated_bytes=est,
            bytes_available=est is not None,
        )
        cards.append(score_statement(stmt, ctx, rubric, statement_id=f"stmt_{index:04d}", source=f"candidate_queries.sql #{index}"))

    report = build_report(
        statements=cards,
        rubric=rubric,
        mode="cost_aware",
        scope={"source": "sql_file", "table": "candidate_queries.sql", "statement_count": len(cards)},
        gate_severity="high",
        linted_at=LINTED_AT,
        rubric_config={"source": "builtin", "name": "nl2sql-guardrail-default", "version": "1.0", "sha256": ""},
        warnings=["Example report: byte estimates are illustrative, not from a live dry-run."],
    )
    # Show a representative gate result in the sample.
    report["expectations"] = [{
        "name": "expect_no_violations_at",
        "limit": "high",
        "actual": report["summary"]["statements_with_violations"],
        "passed": report["summary"]["statements_with_violations"] == 0,
        "message": f"{report['summary']['statements_with_violations']} statement(s) violate at or above high.",
    }]
    return report


def main() -> None:
    out = Path(__file__).resolve().parent
    report = build()
    (out / "sample_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "sample_report.md").write_text(make_markdown(report), encoding="utf-8")
    for theme in ("auto", "light", "dark"):
        (out / f"sample_report_{theme}.html").write_text(make_html(report, theme=theme), encoding="utf-8")
    print(f"Wrote sample_report.json/.md and sample_report_(auto|light|dark).html to {out}")


if __name__ == "__main__":
    main()
