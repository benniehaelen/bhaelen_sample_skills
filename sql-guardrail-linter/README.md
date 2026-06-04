# SQL Guardrail Linter Skill

Lint, audit, score, and grade generated SQL against a guardrail control catalog **before it runs**. The headline question is not "is this query well written" but **"would this query be allowed through the control plane."** Built for natural-language-to-SQL pipelines, where a model proposes SQL and a governed platform has to decide whether the statement is safe to execute.

The linter **never executes the SQL it audits.** It parses each statement structurally (with a real parser, not regexes) and, when a BigQuery connection is available, measures scan cost with a dry-run and resolves partition, tenant, and sensitivity controls from table metadata. It produces a per-statement 0-100 score, an A-F grade, a per-control pass/warn/fail/na finding with evidence and a concrete fix, and a top-fixes list ranked by severity and estimated bytes saved.

The score is a quality lens for trend tracking. The **gate is driven by severity**, because in a governed platform a single blocking violation should stop the statement regardless of how clean the rest of it is.

There are two ways to run it:

- **[Using with Claude](#using-with-claude-recommended)**: ask Claude in plain English. Claude reads the rubric, collects the statements, grades each control (reading intent, for example distinguishing a justified PII access from an accidental one), and writes the report files.
- **[Run locally with Python](#run-locally-with-python-path-b)**: bundled CLI that parses and grades deterministically. `static` mode needs only `sqlglot`; `cost_aware` and `usage_log` modes add a BigQuery connection. Useful for CI pipelines or pre-flight checks in the control plane itself.

Both paths produce the same JSON / Markdown / HTML output shape and share the same renderer.

## The control catalog

Twelve controls across three tiers. Each yields `pass`, `warn`, `fail`, or `na`, carries a severity, and contributes a weight to the score.

| ID | Control | Tier | Severity | Conditional |
| --- | --- | :---: | --- | :---: |
| SQ-001 | read_only_select | 1 | critical | |
| SQ-002 | single_statement | 1 | critical | |
| SQ-003 | no_comment_injection | 1 | high | |
| SQ-004 | partition_filter_present | 2 | high | yes |
| SQ-005 | no_select_star | 2 | medium | |
| SQ-006 | bounded_result | 2 | medium | |
| SQ-007 | no_cartesian_join | 2 | high | |
| SQ-008 | scan_within_ceiling | 2 | high | yes |
| SQ-009 | tenant_predicate_present | 3 | critical | yes |
| SQ-010 | pii_access_justified | 3 | critical | yes |
| SQ-011 | fully_qualified_tables | 3 | low | |
| SQ-012 | order_by_bounded | 3 | low | |

A conditional control that cannot be resolved in the current mode is recorded as `na`, not as a pass, and is dropped from the score denominator. For example, `SQ-008` (scan ceiling) is `na` in `static` mode, and `SQ-004` (partition filter) is `na` for a table whose partition column is unknown.

### Scoring

Default severity weights: `critical = 5`, `high = 3`, `medium = 2`, `low = 1`. A `warn` earns half the control's weight; a `fail` earns zero; an `na` control is removed from the denominator.

```
applicable_weight = sum(weight(c) for c in controls if status(c) != "na")
earned            = sum(weight(c) if pass, 0.5 * weight(c) if warn, 0 if fail)
score             = round(100 * earned / applicable_weight)
```

Default grades: 90+ A, 80-89 B, 70-79 C, 60-69 D, below 60 F. Weights, severities, the ceiling default, grade cutoffs, and the trigger lists are all configurable via `--rubric-config` (see [`examples/rubric_default.json`](examples/rubric_default.json), which reproduces the built-in behavior).

## Using with Claude (recommended)

Claude reads `SKILL.md`, collects the statements, grades each control against the guardrail rubric, and writes the report files. The agent path grades more accurately because it can read intent. For `cost_aware` and `usage_log` modes it uses the BigQuery MCP connector to dry-run the statement and read table metadata; it always passes `dry_run: true` and never executes the audited SQL.

### Install the skill

#### Claude Code

```bash
# User-level (available across all projects)
cp -r sql-guardrail-linter ~/.claude/skills/sql-guardrail-linter

# Or project-level (only this project)
mkdir -p .claude/skills && cp -r sql-guardrail-linter .claude/skills/sql-guardrail-linter
```

The **directory name becomes the slash-command name**, so keep it as `sql-guardrail-linter` to match the `name:` in `SKILL.md`'s frontmatter and invoke it as `/sql-guardrail-linter`. Claude Code hot-reloads skills inside an active session.

#### Claude.ai

Open **Workspace settings, then Custom skills** (or **Team settings** for org-wide installation) and upload the skill directory as a `.zip`.

### Example prompts

- *"Would this query pass the guardrails before I run it? `SELECT * FROM clinical.encounter WHERE admit_date >= '2026-05-01'`"*
- *"Lint these candidate queries and fail if any has a high-or-critical violation: `./candidate_queries.sql`."*
- *"Audit the last 7 days of generated SQL in `proj.ops.mcp_usage_log` against a 10 GB scan ceiling and a tenant registry."*
- *"Score this statement and tell me which fix recovers the most scanned bytes."*

You can also explicitly invoke `/sql-guardrail-linter` in Claude Code.

## Run locally with Python (Path B)

The bundled CLI parses each statement and grades deterministically. Grading is structural rather than semantic, so suggestions are templates rather than statement-specific drafts. The JSON shape, renderer, and CLI flags are identical to Path A.

### Install

```bash
python -m venv .venv
source .venv/bin/activate          # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt    # static mode: just sqlglot
# cost_aware / usage_log modes also need:
pip install google-cloud-bigquery
gcloud auth application-default login
```

`static` mode has a single dependency (`sqlglot`). The renderer itself has no third-party dependencies, so Path A works in any environment with Python and the skill files.

### Static lint of a single statement (no BigQuery)

```bash
python scripts/lint_sql.py \
  --sql "SELECT * FROM analytics.events WHERE event_date = '2026-06-01'" \
  --mode static \
  --output-json report.json --output-md report.md
```

### Cost-aware lint of a file, with a registry and a gate

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

### Usage-log audit over the last seven days

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

See [`examples/lint_queries.sh`](examples/lint_queries.sh) for a runnable static example over the bundled [`candidate_queries.sql`](examples/candidate_queries.sql).

### CI gates

Any combination of these exits `3` when the gate is not met:

- `--expect-min-score N` fails if any statement scores below `N`.
- `--expect-no-violations-at SEVERITY` fails if any statement has a control failure at or above `SEVERITY` (`critical` / `high` / `medium` / `low`).
- `--expect-max-bytes VALUE` fails if any statement's dry-run estimate exceeds `VALUE` (requires `cost_aware` or `usage_log`).

### Render an existing report

`render_report.py` is the single source of truth for Markdown and HTML, with no BigQuery dependency. Feed it a JSON report (from either path) to regenerate the human-facing files, optionally re-running the gates:

```bash
python scripts/render_report.py \
  --input report.json \
  --output-md report.md --output-html report.html --theme auto \
  --expect-no-violations-at high
```

## Exit codes

- `0` clean (all statements linted, all expectations passed if any).
- `1` BigQuery API error.
- `2` input or validation error (including a statement that fails to parse).
- `3` expectation failure (a gate flag was set and not met).

## Output

- **JSON** for machine consumption and as input to `render_report.py`.
- **Markdown** for human review: one section per statement, controls grouped by tier, a top-fixes list, and an expectations status row.
- Optional self-contained **HTML** dashboard with score badges, A-F grade pills, per-control pass/warn/fail/na pills, a per-statement estimated-scan bar, and a collapsible SQL panel. No external assets, no JavaScript. `--theme {auto,light,dark}` (default `auto`, which follows the viewer's OS preference).

See [`examples/`](examples/) for a rendered sample ([`sample_report.md`](examples/sample_report.md), [`sample_report_auto.html`](examples/sample_report_auto.html)). Regenerate them with `python examples/render_sample_report.py`.

## Development

```bash
pip install -r requirements-dev.txt
pytest tests/
```

The test suite runs offline with no BigQuery setup: `static` mode and the parser/controls/scoring/renderer are exercised directly, and `cost_aware` behavior is tested by injecting a `LintContext` with metadata rather than constructing a client.

## License

[MIT](../LICENSE)
