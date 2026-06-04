# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This repo packages Claude Code skills. Each top-level directory is one skill. Currently:

- `bigquery-table-evaluator-skill/` — evaluates a Google BigQuery table and emits JSON / Markdown / HTML reports.
- `score-table-metadata-skill/` — scores authored metadata quality on BigQuery tables against the data-steward rubric. Per-table 0-100 score and A-F letter grade with per-criterion evidence and an issues list.
- `mcp-schema-linter/` — scores the tool definitions exposed by an MCP server against a token-economy rubric. Per-tool 0-100 score and A-F grade, total catalog token cost, and a top-refactor list ranked by tokens saved. **No BigQuery dependency**; reads tool definitions from a live MCP server, a `tools/list` JSON dump, or a file. Deps are `tiktoken` + `requests` only.
- `sql-guardrail-linter/` — lints generated SQL against a guardrail control catalog (12 controls, 3 tiers) before it runs: read-only/single-statement safety, comment injection, partition filters, scan ceiling, cartesian joins, SELECT *, unbounded results, tenant predicates, PII access, fully qualified names, ORDER BY without LIMIT. Per-statement 0-100 score and A-F grade, per-control pass/warn/fail/na findings, and a severity-ranked top-fixes list. Three modes: `static` (parse only, deps are `sqlglot` only), `cost_aware`, and `usage_log` (the latter two add a BigQuery connection for dry-run cost and metadata). Never executes the audited SQL; cost is measured with dry-run only.

**Folder naming:** the directory name is what becomes the slash-command name, and that is the only hard requirement. The two BigQuery skills carry a `-skill` suffix; `mcp-schema-linter/` and `sql-guardrail-linter/` deliberately drop it. Don't "fix" the suffix on the linters, and don't assume a new skill must have one.

Skills are intentionally **self-contained**: each one is meant to be drop-in copy-pasteable into another repo without cross-skill imports. That's why `_serialize.py` and `_validation.py` are duplicated between the two BigQuery skills (with `score-table-metadata-skill` adding `split_dataset_id` to its copy) — promoting them to a shared `_common/` package would couple the skills and break the drop-in property. The MCP linter also has a `_validation.py`, but it shares no code with the others (it validates rubric structure and CLI args, not SQL identifiers) and has no `_serialize.py` at all. The SQL guardrail linter has both: a `_serialize.py` (only `serialize` + `bytes_human`, the subset its renderer needs) and a `_validation.py` that mixes the BigQuery skills' SQL identifier hygiene with the MCP linter's rubric/CLI validation. The duplication is intentional in every case; keep this convention unless the divergence makes it actively painful.

All commands below are run from inside a skill directory (`cd bigquery-table-evaluator-skill`, `cd score-table-metadata-skill`, `cd mcp-schema-linter`, or `cd sql-guardrail-linter`). Each skill gets its own `.venv` — don't share an environment across skills, since the self-containment convention means versions and dependencies can drift independently.

## Common commands

Setup (per skill):

```bash
python -m venv .venv
source .venv/bin/activate         # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
gcloud auth application-default login   # only the two BigQuery skills, Path B; the linter needs no GCP auth
```

Tests:

```bash
pytest tests/
pytest tests/test_render.py                          # one file
pytest tests/test_expectations.py::test_min_rows     # one test
```

The two BigQuery skills require no GCP credentials because `conftest.py` stubs `google.cloud.bigquery`. The linter has no heavy dependency to stub, so its `conftest.py` only puts `scripts/` on the path and exposes a `rubric` fixture; tests that need `tiktoken` skip cleanly when it is not installed.

Re-render the example dashboards after changing the renderer:

```bash
# bigquery-table-evaluator-skill
python examples/render_sample_dashboard.py
# score-table-metadata-skill
python examples/render_sample_scorecard.py
# mcp-schema-linter (scores the bundled example servers; needs tiktoken, no network)
python examples/render_sample_scorecard.py
# sql-guardrail-linter (lints the bundled example queries; needs sqlglot, no network)
python examples/render_sample_report.py
```

Run a skill end-to-end (Path B): see each skill's `README.md` for the full flag set. Exit codes are uniform across all skills: `0` clean, `1` upstream fetch error (BigQuery API for the BigQuery skills and the SQL linter's cost_aware/usage_log modes, MCP fetch for the MCP linter), `2` input/validation error (including a statement that fails to parse, for the SQL linter), `3` expectation failure.

## Architecture

All four skills follow the same Path A / Path B / shared-renderer pattern: an agent path that grades semantically, a local CLI path that grades deterministically, and one renderer that both feed. The two BigQuery skills fetch via the BigQuery MCP connector; the MCP linter swaps that for MCP tool-list fetching; the SQL guardrail linter parses SQL locally (and, in cost_aware/usage_log modes, dry-runs against BigQuery). All keep the same JSON-in / renderer-out contract. Differences below.

### Two execution paths, one renderer

Each skill has two ways to run, and both must produce **byte-identical** Markdown / HTML for the same JSON input:

1. **Path A — agent-driven** (preferred when available). For the BigQuery skills the agent calls the MCP tools (`mcp__bigquery__get_table_info`, `mcp__bigquery__execute_sql`, `mcp__bigquery__list_table_ids`); for the MCP linter the agent fetches the server's `tools/list` and grades each criterion semantically; for the SQL guardrail linter the agent collects the statements, parses each, dry-runs them (`dry_run: true`) and reads table metadata via the BigQuery MCP connector for the conditional controls. Either way the agent assembles a report dict matching the shape documented in `SKILL.md`, then runs the skill's renderer script to emit Markdown / HTML.
2. **Path B — local CLI script.** The BigQuery skills use the `google-cloud-bigquery` client directly (only this entrypoint imports the SDK). The MCP linter's CLI (`lint_mcp_schema.py`) fetches the tool list itself via JSON-RPC / file / inline JSON and grades with the deterministic rule modules in `_rules/`. The SQL guardrail linter's CLI (`lint_sql.py`) parses each statement with `sqlglot` and grades with the deterministic control modules in `_controls/`; `static` mode needs no BigQuery, while `cost_aware` / `usage_log` lazy-import the client in `_metadata.py`.

The shared contract is the JSON report shape (documented in each `SKILL.md`). When changing the report shape, update **both** paths and the renderer in lock-step. There is no golden-file parity test between Path A and Path B output — parity is enforced by both paths feeding the same renderer module from the same JSON shape, so review changes to the report dict assembly with that contract in mind.

### Module split inside `scripts/` (shared between skills)

The split exists so the renderer module can run in any environment without `google-cloud-bigquery` installed:

- `_validation.py` — identifier hygiene (table / column / dataset IDs), CSV parsing, and (for the evaluator) duration parsing + WHERE-clause guard. **Pure functions, no third-party deps.** Never build SQL from user input without going through `quote_table` / `quote_column` / `validate_where_clause`.
- `_serialize.py` — JSON-safe value coercion (datetime / Decimal / bytes), human-readable formatters. Pure.
- CLI script (`evaluate_bigquery_table.py` / `score_table_metadata.py`) — Path B entry. Lazy-imports `google.cloud.bigquery` so the renderer modules stay BigQuery-free.
- Renderer script (`render_report.py` / `render_scorecard.py`) — JSON-in, Markdown/HTML-out renderer for Path A. No BigQuery dependency.

Skill-specific modules:

- `bigquery-table-evaluator-skill/scripts/_expectations.py` — CI-style expectation evaluation and schema-drift detection.
- `bigquery-table-evaluator-skill/scripts/_render.py` — Markdown + HTML rendering, including SVG charts. Single source of truth for that skill's output.
- `score-table-metadata-skill/scripts/_rubric.py` — heuristic rubric (8 table-level criteria, 6 column-level, conditional applicability based on column-name patterns). Single source of truth for deterministic scoring. Rubric *data* (keyword lists, regex triggers, weights, grade cutoffs, thresholds) is externalizable via `--rubric-config <path>` (JSON; schema in `SKILL.md`); check *logic* stays in code. The built-in `DEFAULT_CONFIG` reproduces historical scoring exactly, so existing reports and tests are unaffected when no config is passed.
- `score-table-metadata-skill/scripts/_scorecard_render.py` — Markdown + HTML scorecard rendering.

The MCP schema linter has a different `scripts/` layout because it fetches and tokenizes instead of querying BigQuery:

- `mcp-schema-linter/scripts/_fetchers.py` — normalizes the three input modes (URL via JSON-RPC over Streamable HTTP, file, inline JSON) into one canonical `{server, tools, extras}` shape.
- `mcp-schema-linter/scripts/_tokenizer.py` — tokenizer wrappers. `cl100k_base` (offline via `tiktoken`, the default) or `claude` (Anthropic `count_tokens` endpoint, behind a flag, needs `ANTHROPIC_API_KEY`). **Named `_tokenizer`, not `_tokenize`**, because Python 3.12+ ships a private stdlib `_tokenize` C accelerator that shadows it.
- `mcp-schema-linter/scripts/_rules/` — one module per criterion (15 rules across 3 tiers), each exposing `check(tool, catalog, rubric) -> CriterionResult`. `_rules/__init__.py` holds the dataclass + `all_rules()` registry; `_rules/_helpers.py` holds shared helpers (parameter walk, sibling detection, verb classification). Rule modules read all thresholds and keyword lists from the rubric; only natural-language-detection regexes (when-to-use phrasings, contrast vocabulary, format/unit markers) live in code, since those are check *logic* not rubric *data*.
- `mcp-schema-linter/scripts/_scoring.py` — applies the rules, computes per-tool score/grade, the token-weighted catalog grade, addressable-savings split (measured Tier 1 vs estimated Tier 2), and the `top_refactors` ranking.
- `mcp-schema-linter/scripts/_suggest.py` — template-based rewrite suggestions per failed criterion. **Named `_suggest`, not `_suggestions`**, to dodge the private stdlib `_suggestions` module (the traceback "did you mean" helper).
- `mcp-schema-linter/scripts/render_scorecard.py` — single self-contained file that is both the Markdown/HTML renderer (imported by `lint_mcp_schema.py`) and the Path A CLI. No two-file split like the metadata scorer.

**Authoring convention (linters only):** no em-dashes anywhere in the shipped files (code, comments, docstrings, README, example descriptions) of `mcp-schema-linter` or `sql-guardrail-linter`. Use commas, colons, or sentence breaks. This `CLAUDE.md` keeps the repo's existing em-dash style; the constraint is scoped to those two skills' shipped files.

The SQL guardrail linter has yet another `scripts/` layout, because it parses SQL and (optionally) dry-runs against BigQuery instead of fetching a tool list:

- `sql-guardrail-linter/scripts/_parser.py` — the only module in the hot path that imports `sqlglot`. Turns a raw statement into a `ParsedStatement` (statement type, base tables, projected/WHERE columns, joins, LIMIT/ORDER BY/aggregation, cartesian evidence). Also owns the comment/string-aware statement splitter for `--sql-file`. Everything downstream (controls, scoring, renderer) is pure Python over `ParsedStatement`, so the renderer needs neither `sqlglot` nor BigQuery.
- `sql-guardrail-linter/scripts/_controls/` — one module per control (12 controls across 3 tiers), each exposing `check(stmt, ctx, rubric) -> ControlResult`. `_controls/__init__.py` holds the `ControlResult` and `LintContext` dataclasses, the `control_meta` resolver (rubric overrides tier/name/severity), and the `all_controls()` registry; `_controls/_helpers.py` holds shared pattern/column helpers. Controls type-hint `ParsedStatement` under `TYPE_CHECKING` only, so importing a control never pulls in `sqlglot`.
- `sql-guardrail-linter/scripts/_scoring.py` — single source of truth for the severity-weighted score formula, the per-statement `violations` counts, the batch worst-first ordering, the fleet `summary`, and the severity-then-bytes `top_fixes` ranking.
- `sql-guardrail-linter/scripts/_metadata.py` — BigQuery resolver, lazy-imports `google.cloud.bigquery`. `NullResolver` is the static-mode no-op; `BigQueryMetadataResolver` reads `TimePartitioning.field` (SQ-004), resolves restricted columns from policy tags or name patterns (SQ-010), and dry-runs statements for bytes (SQ-008). Tests inject a `LintContext` with metadata directly, so no client is ever constructed in CI.
- `sql-guardrail-linter/scripts/_suggest`-style suggestions live inline on each `ControlResult` (the control that fails emits its own `suggestion`), not in a separate module; `_scoring` lifts them into `top_fixes`.
- `sql-guardrail-linter/scripts/render_report.py` — single self-contained file that is both the Markdown/HTML renderer (imported by `lint_sql.py`) and the Path A CLI, like the MCP linter's `render_scorecard.py`. No BigQuery dependency.

### Never-execute invariant (SQL guardrail linter only)

The SQL guardrail linter audits SQL but must **never execute** the statement under audit. `cost_aware` / `usage_log` cost comes from a dry-run only: `_metadata.BigQueryMetadataResolver.dry_run` always sets `QueryJobConfig(dry_run=True, use_query_cache=False)`, and Path A calls `mcp__bigquery__execute_sql` with `dry_run: true`. The CLI only dry-runs read-only SELECTs (non-SELECT statements get `estimated_bytes=None` and SQ-008 `na`). The usage-log fetch query (which reads the log table, not the audited SQL) is the one query run for real, and it is dry-run-guarded against the ceiling first. Don't add a path that executes audited SQL.

### Cost-safety invariant (evaluator only)

Every data-scanning query in `bigquery-table-evaluator-skill` Path B goes through `run_query_with_guard`: dry-run first, compare `total_bytes_processed` against `--max-bytes-billed`, and either skip with `status: "skipped_estimate_exceeds_cap"` or run with `maximum_bytes_billed` enforced. Path A reproduces this by calling `mcp__bigquery__execute_sql` with `dry_run: true` first. Don't add a new data check that bypasses this guard.

`score-table-metadata-skill` only reads metadata (no SQL, no scans) — no cost guard needed. `mcp-schema-linter` reads only tool definitions and never invokes a tool, so it has no scan cost either; its only outbound calls are the `tools/list` fetch and, optionally, the `claude` tokenizer's `count_tokens` request.

### Identifier validation

Table IDs, dataset IDs, and column names are validated against tight regexes in `_validation.py` before being interpolated into SQL or used in API calls (always wrapped in backticks for SQL). The evaluator's `--where` clause uses a denylist for backticks, semicolons, and SQL comments — best-effort guard, not a parser, so callers remain responsible for valid SQL.

### Rubric semantics (scorer only)

The metadata-scoring rubric in `_rubric.py` distinguishes **always-applicable** column criteria (`has_description`, `not_type_echo`) from **conditionally-applicable** criteria that only count toward a column's max when the column's name matches a trigger pattern (`coded_field_explained`, `units_or_format`, `sensitivity_flagged`). The `caveats_present` criterion is bonus — it only contributes to a column's max when the description actually contains a caveat phrase. This means a non-coded, non-measure, non-sensitive column has a max of 4 (the two always-on criteria), so it isn't penalized for not being something it isn't. Aggregation normalizes by `points / max` per column, so the scoring is fair across heterogeneous schemas.

### Tests

The two BigQuery skills' `tests/conftest.py` installs stub modules for `google.cloud.bigquery` and `google.api_core.exceptions` so the suite runs with no GCP setup. Tests load internal modules directly via per-module pytest fixtures — when adding a new internal module to `scripts/`, add a matching fixture there.

The MCP linter's `tests/conftest.py` stubs nothing (no heavy dependency); it only puts `scripts/` on `sys.path` and exposes a `rubric` fixture. Tests that exercise the `cl100k_base` tokenizer or the full scoring pipeline are guarded with a `needs_tiktoken` skip marker so the suite still runs without `tiktoken`; the `claude` tokenizer and the URL fetcher are tested against a stubbed `requests` module.

The SQL guardrail linter's `tests/conftest.py` also stubs nothing: `sqlglot` is a real test dependency (the parser), and `cost_aware` behavior is tested by injecting a `LintContext` with metadata directly rather than constructing a BigQuery client, so the suite runs fully offline with no GCP setup.

Test files mirror `scripts/` one-to-one: `tests/test_<module>.py` corresponds to `scripts/_<module>.py` (e.g., `test_validation.py` ↔ `_validation.py`, `test_rubric.py` ↔ `_rubric.py`). The CLI entrypoints have their own test files. For the MCP linter, `test_rules.py` covers all 15 rule modules collectively (pass / partial / fail / N/A branches), and `test_render.py` covers `render_scorecard.py`. For the SQL guardrail linter, `test_controls.py` covers all 12 control modules (pass / warn / fail / na branches), `test_parser.py` covers the `sqlglot` analysis and splitter, and `test_lint_sql.py` exercises the CLI end-to-end in static mode (gates, exit codes, rubric provenance).
