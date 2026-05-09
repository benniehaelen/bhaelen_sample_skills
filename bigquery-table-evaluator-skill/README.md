# BigQuery Table Evaluator Skill

A small skill for evaluating a Google BigQuery table. It can run metadata-only checks, or optional data-quality checks with BigQuery dry-run cost protection.

There are two ways to run it:

- **[Using with Claude](#using-with-claude-recommended)** — ask Claude in plain English. Claude reads the skill instructions, fetches metadata via a BigQuery MCP server, runs the data checks (with dry-run cost protection), and writes the report files. Recommended for most users.
- **[Run locally with Python](#run-locally-with-python-path-b)** — bundled CLI script that uses `google-cloud-bigquery` directly. Useful when an MCP server isn't available, or for CI pipelines that need deterministic output.

Both paths produce the same JSON / Markdown / HTML output shape and share the same renderer.

## Using with Claude (recommended)

Claude reads `SKILL.md`, fetches BigQuery metadata via a registered MCP connector, optionally runs data-scanning queries (with a dry-run cost cap you specify), and writes the report files. No local Python install required — you only need the skill directory and a BigQuery MCP server registered with your Claude environment.

### Install the skill

#### Claude Code

Drop the skill directory into one of:

```bash
# User-level (available across all projects)
cp -r bigquery-table-evaluator-skill ~/.claude/skills/bigquery-table-evaluator

# Or project-level (only this project)
mkdir -p .claude/skills && cp -r bigquery-table-evaluator-skill .claude/skills/bigquery-table-evaluator
```

The **directory name becomes the slash-command name** — keep it as `bigquery-table-evaluator` so the skill is invoked as `/bigquery-table-evaluator` and matches the `name:` in `SKILL.md`'s frontmatter. Claude Code hot-reloads skills inside an active session.

#### Claude.ai

Open **Workspace settings → Custom skills** (or **Team settings** for org-wide installation), then upload the skill directory as a `.zip`.

### Set up the BigQuery MCP server

Same MCP server as the [score-table-metadata skill](../score-table-metadata-skill/) — set it up once and both skills will use it. The skill expects the [Google GenAI Toolbox](https://github.com/googleapis/genai-toolbox) configured with `--prebuilt bigquery`, exposing `mcp__bigquery__get_table_info`, `mcp__bigquery__execute_sql`, and `mcp__bigquery__list_table_ids`.

**Claude Code** — register the MCP server once, user-wide:

```bash
# Install the toolbox per its README, then register with Claude Code:
claude mcp add bigquery -- toolbox serve --prebuilt bigquery
# Authenticate the toolbox via Application Default Credentials:
gcloud auth application-default login
```

Verify with `claude mcp list` — `bigquery` should appear. Test by asking Claude *"List the datasets in `<your-project>`."*

**Claude.ai** — Workspace MCP settings → add the BigQuery MCP using your workspace's auth model (typically a service-account JSON).

### Cost safety: always specify a scan budget

Data checks (freshness, duplicate keys, null profiling, sample rows) scan table data and **incur BigQuery costs**. The skill does a dry-run first and skips a check if the estimated scan exceeds a cap you specify. **Always tell Claude a scan budget when asking for data checks** — e.g., *"with a 1 GiB scan cap"* or *"max 100 MiB scanned per check"*.

When no budget is mentioned, Claude defaults to metadata-only checks (no data scan, no cost). That's safe but skips freshness, duplicates, and null profiling.

### Example prompts

- *"Audit the BigQuery table `acme.events.raw` — metadata only, no data scan."*
- *"Run a full health check on `acme.warehouse.orders` with a 1 GiB scan cap. Use `event_timestamp` for freshness and `order_id` for duplicate detection."*
- *"Evaluate `acme.warehouse.users` and fail the run if the table has fewer than 10,000 rows or any duplicate `user_id` values. Cap data scans at 500 MiB."*
- *"Check freshness on `acme.events.clickstream`. The latest `event_ts` should be within 24 hours; alert me if it isn't."*
- *"Profile null rates for the columns in `acme.warehouse.orders` and fail if `user_id` is more than 0% null or `referrer` is more than 5% null. Cap scans at 1 GiB."*
- *"Diff `acme.warehouse.orders` against last week's report at `./report-prev.json` — flag any schema drift."*

You can also explicitly invoke the skill with `/bigquery-table-evaluator` in Claude Code if Claude doesn't pick it up automatically.

### What you get back

Up to three files in your working directory (file names configurable in the prompt):

- **`report.json`** — machine-readable health report. Metadata, schema, partition/clustering stats, freshness, duplicate keys, per-column null rates and approximate distinct counts, expectation results, and any warnings.
- **`report.md`** — Markdown summary suitable for review or PR comments.
- **`report.html`** — self-contained dashboard (no JavaScript, no external assets) with an SVG bar chart of per-column null rates, status pills for each expectation, a partition-stats card, and a collapsible schema table.

A pre-rendered example lives in [`examples/sample_dashboard_light.html`](examples/sample_dashboard_light.html) (also `_dark` and `_auto`). Open it to see what a finished dashboard looks like before running against your own table.

### What runs where

In Path A, **Claude does the metadata fetch, dry-run cost check, and report assembly itself** by following `SKILL.md` and calling the MCP server. The bundled Python isn't a check engine that Claude shells out to — Claude calls `mcp__bigquery__execute_sql` (with `dry_run: true` first to enforce your scan cap) for each data check, then `mcp__bigquery__execute_sql` for real, and assembles the JSON report dict in its own context.

The only Python that runs in Path A is the **renderer**: `scripts/render_report.py` (and its imports, `_render.py` + `_serialize.py`). It's a pure JSON-in / Markdown+HTML-out transformer with no BigQuery dependency and no third-party packages. After writing the JSON, Claude calls it once:

```bash
python scripts/render_report.py \
  --input report.json --output-md report.md --output-html report.html --theme auto
```

| File | Path A (Claude) | Path B (CLI) |
| --- | --- | --- |
| `scripts/render_report.py` + `_render.py` + `_serialize.py` | runs (renderer) | runs |
| `scripts/evaluate_bigquery_table.py` (CLI entry) | not used | runs |
| `scripts/_expectations.py` (expectation + drift evaluator) | not used — Claude evaluates from `SKILL.md` | runs |
| `scripts/_validation.py` | not used | runs |

**Practical implications:**

- Path A still needs Python somewhere — the renderer has to run. In **Claude Code**, it runs on your local machine via the Bash tool (stdlib only, no `pip install` needed). In **Claude.ai**, it runs in the Python analysis sandbox.
- If you only need the JSON output, no Python is required at all — Claude writes that file directly.
- **Cost-safety in Path A is enforced by SKILL.md, not the Python guard.** The CLI's `run_query_with_guard()` (in `evaluate_bigquery_table.py`) is the Path B mechanism. Path A reproduces the dry-run-first pattern by following SKILL.md's instructions to call `mcp__bigquery__execute_sql` with `dry_run: true` and check the estimated bytes before running for real. So you still need to *tell Claude a scan budget* in your prompt — Claude won't invent one.
- The renderer is the contract: both paths feed the *same* renderer with the same JSON shape (documented in `SKILL.md`), so the Markdown and HTML outputs are identical regardless of which path produced the JSON.

### Troubleshooting

- **Claude doesn't seem to know about the skill.** Confirm `SKILL.md` sits at the *root* of the skill directory (not nested under `scripts/`). Run `/help` in Claude Code, or check the Custom skills panel in Claude.ai workspace settings.
- **Claude says it doesn't have BigQuery access.** The MCP server isn't reachable. Run `claude mcp list` (Claude Code) or check workspace MCP settings (Claude.ai). Test with *"List the datasets in `<project>`"* — if that fails, fix the MCP before re-running the skill.
- **A check came back as `skipped_estimate_exceeds_cap`.** Your scan budget was too tight for the table. Either raise the cap (e.g., 5 GiB instead of 1 GiB) or constrain the scan with a WHERE clause: *"Restrict data checks to `event_date >= '2026-01-01'`."*
- **Expectations didn't fire.** Make sure you stated thresholds explicitly in the prompt. Claude won't invent business thresholds — say *"fail if rows < 10,000"* rather than *"check the row count"*.
- **The same column gets profiled every time even though I asked Claude to skip it.** Tell Claude which columns to profile or skip explicitly: *"Profile only `user_id` and `event_id` for nulls."*
- **I have local BigQuery access but no MCP server.** Use the Python path below.

## Run locally with Python (Path B)

If a BigQuery MCP server isn't available — for example, in CI pipelines or on a workstation without one configured — the skill ships a deterministic Python implementation that uses `google-cloud-bigquery` directly. The JSON shape, renderer, and CLI flags match what Claude produces via Path A.

### Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
gcloud auth application-default login
```

### Metadata-only evaluation

```bash
python scripts/evaluate_bigquery_table.py \
  --table my-project.analytics.events \
  --output-json report.json \
  --output-md report.md
```

### Data checks with a scan cap

```bash
python scripts/evaluate_bigquery_table.py \
  --table my-project.analytics.events \
  --run-data-checks \
  --freshness-col event_timestamp \
  --key-cols event_id \
  --max-bytes-billed 1073741824 \
  --sample-limit 5 \
  --output-json report.json \
  --output-md report.md
```

### Constraining scans with --where

For partitioned or large tables, pass `--where` to restrict the data-scanning checks (freshness, duplicate keys, column profile). The clause is appended to each query as `WHERE (...)`. Backticks, semicolons, and SQL comments are rejected.

```bash
python scripts/evaluate_bigquery_table.py \
  --table my-project.analytics.events \
  --run-data-checks \
  --where "event_date >= '2026-04-26'" \
  --key-cols event_id \
  --max-bytes-billed 1073741824
```

### Expectations (CI-style health gate)

Pass any combination of expectation flags and the script exits with code `3` if any fail. The report still gets written so you can inspect what broke.

```bash
python scripts/evaluate_bigquery_table.py \
  --table my-project.analytics.events \
  --run-data-checks \
  --freshness-col event_timestamp \
  --key-cols event_id \
  --expect-min-rows 1000 \
  --expect-zero-duplicates \
  --expect-freshness-within 24h \
  --expect-max-null-rate user_id=0 \
  --expect-max-null-rate referrer=0.05
```

Available expectations:

- `--expect-min-rows N` — uses metadata `num_rows`.
- `--expect-zero-duplicates` — requires `--key-cols`.
- `--expect-freshness-within DURATION` — `30s`, `15m`, `24h`, `7d`. Requires `--freshness-col`.
- `--expect-max-null-rate COL=RATE` — repeatable. Requires the column to be in the profile.
- `--expect-no-schema-drift` — requires `--baseline`.

Exit codes: `0` clean, `1` BigQuery API error, `2` input/validation error, `3` expectation failure.

## What it checks

Applies to both Path A (Claude) and Path B (Python CLI):

- Table metadata: type, created/modified timestamps, rows, bytes, labels, description.
- Schema summary: top-level fields, type, mode, description.
- Partitioning and clustering metadata.
- Optional partition health (`--check-partitions` or `--run-data-checks` on a partitioned table): partition count, empty partitions, oldest/newest partition, total/max/avg partition bytes — via `INFORMATION_SCHEMA.PARTITIONS`.
- Optional freshness: `MAX(freshness_column)`.
- Optional duplicate keys: duplicate groups and duplicate excess rows.
- Optional column profile: null counts and approximate distinct counts for scalar columns.
- Optional sample rows via the BigQuery table row API.
- Optional schema drift: pass `--baseline previous_report.json` to surface added, removed, or retyped columns vs a prior run.

## HTML dashboard (optional)

Add `--output-html report.html` to also emit a single self-contained HTML dashboard alongside the JSON/Markdown. It has inline CSS, an SVG bar chart of per-column null rates, status pills for each expectation, a partition-stats card, and a collapsible schema table. No external assets, no JavaScript — open it locally or attach it as a CI artifact.

```bash
python scripts/evaluate_bigquery_table.py \
  --table my-project.analytics.events \
  --run-data-checks \
  --check-partitions \
  --freshness-col event_timestamp \
  --output-html report.html \
  --theme auto
```

Use `--theme {auto,light,dark}` (default `auto`) to control the palette. `auto` ships both palettes and switches via the `prefers-color-scheme` media query; `light` or `dark` bake in a single fixed palette — useful for artifacts that need consistent appearance regardless of viewer.

Pre-rendered examples live in [`examples/`](examples/) — open `examples/sample_dashboard_auto.html` (or `_light` / `_dark`) in a browser to see the output without running against a real table. Re-render any time after changing the renderer:

```bash
python examples/render_sample_dashboard.py
```

The generator stubs `google.cloud.bigquery`, so it does not need GCP credentials or the real client library. The script in `scripts/` is the source of truth; the HTML files are illustrative and may lag if not re-rendered.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/
```

The test suite stubs `google.cloud.bigquery` so it runs without GCP credentials. It covers the input validators, the expectation evaluator, the schema-drift differ, and the WHERE-clause guard.

## Notes

Data checks can scan table data and incur BigQuery costs. The script performs a dry run first and skips a check when the estimated bytes exceed `--max-bytes-billed`.
