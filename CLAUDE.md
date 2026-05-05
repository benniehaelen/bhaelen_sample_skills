# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This repo packages Claude Code skills. Each top-level directory is one skill. Currently:

- `bigquery-table-evaluator-skill/` — evaluates a Google BigQuery table and emits JSON / Markdown / HTML reports.
- `score-table-metadata-skill/` — scores authored metadata quality on BigQuery tables against the data-steward rubric. Per-table 0-100 score and A-F letter grade with per-criterion evidence and an issues list.

Skills are intentionally **self-contained**: each one is meant to be drop-in copy-pasteable into another repo without cross-skill imports. That's why `_serialize.py` and `_validation.py` are duplicated between the two skills (with the second skill adding `split_dataset_id` to its copy) — promoting them to a shared `_common/` package would couple the skills and break the drop-in property. Keep this convention unless and until a third skill makes the duplication actively painful.

All commands below are run from inside a skill directory (`cd bigquery-table-evaluator-skill` or `cd score-table-metadata-skill`).

## Common commands

Setup (per skill):

```bash
python -m venv .venv
source .venv/bin/activate         # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
gcloud auth application-default login   # only needed for Path B (live BigQuery)
```

Tests (no GCP credentials required — `conftest.py` stubs `google.cloud.bigquery`):

```bash
pytest tests/
pytest tests/test_render.py                          # one file
pytest tests/test_expectations.py::test_min_rows     # one test
```

Re-render the example dashboards after changing the renderer:

```bash
# bigquery-table-evaluator-skill
python examples/render_sample_dashboard.py
# score-table-metadata-skill
python examples/render_sample_scorecard.py
```

Run the evaluator end-to-end (Path B — local script with `google-cloud-bigquery`): see each skill's `README.md` for the full flag set. Exit codes (both skills): `0` clean, `1` BigQuery API error, `2` input/validation error, `3` expectation failure.

## Architecture

Both skills follow the same Path A / Path B / shared-renderer pattern. Differences below.

### Two execution paths, one renderer

Each skill has two ways to run, and both must produce **byte-identical** Markdown / HTML for the same JSON input:

1. **Path A — BigQuery MCP connector** (preferred when available). The agent calls the MCP tools (`mcp__bigquery__get_table_info`, `mcp__bigquery__execute_sql`, `mcp__bigquery__list_table_ids`), assembles a report dict matching the shape documented in `SKILL.md`, then runs the skill's renderer script to emit Markdown / HTML.
2. **Path B — local CLI script.** Uses the `google-cloud-bigquery` client directly. Only this entrypoint imports the BigQuery SDK.

The shared contract is the JSON report shape (documented in each `SKILL.md`). When changing the report shape, update **both** paths and the renderer in lock-step.

### Module split inside `scripts/` (shared between skills)

The split exists so the renderer module can run in any environment without `google-cloud-bigquery` installed:

- `_validation.py` — identifier hygiene (table / column / dataset IDs), CSV parsing, and (for the evaluator) duration parsing + WHERE-clause guard. **Pure functions, no third-party deps.** Never build SQL from user input without going through `quote_table` / `quote_column` / `validate_where_clause`.
- `_serialize.py` — JSON-safe value coercion (datetime / Decimal / bytes), human-readable formatters. Pure.
- CLI script (`evaluate_bigquery_table.py` / `score_table_metadata.py`) — Path B entry. Lazy-imports `google.cloud.bigquery` so the renderer modules stay BigQuery-free.
- Renderer script (`render_report.py` / `render_scorecard.py`) — JSON-in, Markdown/HTML-out renderer for Path A. No BigQuery dependency.

Skill-specific modules:

- `bigquery-table-evaluator-skill/scripts/_expectations.py` — CI-style expectation evaluation and schema-drift detection.
- `bigquery-table-evaluator-skill/scripts/_render.py` — Markdown + HTML rendering, including SVG charts. Single source of truth for that skill's output.
- `score-table-metadata-skill/scripts/_rubric.py` — heuristic rubric (8 table-level criteria, 6 column-level, conditional applicability based on column-name patterns). Single source of truth for deterministic scoring.
- `score-table-metadata-skill/scripts/_scorecard_render.py` — Markdown + HTML scorecard rendering.

### Cost-safety invariant (evaluator only)

Every data-scanning query in `bigquery-table-evaluator-skill` Path B goes through `run_query_with_guard`: dry-run first, compare `total_bytes_processed` against `--max-bytes-billed`, and either skip with `status: "skipped_estimate_exceeds_cap"` or run with `maximum_bytes_billed` enforced. Path A reproduces this by calling `mcp__bigquery__execute_sql` with `dry_run: true` first. Don't add a new data check that bypasses this guard.

`score-table-metadata-skill` only reads metadata (no SQL, no scans) — no cost guard needed.

### Identifier validation

Table IDs, dataset IDs, and column names are validated against tight regexes in `_validation.py` before being interpolated into SQL or used in API calls (always wrapped in backticks for SQL). The evaluator's `--where` clause uses a denylist for backticks, semicolons, and SQL comments — best-effort guard, not a parser, so callers remain responsible for valid SQL.

### Rubric semantics (scorer only)

The metadata-scoring rubric in `_rubric.py` distinguishes **always-applicable** column criteria (`has_description`, `not_type_echo`) from **conditionally-applicable** criteria that only count toward a column's max when the column's name matches a trigger pattern (`coded_field_explained`, `units_or_format`, `sensitivity_flagged`). The `caveats_present` criterion is bonus — it only contributes to a column's max when the description actually contains a caveat phrase. This means a non-coded, non-measure, non-sensitive column has a max of 4 (the two always-on criteria), so it isn't penalized for not being something it isn't. Aggregation normalizes by `points / max` per column, so the scoring is fair across heterogeneous schemas.

### Tests

Each skill's `tests/conftest.py` installs stub modules for `google.cloud.bigquery` and `google.api_core.exceptions` so the test suite runs with no GCP setup. Tests load internal modules directly via per-module pytest fixtures — when adding a new internal module to `scripts/`, add a matching fixture there.
