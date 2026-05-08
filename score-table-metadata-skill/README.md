# Metadata Scorecard Skill

Score authored metadata quality on Google BigQuery tables against the data-steward rubric. Reads only metadata (descriptions, labels, schema field descriptions, policy tags) — never scans data. Produces a per-table 0-100 score, A-F letter grade, per-criterion evidence, and an actionable issues list.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
gcloud auth application-default login
```

## Score every table in a dataset

```bash
python scripts/score_table_metadata.py \
  --dataset my-project.analytics \
  --output-json scorecard.json \
  --output-md scorecard.md \
  --output-html scorecard.html
```

## Score an explicit list of tables

```bash
python scripts/score_table_metadata.py \
  --tables my-project.analytics.events,my-project.analytics.users \
  --output-json scorecard.json --output-md scorecard.md
```

## CI-style health gate

Pass `--expect-min-score N` and the script exits with code `3` if any table scores below `N`. The scorecard is still written so you can inspect what failed.

```bash
python scripts/score_table_metadata.py \
  --dataset my-project.analytics \
  --expect-min-score 70 \
  --output-json scorecard.json --output-md scorecard.md
```

Exit codes: `0` clean, `1` BigQuery API error, `2` input/validation error, `3` expectation failure.

## Rubric (v1.0)

Each criterion scores 0/1/2 (fail / partial / pass).

**Table-level (8 criteria, max 16 pts):**

| Criterion | What it checks |
| --- | --- |
| `business_description` | Non-empty, ≥30 chars, not generic ("table from system X"). |
| `grain_statement` | Says what one row represents. |
| `primary_keys` | Names the key or composite key. |
| `join_guidance` | Explains how to join to related tables. |
| `ownership` | Owner/steward in labels or description. |
| `sensitivity` | PHI/PII/restricted classification stated or labeled. |
| `history_rule` | Current-vs-history rule (versioning, snapshot, SCD type). |
| `lineage` | Source system or upstream pipeline mentioned. |

**Column-level (max varies — only criteria that apply to the column name pattern are counted):**

| Criterion | Applies to | What it checks |
| --- | --- | --- |
| `has_description` | Always | Non-empty, ≥10 chars. |
| `not_type_echo` | Always | Description doesn't just repeat the type or column name. |
| `derived_or_source_status` | Always | Description states whether the value is raw from a source system or derived/calculated downstream (FK / "from upstream" / "auto-generated" / "calculated from" all count). |
| `coded_field_explained` | Names ending in `_code`, `_status`, `_flag`, `_type`, `_cd`, `_ind`, `_category` | Description mentions code system / values / enumerations. |
| `units_or_format` | Names containing `amount`, `count`, `rate`, `pct`, `temp`, `dose`, `qty`, `weight`, `date`, `timestamp`, etc. | Description states units, timezone, or format. |
| `sensitivity_flagged` | Names like `ssn`, `email`, `dob`, `phone`, `mrn`, `patient_id`, `address`, `first_name`, etc. | Description mentions sensitivity OR column has a policy tag. |
| `caveats_present` | Bonus | +2 if description mentions a known caveat phrase (`deprecated`, `legacy`, `not enforced`, `by design`, `be aware`, `note:`, `caution:`, `may be null`, `duplicates exist`, `overloaded`, etc.). |

**Combined score**: `weights.table * (table_pts / 16) + weights.column * column_mean_normalized`, scaled to 0-100. Default weights are `0.4` / `0.6`; default grades are 90+ A, 80-89 B, 70-79 C, 60-69 D, <60 F. Both are configurable — see "Custom rubric" below.

The bundled Python rubric is a deterministic heuristic (regex + keyword matching). For higher-quality grading, the same skill can be invoked through Claude Code with the BigQuery MCP connector — the agent reads each description and grades semantically. Both paths produce the same JSON shape; see [`SKILL.md`](SKILL.md) for the connector-driven flow.

## Custom rubric

Pass `--rubric-config <path>` to override weights, keyword lists, regex triggers, length thresholds, or grade cutoffs. Sections you omit fall back to the built-in defaults. The full schema is documented in [`SKILL.md`](SKILL.md#configuring-the-rubric); two starter files ship in [`examples/`](examples/):

- [`examples/rubric_default.json`](examples/rubric_default.json) — the built-in rubric, externalized. Loading it produces identical scores to the no-flag default; copy and tweak.
- [`examples/rubric_finance.json`](examples/rubric_finance.json) — a finance-domain example showing customized weights, stricter thresholds, and finance-specific keywords (LEI, CUSIP, OFAC, KYC/AML).

```bash
python scripts/score_table_metadata.py \
  --dataset my-project.analytics \
  --rubric-config examples/rubric_finance.json \
  --output-json scorecard.json --output-md scorecard.md
```

The rubric source, name, version, and SHA-256 are stamped into the JSON output and surfaced in the scorecard subtitle so reviewers can tell which rubric was applied.

## Suggested fixes

Each issue in the scorecard now ships with a suggested fix the steward can paste into the description with minimal editing. The bundled heuristic produces schema-aware templates — a missing grain statement on a table with an `encounter_id` column suggests `'Grain: one row per `encounter_id`.'`; a missing units statement on `event_timestamp` suggests `'in UTC, ISO 8601.'`. Path A (the agent-driven flow described in [`SKILL.md`](SKILL.md)) produces richer, table-specific suggestions because it reads the existing description and full schema before grading.

## HTML scorecard

The optional `--output-html` flag emits a single self-contained HTML scorecard alongside the JSON/Markdown. Inline CSS, no JavaScript. Tables are listed worst-first so the most actionable items appear at the top. Score badges, A-F grade pills, per-criterion pass/partial/fail pills, and per-column collapsible details.

```bash
python scripts/score_table_metadata.py \
  --dataset my-project.analytics \
  --output-html scorecard.html \
  --theme auto
```

`--theme {auto,light,dark}` controls the palette. `auto` ships both palettes and switches via `prefers-color-scheme`.

Pre-rendered samples live in [`examples/`](examples/) — open `examples/sample_scorecard_auto.html` (or `_light` / `_dark`) to see the output without running against a real dataset. Re-render any time after changing the renderer:

```bash
python examples/render_sample_scorecard.py
```

The generator stubs `google.cloud.bigquery`, so it does not need GCP credentials.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/
```

The test suite stubs `google.cloud.bigquery` so it runs without GCP credentials. It covers the rubric (fail/partial/pass cases for each criterion + grade boundaries), the renderer (stable Markdown, HTML structural elements), and a CLI smoke test against a stubbed client.

## Notes

This skill only reads metadata. There is no data scan, so no `--max-bytes-billed` flag is needed. For data-quality auditing (row counts, freshness, duplicates, partition health), see the sibling [`bigquery-table-evaluator-skill`](../bigquery-table-evaluator-skill/).
