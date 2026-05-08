---
name: score-table-metadata
description: Use this skill when a user wants to score, audit, grade, or assess the quality of authored metadata for one or more BigQuery tables. It scores each table against the data-steward rubric (8 table-level criteria, 6 column-level criteria), produces a 0-100 score and letter grade A-F per table, and emits a scorecard with per-criterion evidence and actionable issues. Accepts either a whole dataset or an explicit list of tables. Optional CI-style gate (--expect-min-score) exits non-zero if any table is below threshold.
---

# Metadata Scorecard

## Purpose
Score authored metadata quality on BigQuery tables against the data-steward rubric. Reads only metadata (table description, labels, column descriptions, policy tags) — never scans data. Produces a per-table 0-100 score, A-F letter grade, per-criterion evidence, and a list of actionable issues.

## Required inputs
One of:
- `dataset`: a project.dataset to enumerate and score every table within.
- `tables`: an explicit list of fully qualified `project.dataset.table` IDs.

## Optional inputs
- `expect_min_score`: integer 0-100; exit 3 if any table scores below it (CI gate).
- `rubric_config`: path to a JSON file overriding the rubric data (weights, keyword lists, regex triggers, thresholds, grade cutoffs). Omitted sections fall back to the built-in defaults. See "Configuring the rubric" below.
- HTML theme: `auto` (default) / `light` / `dark`.

## Rubric (v1.0)

Per-criterion scoring is 0/1/2 (fail / partial / pass). Hard limit on BigQuery descriptions is 1024 chars; metadata that wastes that budget on schema duplication ("string field", "the X column") scores poorly.

### Table-level criteria (max 16)

1. `business_description` — non-empty, ≥30 chars, not a generic system label like "table from <source>".
2. `grain_statement` — explicitly says what one row represents (e.g. "one row per ...", "grain: ...", "row represents ...").
3. `primary_keys` — names the primary key, composite key, or the columns that uniquely identify a row.
4. `join_guidance` — explains how to join to related tables (e.g. "join on …", "FK to …").
5. `ownership` — owner / steward / team recorded as a label (`owner`, `steward`, `team`, `domain`, `contact`) **or** explicit in description.
6. `sensitivity` — PHI / PII / restricted / confidential classification stated as a label (`pii`, `phi`, `sensitive`, `classification`) **or** explicit in description.
7. `history_rule` — current-state vs. history rule stated (e.g. "filter `latest_record_ind=1`", "type-2 SCD", "snapshot as of …").
8. `lineage` — source system or upstream pipeline stated, **or** captured in labels (`source`, `upstream`, `pipeline`).

### Column-level criteria (max varies per column)

These three **always** apply (max 6 pts):
1. `has_description` — present and non-empty (≥10 chars for full credit).
2. `not_type_echo` — doesn't just repeat the type or name (e.g. "string field", "the user_id field" → fail). Full credit requires ≥15 chars of meaningful text.
3. `derived_or_source_status` — description states whether the value is raw from a source system or derived/calculated downstream. FK / lookup / "from upstream" / "auto-generated" / "source-native" / "calculated from" all count.

These are **conditional** — they only count toward the column's max when the column name suggests they apply (max 2 pts each when applicable):
4. `coded_field_explained` — applies when the column name ends in `_code`, `_status`, `_flag`, `_type`, `_cd`, `_ind`, or `_category`. Description should mention the code system, allowed values, or enumerations.
5. `units_or_format` — applies when the column name contains `amount`, `count`, `rate`, `pct`, `temp`, `dose`, `qty`, `quantity`, `weight`, `height`, `length`, `date`, `datetime`, `timestamp`, `duration`, `elapsed`, `seconds`, `minutes`, `hours`, `days`, `price`, `cost`. Description should state units, timezone, or format.
6. `sensitivity_flagged` — applies when the column name matches `ssn`, `email`, `dob`, `date_of_birth`, `phone`, `mrn`, `patient_id`, `address`, `zip`, `postal`, `first_name`, `last_name`, `full_name`, `account`, `credit_card`, `card_number`, `tax_id`. Description should mention sensitivity, **or** the column should have BigQuery policy tags.

Bonus criterion (only counted when applicable):
7. `caveats_present` — column max +2 if description mentions a known caveat phrase (`deprecated`, `legacy`, `do not use`, `not enforced`, `by design`, `be aware`, `note:`, `caution:`, `may be null`, `duplicates exist`, `overloaded`, `multiple meanings`, etc.). Otherwise the criterion is recorded but contributes 0/0 to the column's max.

### Scoring formula

For each table:

- `table_ratio = table_points / table_max`  (table_max is always 16)
- For each column: `col_ratio = col_points / col_max`  (col_max varies by name — see above)
- `column_mean = mean(col_ratio for each column)`
- `score = round(100 * (weights.table * table_ratio + weights.column * column_mean))`
- Default weights: `weights.table = 0.4`, `weights.column = 0.6`.
- Default letter grades: 90+ A, 80-89 B, 70-79 C, 60-69 D, <60 F. Cutoffs are configurable.

### Configuring the rubric

The rubric *data* — keyword lists, regex triggers, weights, grade cutoffs, length thresholds — can be overridden by a JSON config file. The check *logic* (what counts as full credit vs. partial vs. fail) is fixed in code. Pass the file via `--rubric-config <path>` to the bundled CLI; Path A agents should read the file and respect its contents when grading.

Sections you omit fall back to the built-in defaults, so a partial config that only adjusts weights is valid. The shipped `examples/rubric_default.json` reproduces the built-in scoring exactly and is a useful starting template.

Schema (all sections optional except where noted):

```jsonc
{
  "name": "data-steward-default",
  "version": "1.0",
  "weights": {"table": 0.4, "column": 0.6},                 // must sum to ~1.0
  "grade_cutoffs": {"A": 90, "B": 80, "C": 70, "D": 60},   // strictly decreasing, in [0,100]
  "thresholds": {
    "table_desc_min": 30,                  // chars; below = partial credit on business_description
    "column_desc_min_partial": 8,          // chars; below = fail on not_type_echo
    "column_desc_min_full": 15,            // chars; below = partial on not_type_echo
    "evidence_max_chars": 90               // truncation length for evidence snippets
  },
  "column_triggers": {                     // case-insensitive regex strings
    "coded":     "_(code|status|flag|type|cd|ind|category)$",
    "measure":   "(^|_)(amount|count|rate|...)(_|$)",
    "sensitive": "(^|_)(ssn|email|dob|...)(_|$)"
  },
  "keywords": {                            // per-criterion buckets: strong / weak / label
    "<criterion-name>": {"strong": [...], "weak": [...], "label": [...]}
  },
  "type_echo_patterns": ["...regex...", "..."],   // patterns that fail not_type_echo
  "generic_table_desc_re": "...regex..."          // pattern that downgrades business_description
}
```

Valid criterion names for `keywords`:

- Table-level: `grain_statement`, `primary_keys`, `join_guidance`, `ownership`, `sensitivity`, `history_rule`, `lineage`. (`business_description` has no keyword list — it's length- and regex-based.)
- Column-level: `coded_field_explained`, `units_or_format`, `sensitivity_flagged`, `caveats_present`, `derived_or_source_status`. (`has_description` and `not_type_echo` are length- and regex-based.)

**Bucket meanings:**
- `strong` — full credit (2 pts) when any substring matches the description.
- `weak` — partial credit (1 pt) when any substring matches, only used by criteria that have a tiered structure (`primary_keys`, `join_guidance`, `ownership`, `sensitivity`, `lineage`).
- `label` — used by `ownership`, `sensitivity`, and `lineage` only. If any BigQuery label key matches, the criterion gets full credit (more reliable than description text).

**Path A note (semantic grading with a custom config):** if the user supplies a rubric config, read it before scoring. The keyword matchers are still guides, not rules — apply judgment when the description plainly satisfies a criterion even if it doesn't use a listed keyword. But the *weights*, *grade cutoffs*, *trigger regexes*, and *thresholds* are authoritative — apply them exactly so Path A and Path B agree on the numeric score and grade.

Provenance is stamped into the report under the top-level `rubric_config` key:

```jsonc
{
  "rubric_config": {
    "source": "/abs/path/to/rubric.json",   // or "builtin"
    "name": "data-steward-default",
    "version": "1.0",
    "sha256": "a1b2c3..."                   // sha256 of the file bytes; "" for builtin
  }
}
```

The renderer surfaces this in the scorecard subtitle so reviewers can tell at a glance which rubric was applied.

## Implementation

Two execution paths. **Prefer Path A** when the BigQuery MCP connector is available — it works in any Claude environment without local Python deps or `gcloud` auth.

### Path A — BigQuery MCP connector (preferred)

Requires the [Google GenAI Toolbox](https://github.com/googleapis/genai-toolbox) MCP server registered with the `--prebuilt bigquery` configuration. Tool names below assume the server entry is named `bigquery`.

**Step 1 — enumerate tables.**

If the user provided a dataset, call:
```
mcp__bigquery__list_table_ids(project="<project>", dataset="<dataset>")
```
If they provided an explicit list of `project.dataset.table` IDs, skip enumeration and use them directly.

**Step 2 — fetch each table's metadata.**

For each table ID, call:
```
mcp__bigquery__get_table_info(project="<project>", dataset="<dataset>", table="<table>")
```
This returns the description, labels, and schema (with per-field descriptions and any policy tags). No data scan, so no dry-run / scan-cap is needed for this skill.

**Step 3 — grade each criterion semantically.**

For every criterion in the rubric, decide pass / partial / fail by reading the description. You are the grader. The keyword matchers in the heuristic Python implementation are guides, not rules — apply judgment:

- Pass: the criterion is clearly satisfied (e.g. for `grain_statement`, the description plainly states what one row represents).
- Partial: the criterion is partially satisfied (e.g. mentions "key" but doesn't identify the actual key).
- Fail: the criterion is not addressed.
- N/A (column conditional criteria only): the column's name does not match the trigger pattern. Do not include the criterion at all in that column's `criteria` list.

For each criterion, capture a short evidence snippet (≤90 chars, single line) — the part of the description that justifies the score. For fails, leave evidence empty if there's nothing relevant.

**Step 4 — assemble the report dict.**

Include the **full** table description in `table_metadata.description` and the full `labels` dict in `table_metadata.labels` — the renderer surfaces both in a collapsible "Description & labels" panel at the top of each table card. Likewise include each column's full description in its `description` field. The `evidence` snippets inside each criterion are short (≤90 chars) and are only used to justify a specific score; the renderer uses the full descriptions for the user-facing display.

The shape must match what `scripts/render_scorecard.py` expects:

```json
{
  "rubric_version": "1.0",
  "rubric_config": {
    "source": "builtin",
    "name": "data-steward-default",
    "version": "1.0",
    "sha256": ""
  },
  "scored_at": "2026-05-04T17:32:00+00:00",
  "scope": {"dataset": "project.dataset"},
  "tables": [
    {
      "table_id": "project.dataset.table",
      "score": 87,
      "grade": "B",
      "table_metadata": {
        "description": "Encounter records for inpatient, outpatient, and ED visits. Grain: one row per encounter version per coid. ...",
        "labels": {"owner": "clinical-data-team", "phi": "true"},
        "points": 14,
        "max": 16,
        "criteria": [
          {"name": "business_description", "points": 2, "max": 2, "passed": true, "evidence": "Encounter records for inpatient, outpatient, ED visits..."},
          {"name": "grain_statement", "points": 2, "max": 2, "passed": true, "evidence": "Grain: one row per encounter version per coid"},
          {"name": "primary_keys", "points": 2, "max": 2, "passed": true, "evidence": "..."},
          {"name": "join_guidance", "points": 2, "max": 2, "passed": true, "evidence": "Join to patient via empi_text"},
          {"name": "ownership", "points": 2, "max": 2, "passed": true, "evidence": "label `owner=clinical-data-team`"},
          {"name": "sensitivity", "points": 2, "max": 2, "passed": true, "evidence": "Contains PHI"},
          {"name": "history_rule", "points": 1, "max": 2, "passed": false, "evidence": "Use latest_record_ind=1 for current state"},
          {"name": "lineage", "points": 1, "max": 2, "passed": false, "evidence": "from ADT feed"}
        ]
      },
      "column_metadata": {
        "mean_normalized": 0.85,
        "column_count": 24,
        "columns": [
          {
            "name": "discharge_disposition_code",
            "type": "STRING",
            "mode": "NULLABLE",
            "description": "Coded discharge status from the source ADT feed; values map to home, transfer, expired, hospice. Nullable for in-progress encounters.",
            "points": 8,
            "max": 8,
            "criteria": [
              {"name": "has_description", "points": 2, "max": 2, "passed": true, "evidence": "Coded discharge status..."},
              {"name": "not_type_echo", "points": 2, "max": 2, "passed": true, "evidence": "..."},
              {"name": "derived_or_source_status", "points": 2, "max": 2, "passed": true, "evidence": "from the source ADT feed"},
              {"name": "coded_field_explained", "points": 2, "max": 2, "passed": true, "evidence": "values map to home/transfer/expired/hospice"}
            ]
          }
        ]
      },
      "issues": [
        {
          "criterion": "history_rule",
          "message": "No current-state vs. history rule; clarify versioning or how to filter to the latest record.",
          "suggestion": "Looks like a type-2 SCD. Add: 'Type-2 SCD; filter `latest_record_ind=1` for current state.'"
        },
        {
          "criterion": "lineage",
          "message": "Source system / lineage not mentioned.",
          "suggestion": "Add 'Loaded from <source-system>' to the description, or attach a `source=<system>` label."
        },
        {
          "criterion": "has_description",
          "column": "user_id",
          "message": "Column `user_id` has no description.",
          "suggestion": "Add: 'Stable identifier for the user; sourced from the upstream registry.'"
        }
      ]
    }
  ],
  "expectations": [],
  "warnings": []
}
```

The `score`, `grade`, `points`/`max` totals, `mean_normalized`, and the `passed` boolean must all be self-consistent. Compute them; do not invent.

**Issues with suggested fixes (Path A): write concrete, table-specific suggestions.**

Each entry in `issues` is a dict with `criterion`, `message`, `suggestion`, and (for column-level issues) `column`. The `message` describes what's missing; the `suggestion` is a concrete proposed fix that the steward can paste into the table or column description with minimal editing.

The bundled Python heuristic produces *generic templates* (e.g., "Add: 'Grain: one row per `<entity>`.'"). You can do far better — you have the description and full schema in context. When grading semantically, write suggestions that:

- Reference real column names from the table's schema, not placeholders.
- Use the existing description's tone and verbosity. If the description is terse, suggest a terse fix; if it's a paragraph, suggest a sentence or two.
- For grain / primary keys / joins: name the actual likely PK/FK columns based on naming conventions (`id`, `*_id`) and types.
- For sensitivity: when the table contains columns matching PHI/PII patterns, list them by name in the suggestion so the steward can verify.
- For history rules: if the schema includes `valid_from`/`valid_to` or `*_record_ind`, suggest specific type-2 SCD phrasing referencing those columns.
- For column-level issues: keep suggestions short (one or two sentences) and pasteable directly into the column description. For `units_or_format`, use the column type and name to pick units (e.g., `event_timestamp` → "in UTC, ISO 8601"; `total_amount_usd` → currency is already obvious, suggest decimal precision instead).

Suggestions should be best-effort proposals, not authoritative facts. Phrase them as drafts ("Add: '...'.") so the steward knows they need to verify before committing. **Never fabricate** facts you can't infer from the description and schema — for example, don't claim a specific source system unless the description or labels already hint at one.

The renderer surfaces `message` prominently and `suggestion` underneath in muted/italic styling, so a reader scanning the scorecard sees both at a glance.

**Step 5 — render.**

Write the report dict as JSON, then run:

```bash
python scripts/render_scorecard.py --input scorecard.json --output-md scorecard.md --output-html scorecard.html --theme auto
```

To gate against a CI threshold:

```bash
python scripts/render_scorecard.py --input scorecard.json --output-md scorecard.md --expect-min-score 70
```

`render_scorecard.py` has no BigQuery dependency — Path A works in any environment with Python + the skill files (no `google-cloud-bigquery`, no `gcloud auth` needed).

### Path B — Bundled Python script (fallback)

Use this when the MCP connector isn't available — typically a local Claude Code session where `google-cloud-bigquery` is installed and `gcloud auth application-default login` has been run.

```bash
python scripts/score_table_metadata.py \
  --dataset my-project.analytics \
  --output-json scorecard.json \
  --output-md scorecard.md \
  --output-html scorecard.html \
  --expect-min-score 70
```

Or for an explicit list:

```bash
python scripts/score_table_metadata.py \
  --tables my-project.analytics.events,my-project.analytics.users \
  --output-json scorecard.json --output-md scorecard.md
```

To apply a custom rubric:

```bash
python scripts/score_table_metadata.py \
  --dataset my-project.analytics \
  --rubric-config path/to/rubric.json \
  --output-json scorecard.json --output-md scorecard.md
```

The bundled script applies the deterministic heuristic in `_rubric.py`. The agent-driven Path A produces higher-quality grading because it can read meaning rather than match keywords; both paths are valid and produce the same JSON shape.

## Output

- **JSON** — machine-readable scorecard, suitable as input to `render_scorecard.py` or downstream tooling.
- **Markdown** — human review; one section per table with criteria tables and an issues list.
- **HTML scorecard** (optional `--output-html`) — single self-contained file with inline CSS, score badges, A-F grade pills, per-criterion pass/partial/fail pills, collapsible per-column breakdowns, and a top-level expectations status row. No external assets, no JavaScript. Pass `--theme {auto,light,dark}` (default `auto`).

Tables are listed worst-first (lowest score) so the most actionable items appear at the top of the scorecard.

## Authentication

- **Path A**: handled by the MCP server (it picks up `GOOGLE_APPLICATION_CREDENTIALS` and `BIGQUERY_PROJECT` from its own environment).
- **Path B**: Google Application Default Credentials, or any environment accepted by the `google-cloud-bigquery` client library.

## Exit codes

- `0` — clean (all tables scored, all expectations passed if any).
- `1` — BigQuery API error.
- `2` — input/validation error.
- `3` — expectation failure (`--expect-min-score` threshold not met).
