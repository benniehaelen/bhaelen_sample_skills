---
name: sql-guardrail-linter
description: Use this skill when a user wants to lint, audit, score, or grade SQL produced by a natural-language-to-SQL pipeline (or any generated SQL) against a guardrail control catalog before it runs. It checks each statement for read-only safety, single-statement safety, partition-filter presence, scan cost within a ceiling, cartesian joins, SELECT *, unbounded results, tenant or zone predicates, PII or PHI column access, fully qualified table names, and ORDER BY without LIMIT. Produces a 0-100 score and letter grade A-F per statement, a per-control pass/warn/fail finding with evidence and a concrete fix, and a top-fixes list ranked by severity and estimated bytes saved. Accepts a single SQL string, a file of statements, or a batch pulled from a usage-log table. The linter never executes the SQL it audits; cost is measured with dry-run only. Optional CI-style gates (--expect-no-violations-at, --expect-min-score, --expect-max-bytes) exit non-zero on failure.
---

# SQL Guardrail Linter

## Purpose
Lint generated SQL against a guardrail control catalog and produce a compact safety report before the statement is allowed to run. The headline question is not "is this query well written" but "would this query be allowed through the control plane." The skill never executes the SQL it audits. It parses each statement structurally and, when a BigQuery connection is available, measures scan cost with a dry-run and resolves partition, tenant, and sensitivity controls from table metadata. It produces a per-statement 0-100 score, an A-F grade, a per-control finding with evidence and a concrete fix, and a violations summary that drives the CI gate.

## Required inputs
One of:
- `sql`: a single SQL statement as a string.
- `sql_file`: a path to a file containing one or more statements (separated by `;`).
- `usage_log`: a fully qualified `project.dataset.table` whose rows contain logged statements to audit over a window. Requires `usage_log_sql_column` and a time bound (see optional inputs).

## Optional inputs
- `mode`: `static` (parse only, no BigQuery), `cost_aware` (adds dry-run cost and metadata-resolved controls), or `usage_log` (pull statements from a log table, then run `cost_aware` on each). Default is `cost_aware` when a BigQuery connection is available, otherwise `static`.
- `dialect`: SQL dialect for parsing. Default `bigquery`.
- `max_bytes_ceiling`: scan-cost ceiling for `SQ-008`, as a byte count or a human value like `50GB`. A statement whose dry-run estimate exceeds this fails the control.
- `tenant_registry`: a JSON map of `project.dataset.table` to the tenant or zone column that must be constrained (for example `{"proj.clinical.encounter": "coid"}`). Drives `SQ-009`. Tables not listed are treated as not tenant-scoped.
- `pii_policy`: how PII or PHI columns are identified for `SQ-010`. One of `policy_tags` (read BigQuery policy tags), `labels`, or a JSON map of table to a list of restricted columns. In `static` mode, only the explicit JSON map is available.
- `pii_allowlist`: columns or callers permitted to read restricted columns, so a justified access does not fail `SQ-010`.
- `usage_log_sql_column`: the column in `usage_log` holding the statement text.
- `since` / `until`: time bounds for `usage_log` mode (for example `since=7d`). Applied to the log table's timestamp column via `usage_log_time_column`.
- `usage_log_time_column`: timestamp column used to bound the window. Required for `usage_log` mode.
- `rubric_config`: path to a JSON file overriding the rubric data (control weights, severities, the ceiling default, grade cutoffs, trigger lists). Omitted sections fall back to built-in defaults. See "Configuring the rubric" below.
- HTML theme: `auto` (default) / `light` / `dark`.
- Expectations (any combination; exits with code 3 if any fail):
  - `expect_min_score`: integer 0-100; fails if any statement scores below it.
  - `expect_no_violations_at`: minimum severity that fails the gate (`critical`, `high`, `medium`, `low`). Default `high`, meaning any control failure at `high` or `critical` fails the gate.
  - `expect_max_bytes`: per-statement dry-run ceiling for the gate, independent of the scoring ceiling. Requires `cost_aware` or `usage_log` mode.

## Operating rules
1. Never execute the SQL under audit. Use dry-run only to estimate cost and resolve referenced tables. The bundled script and Path A both enforce this.
2. Never interpolate unvalidated identifiers. Validate `project.dataset.table` for the usage-log source and any registry keys, then wrap identifiers in backticks. Reject anything that does not match `^[A-Za-z0-9_-]+$` / `^[A-Za-z0-9_]+$` / `^[A-Za-z0-9_*$-]+$`.
3. Parse, do not pattern-match, when deciding statement type, joins, projected columns, and `WHERE` predicates. Use a real parser (`sqlglot`) so that comments and string literals do not produce false controls.
4. A control that cannot be resolved in the current mode is recorded as `na`, not as a pass. For example, `SQ-008` (scan ceiling) is `na` in `static` mode, and `SQ-004` (partition filter) is `na` for a table whose partition column is unknown.
5. Keep reports concise: one card per statement, controls grouped by tier, worst-first ordering.
6. Suggestions are drafts the author can paste in with minimal editing. Reference the real columns and tables in the statement. Never fabricate a tenant column, partition column, or source system that the inputs do not establish.

## Rubric (v1.0)

Each control yields `pass`, `warn`, or `fail`, carries a severity, and contributes a weight to the score. The score is a quality lens for trend tracking. The gate is driven by severity, because in a governed platform a single blocking violation should stop the statement regardless of how clean the rest of it is.

Default severity weights (configurable): `critical = 5`, `high = 3`, `medium = 2`, `low = 1`. A `warn` earns half the control's weight; a `fail` earns zero; an `na` control is removed from the denominator.

### Tier 1: statement safety (the controls that block unconditionally)

- `SQ-001` read_only_select (critical): the statement is a single read-only `SELECT` or `WITH ... SELECT`. Any `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `DROP`, `ALTER`, `TRUNCATE`, or `GRANT` fails.
- `SQ-002` single_statement (critical): exactly one statement. Stacked statements or trailing separators that introduce a second statement fail.
- `SQ-003` no_comment_injection (high): no comment block or query hint used to smuggle directives or disable a control.

### Tier 2: cost and scan control

- `SQ-004` partition_filter_present (high, conditional): a query that reads a partitioned table constrains the partition column in `WHERE`. Conditional on a referenced table being partitioned. `na` when no referenced table is partitioned or the partition column is unknown.
- `SQ-005` no_select_star (medium): columns are enumerated; no `SELECT *` or `SELECT t.*`.
- `SQ-006` bounded_result (medium): a non-aggregating query carries a `LIMIT`, or the query is an aggregation. Pure scans with no bound `warn`.
- `SQ-007` no_cartesian_join (high): every join has an `ON` or `USING` predicate. A `CROSS JOIN` or a comma-join without a predicate fails.
- `SQ-008` scan_within_ceiling (high, conditional): the dry-run estimate is within `max_bytes_ceiling`. `na` in `static` mode.

### Tier 3: governance and hygiene

- `SQ-009` tenant_predicate_present (critical, conditional): a query that reads a tenant-scoped or zoned table constrains the tenant column from `tenant_registry`. `na` for tables not in the registry.
- `SQ-010` pii_access_justified (critical, conditional): no restricted (PII or PHI) column is projected unless permitted by `pii_allowlist`. Conditional on the projection touching a restricted column.
- `SQ-011` fully_qualified_tables (low): every base table is written as `project.dataset.table`. Unqualified or two-part names fail, because they can bypass zone routing.
- `SQ-012` order_by_bounded (low): an `ORDER BY` is paired with a `LIMIT`.

### Scoring formula

For each statement:

- `applicable_weight = sum(weight(c) for c in controls if status(c) != "na")`
- `earned = sum(weight(c) if pass, 0.5 * weight(c) if warn, 0 if fail)`
- `score = round(100 * earned / applicable_weight)`
- Default letter grades: 90+ A, 80-89 B, 70-79 C, 60-69 D, below 60 F. Cutoffs are configurable.

For a batch (`sql_file` or `usage_log`), the report lists statements worst-first. The fleet headline metrics are the count of statements with at least one violation at or above the gate severity, and the total estimated bytes across statements (in `cost_aware` and `usage_log` modes).

### Configuring the rubric

The rubric data (severities, weights, the ceiling default, grade cutoffs, the comment-injection and PII trigger lists) can be overridden by a JSON config. The control logic (what counts as a pass, warn, or fail) is fixed in code. Pass the file via `--rubric-config <path>` to the bundled CLI; Path A agents should read the file and respect its contents.

Sections you omit fall back to built-in defaults, so a partial config that only raises a severity is valid. The shipped `examples/rubric_default.json` reproduces the built-in behavior and is a useful starting template.

```jsonc
{
  "name": "nl2sql-guardrail-default",
  "version": "1.0",
  "severity_weights": {"critical": 5, "high": 3, "medium": 2, "low": 1},
  "grade_cutoffs": {"A": 90, "B": 80, "C": 70, "D": 60},   // strictly decreasing, in [0,100]
  "max_bytes_ceiling": 53687091200,                         // 50 GB default for SQ-008
  "controls": {                                             // override severity per control id
    "SQ-009": {"severity": "critical"},
    "SQ-011": {"severity": "medium"}
  },
  "comment_injection_patterns": ["...regex...", "..."],     // patterns that fail SQ-003
  "pii_column_patterns": ["(^|_|\\.)ssn(_|$)", "..."]       // fallback PII triggers when no policy tags
}
```

Provenance is stamped into the report under the top-level `rubric_config` key (`source`, `name`, `version`, `sha256`), and the renderer surfaces it in the subtitle so reviewers can tell which rubric was applied.

## Implementation

Three modes, two execution paths. `static` mode needs no BigQuery at all and runs anywhere with Python and `sqlglot`. `cost_aware` and `usage_log` modes need a BigQuery connection. Prefer Path A when the BigQuery MCP connector is available, since it works in any Claude environment without local Python deps or `gcloud` auth.

### Path A: BigQuery MCP connector (preferred for cost_aware and usage_log)

Requires the [Google GenAI Toolbox](https://github.com/googleapis/genai-toolbox) MCP server registered with the `--prebuilt bigquery` configuration. Tool names below assume the server entry is named `bigquery`. If it is named differently, the prefix changes accordingly (for example `mcp__<server-name>__execute_sql`).

**Step 1: collect the statements.**
- For `sql` or `sql_file`, read the text directly. Split on `;` at statement boundaries, ignoring separators inside string literals and comments.
- For `usage_log` mode, fetch the logged statements with a single bounded query. Validate the table ID and column names first, then run with `dry_run: true` to confirm the scan is within budget before running for real:
  ```sql
  SELECT `{sql_col}` AS sql_text, `{time_col}` AS logged_at
  FROM `{project}.{dataset}.{table}`
  WHERE `{time_col}` >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {n} {unit})
  ```

**Step 2: parse each statement.** Determine the statement type, the base tables, the projected columns, the join predicates, the `WHERE` predicates, and the presence of `LIMIT` and `ORDER BY`. This resolves the unconditional controls (`SQ-001`, `SQ-002`, `SQ-003`, `SQ-005`, `SQ-006`, `SQ-007`, `SQ-011`, `SQ-012`).

**Step 3: resolve the conditional controls from metadata (cost_aware and usage_log only).**
- For each base table, call `mcp__bigquery__get_table_info(project=..., dataset=..., table=...)`. Read `TimePartitioning.field` for `SQ-004`, and the schema's policy tags or descriptions for `SQ-010` (subject to `pii_policy`).
- For `SQ-009`, look the table up in `tenant_registry`; if present, check whether the resolved tenant column appears in the statement's `WHERE` predicates.
- For `SQ-008`, dry-run the statement under audit to read `totalBytesProcessed`, and compare to `max_bytes_ceiling`. Always pass `dry_run: true`. Never run the audited statement for real.

**Step 4: assemble the report dict.** The shape must match what `scripts/render_report.py` expects:

```json
{
  "rubric_version": "1.0",
  "rubric_config": {"source": "builtin", "name": "nl2sql-guardrail-default", "version": "1.0", "sha256": ""},
  "linted_at": "2026-06-03T16:20:00+00:00",
  "mode": "cost_aware",
  "scope": {"source": "usage_log", "table": "project.ops.mcp_usage_log", "window": "7d", "statement_count": 42},
  "statements": [
    {
      "statement_id": "stmt_0007",
      "source": "mcp_usage_log @ 2026-06-02T09:14:00+00:00",
      "sql_preview": "SELECT * FROM `proj.clinical.encounter` WHERE admit_date >= '2026-05-01'",
      "score": 41,
      "grade": "F",
      "statement_type": "SELECT",
      "estimated_bytes": 41203847168,
      "estimated_bytes_human": "38.37 GiB",
      "referenced_tables": ["proj.clinical.encounter"],
      "controls": [
        {"id": "SQ-001", "name": "read_only_select", "tier": 1, "severity": "critical", "status": "pass", "evidence": "single SELECT"},
        {"id": "SQ-005", "name": "no_select_star", "tier": 2, "severity": "medium", "status": "fail", "evidence": "SELECT * over 38 columns",
         "suggestion": "Enumerate the columns the caller needs, for example encounter_id, admit_date, coid."},
        {"id": "SQ-009", "name": "tenant_predicate_present", "tier": 3, "severity": "critical", "status": "fail", "evidence": "no predicate on coid",
         "suggestion": "Add a zone filter: AND coid = @caller_coid."},
        {"id": "SQ-008", "name": "scan_within_ceiling", "tier": 2, "severity": "high", "status": "fail", "evidence": "38.37 GiB exceeds the configured ceiling",
         "suggestion": "Add a partition filter on admit_date to prune the scan."}
      ],
      "violations": {"critical": 1, "high": 1, "medium": 1, "low": 0}
    }
  ],
  "top_fixes": [
    {"statement_id": "stmt_0007", "control": "SQ-009", "severity": "critical", "impact": "blocks the statement", "fix": "Add coid predicate."},
    {"statement_id": "stmt_0007", "control": "SQ-008", "severity": "high", "estimated_bytes_saved": 40000000000, "fix": "Add admit_date partition filter."}
  ],
  "expectations": [],
  "warnings": []
}
```

The `score`, `grade`, per-statement `violations` counts, and the `top_fixes` ranking must all be self-consistent with the `controls` list. Compute them; do not invent. Rank `top_fixes` by severity first, then by `estimated_bytes_saved` where a dry-run is available.

**Step 5: render.** Write the assembled dict to JSON and run:

```bash
python scripts/render_report.py --input report.json --output-md report.md --output-html report.html --theme auto
```

Pass any expectation flags (`--expect-min-score`, `--expect-no-violations-at`, `--expect-max-bytes`) to evaluate the gate and exit `3` if it fails. The renderer is the single source of truth for the Markdown and HTML output; never hand-format these from the JSON. `render_report.py` has no BigQuery dependency, so Path A works in any environment with Python and the skill files.

### Path B: bundled Python script (fallback)

Use this in a local Claude Code session, or whenever the MCP connector is not available. `static` mode needs only `sqlglot`. `cost_aware` and `usage_log` modes also need `google-cloud-bigquery` and `gcloud auth application-default login`.

Static lint of a single statement (no BigQuery):

```bash
python scripts/lint_sql.py \
  --sql "SELECT * FROM analytics.events WHERE event_date = '2026-06-01'" \
  --mode static \
  --output-json report.json --output-md report.md
```

Cost-aware lint of a file, with a registry and a gate:

```bash
python scripts/lint_sql.py \
  --sql-file candidate_queries.sql \
  --mode cost_aware \
  --max-bytes-ceiling 10GB \
  --tenant-registry tenants.json \
  --pii-policy policy_tags \
  --expect-no-violations-at high \
  --output-json report.json --output-md report.md --output-html report.html
```

Usage-log audit over the last seven days:

```bash
python scripts/lint_sql.py \
  --usage-log my-project.ops.mcp_usage_log \
  --usage-log-sql-column statement_text \
  --usage-log-time-column logged_at \
  --since 7d \
  --mode usage_log \
  --max-bytes-ceiling 10GB \
  --tenant-registry tenants.json \
  --expect-min-score 70 \
  --output-json report.json --output-md report.md
```

The bundled script and `render_report.py` produce byte-identical Markdown and HTML for the same JSON shape. Path A grades more accurately because it can read intent (for example distinguishing a justified PII access from an accidental one); both paths produce the same JSON shape.

## Output
- JSON report for machine consumption and as input to `render_report.py`.
- Markdown report for human review: one section per statement, controls grouped by tier, a top-fixes list, and an expectations status row.
- Optional self-contained HTML dashboard (`--output-html report.html`) with score badges, A-F grade pills, per-control pass/warn/fail/na pills, a per-statement estimated-bytes bar, and a collapsible raw-SQL panel. No external assets, no JavaScript. Pass `--theme {auto,light,dark}` (default `auto`, which follows the viewer's OS preference).

## Authentication
- **Path A**: handled by the MCP server (it picks up `GOOGLE_APPLICATION_CREDENTIALS` and `BIGQUERY_PROJECT` from its own environment).
- **Path B**: `static` mode needs no credentials. `cost_aware` and `usage_log` modes use Google Application Default Credentials, or any environment accepted by the `google-cloud-bigquery` client library.

## Exit codes
- `0`: clean (all statements linted, all expectations passed if any).
- `1`: BigQuery API error.
- `2`: input or validation error (including a statement that fails to parse).
- `3`: expectation failure (a gate flag was set and not met).
