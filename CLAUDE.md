# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This repo packages Claude Code skills. Each top-level directory is one skill. Currently:

- `bigquery-table-evaluator-skill/` — evaluates a Google BigQuery table and emits JSON / Markdown / HTML reports.

All commands below are run from inside the skill directory (`cd bigquery-table-evaluator-skill`).

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

Re-render the example dashboards after changing `scripts/_render.py`:

```bash
python examples/render_sample_dashboard.py
```

Run the evaluator end-to-end (Path B — local script with `google-cloud-bigquery`): see `README.md` and `examples/evaluate_table.sh` for the full flag set. Exit codes: `0` clean, `1` BigQuery API error, `2` input/validation error, `3` expectation failure.

## Architecture

### Two execution paths, one renderer

The skill has two ways to run, and both must produce **byte-identical** Markdown / HTML for the same JSON input:

1. **Path A — BigQuery MCP connector** (preferred when available). The agent calls `mcp__bigquery__get_table_info` + `mcp__bigquery__execute_sql` itself, assembles a report dict matching the shape in `SKILL.md`, then runs `scripts/render_report.py` to emit Markdown / HTML.
2. **Path B — `scripts/evaluate_bigquery_table.py`**. Uses the `google-cloud-bigquery` client directly. Only this entrypoint imports the BigQuery SDK.

The shared contract is the JSON report shape (documented in `SKILL.md` under "Report shape"). When changing the report shape, update **both** paths and the renderer in lock-step.

### Module split inside `scripts/`

The split exists so `render_report.py` can run in any environment without `google-cloud-bigquery` installed:

- `_validation.py` — SQL identifier hygiene, duration parsing, WHERE-clause guard. **Pure functions, no third-party deps.** All SQL-injection invariants live here. Never build SQL from user input without going through `quote_table` / `quote_column` / `validate_where_clause`.
- `_serialize.py` — JSON-safe value coercion (datetime / Decimal / bytes), human-readable formatters (`bytes_human`, `humanize_seconds`, `format_datetime`). Pure.
- `_expectations.py` — CI-style expectation evaluation and schema-drift detection. Reads a populated report dict; never triggers queries.
- `_render.py` — Markdown + HTML rendering, including SVG charts. The single source of truth for output format.
- `evaluate_bigquery_table.py` — CLI + BigQuery client interactions. Lazy-imports `google.cloud.bigquery` so the renderer modules stay BigQuery-free.
- `render_report.py` — JSON-in, Markdown/HTML-out renderer for Path A. No BigQuery dependency.

### Cost-safety invariant

Every data-scanning query in Path B goes through `run_query_with_guard`: dry-run first, compare `total_bytes_processed` against `--max-bytes-billed`, and either skip with `status: "skipped_estimate_exceeds_cap"` or run with `maximum_bytes_billed` enforced. Path A reproduces this by calling `mcp__bigquery__execute_sql` with `dry_run: true` first. Don't add a new data check that bypasses this guard.

### Identifier validation

Table IDs and column names are validated against tight regexes in `_validation.py` before being interpolated into SQL (always wrapped in backticks). The `--where` clause uses a denylist for backticks, semicolons, and SQL comments — it's a best-effort guard, not a parser, so callers are still responsible for the expression being valid SQL.

### Tests

`tests/conftest.py` installs stub modules for `google.cloud.bigquery` and `google.api_core.exceptions` so the test suite runs with no GCP setup. Tests load the renderer / validation / expectations modules directly via fixtures (`render_module`, `validation_module`, etc.) — when adding a new internal module to `scripts/`, add a matching fixture there.
