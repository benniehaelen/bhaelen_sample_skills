# Metadata Scorecard Skill

Score authored metadata quality on Google BigQuery tables against the data-steward rubric. Reads only metadata (descriptions, labels, schema field descriptions, policy tags) — never scans data. Produces a per-table 0-100 score, A-F letter grade, per-criterion evidence, and an actionable issues list with suggested fixes.

There are two ways to run it:

- **[Using with Claude](#using-with-claude-recommended)** — ask Claude in plain English. Claude reads the rubric, fetches metadata via a BigQuery MCP server, grades each criterion semantically, and writes the scorecard files. Recommended for most users.
- **[Run locally with Python](#run-locally-with-python-path-b)** — bundled CLI script that uses `google-cloud-bigquery` directly. Heuristic grading (regex + keyword matching). Useful when an MCP server isn't available, or for CI pipelines.

Both paths produce the same JSON / Markdown / HTML output shape and share the same renderer.

## Using with Claude (recommended)

Claude reads `SKILL.md`, fetches BigQuery metadata via a registered MCP connector, grades each table against the rubric, and writes the scorecard files. No local Python install required — you only need the skill directory and a BigQuery MCP server registered with your Claude environment.

### Install the skill

#### Claude Code

Drop the skill directory into one of:

```bash
# User-level (available across all projects)
cp -r score-table-metadata-skill ~/.claude/skills/score-table-metadata

# Or project-level (only this project)
mkdir -p .claude/skills && cp -r score-table-metadata-skill .claude/skills/score-table-metadata
```

The **directory name becomes the slash-command name** — keep it as `score-table-metadata` so the skill is invoked as `/score-table-metadata` and matches the `name:` in `SKILL.md`'s frontmatter. Claude Code hot-reloads skills inside an active session, so no restart is needed (unless `~/.claude/skills/` didn't exist before, in which case start a new session once so the watcher picks up the directory).

#### Claude.ai

Open **Workspace settings → Custom skills** (or **Team settings** for org-wide installation), then upload the skill directory as a `.zip`. The skill becomes available across new conversations in that workspace.

### Set up the BigQuery MCP server

Claude needs metadata-read access to BigQuery. The skill expects a registered MCP server that exposes `mcp__bigquery__get_table_info`, `mcp__bigquery__list_table_ids`, and `mcp__bigquery__execute_sql` (the convention used by the [Google GenAI Toolbox](https://github.com/googleapis/genai-toolbox) when configured with `--prebuilt bigquery`).

**Claude Code** — register the MCP server once, user-wide:

```bash
# Install the toolbox per its own README, then register with Claude Code:
claude mcp add bigquery -- toolbox serve --prebuilt bigquery
# Authenticate the toolbox via Application Default Credentials:
gcloud auth application-default login
```

Verify with `claude mcp list` — `bigquery` should appear. Test by asking Claude *"List the datasets in `<your-project>`."*

**Claude.ai** — Workspace MCP settings → add the BigQuery MCP using your workspace's auth model (typically a service-account JSON). Same verification: ask Claude to list datasets first.

### Example prompts

Once both the skill and MCP server are installed, ask Claude in plain English:

- *"Score the metadata for every table in `my-project.analytics`."*
- *"Audit these BigQuery tables for metadata quality: `acme.sales.orders`, `acme.sales.line_items`, `acme.sales.customers`."*
- *"Run a metadata scorecard against the `acme.warehouse` dataset and fail anything below 70."*
- *"Score the tables in `acme.regulated` using my custom rubric at `./finance-rubric.json`."*
- *"Show me the worst-scoring tables in `acme.warehouse` and the top three issues for each, with concrete suggested fixes I can paste into the descriptions."*

You can also explicitly invoke the skill with `/score-table-metadata` in Claude Code if Claude doesn't pick it up automatically.

### What you get back

Three files in your working directory (file names configurable in the prompt):

- **`metadata_scorecard.json`** — machine-readable scorecard. Per-table score, grade, per-criterion evidence, and an `issues` list where each entry has a `criterion`, `message`, `column` (when applicable), and a concrete `suggestion` you can paste into the description.
- **`metadata_scorecard.md`** — Markdown summary, one section per table, sorted worst-first.
- **`metadata_scorecard.html`** — self-contained dashboard (no JavaScript, no external assets) you can open in any browser, email around, or commit alongside docs.

A pre-rendered example lives in [`examples/sample_scorecard_light.html`](examples/sample_scorecard_light.html) (also `_dark` and `_auto` variants). Open it to see what a finished scorecard looks like before running against your own data.

### What runs where

In Path A, **Claude does the rubric grading itself** by reading `SKILL.md`. The bundled Python isn't a rubric engine that Claude shells out to — Claude reads each description, decides pass / partial / fail per criterion, and assembles the JSON report dict in its own context.

The only Python that runs in Path A is the **renderer**: `scripts/render_scorecard.py` (and its imports, `_scorecard_render.py` + `_serialize.py`). It's a pure JSON-in / Markdown+HTML-out transformer with no BigQuery dependency and no third-party packages. After writing the JSON, Claude calls it once:

```bash
python scripts/render_scorecard.py \
  --input scorecard.json --output-md scorecard.md --output-html scorecard.html --theme auto
```

| File | Path A (Claude) | Path B (CLI) |
| --- | --- | --- |
| `scripts/render_scorecard.py` + `_scorecard_render.py` + `_serialize.py` | runs (renderer) | runs |
| `scripts/score_table_metadata.py` (CLI entry) | not used | runs |
| `scripts/_rubric.py` (heuristic grader) | not used — Claude grades from `SKILL.md` | runs |
| `scripts/_validation.py` | not used | runs |

**Practical implications:**

- Path A still needs Python somewhere — the renderer has to run. In **Claude Code**, it runs on your local machine via the Bash tool (stdlib only, so any Python 3 works — no `pip install` needed). In **Claude.ai**, it runs in the Python analysis sandbox.
- If you only need the JSON output, no Python is required at all — Claude writes that file directly.
- The renderer is the contract, not the rubric. Both paths feed the *same* renderer with the same JSON shape (documented in `SKILL.md`), so the Markdown and HTML outputs are identical regardless of who graded.

### Troubleshooting

- **Claude doesn't seem to know about the skill.** Confirm `SKILL.md` sits at the *root* of the skill directory (e.g., `~/.claude/skills/score-table-metadata/SKILL.md`) — not nested under `scripts/` or anywhere else. In Claude Code, run `/help` and check the available skills list. In Claude.ai, look at the Custom skills panel in workspace settings.
- **Claude says it doesn't have BigQuery access.** The MCP server isn't reachable. Run `claude mcp list` (Claude Code) or check the workspace MCP settings (Claude.ai). Test by asking Claude *"List the datasets in `<project>`"* — if that fails, fix the MCP before re-running the skill.
- **Claude can't find my custom rubric file.** Use an absolute path, or a path relative to the directory Claude is operating in (Claude will tell you which directory it's using if you ask).
- **The output JSON is missing fields.** Re-prompt with: *"Re-grade and make sure each criterion has `name`, `points`, `max`, `passed`, and `evidence`, and that every issue has `criterion`, `message`, and `suggestion` — match the JSON shape in `SKILL.md`."*
- **I have local BigQuery access but no MCP server.** Use the Python path below.

## Run locally with Python (Path B)

If a BigQuery MCP server isn't available — for example, in CI pipelines or on a workstation without one configured — the skill ships a deterministic Python implementation that uses `google-cloud-bigquery` directly. The grading is heuristic (regex + keyword matching) rather than semantic, so suggested fixes are schema-aware templates rather than table-specific drafts. The JSON shape, renderer, and CLI flags are identical.

### Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
gcloud auth application-default login
```

### Score every table in a dataset

```bash
python scripts/score_table_metadata.py \
  --dataset my-project.analytics \
  --output-json scorecard.json \
  --output-md scorecard.md \
  --output-html scorecard.html
```

### Score an explicit list of tables

```bash
python scripts/score_table_metadata.py \
  --tables my-project.analytics.events,my-project.analytics.users \
  --output-json scorecard.json --output-md scorecard.md
```

### CI-style health gate

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

## Nested fields (`STRUCT` and `ARRAY<STRUCT>`)

BigQuery schemas can be hierarchical. The skill walks the schema tree, scoring every leaf and parent struct as its own column with **dotted names** (`address.street`, `events.event_id`) and a `parent` field linking each entry to its immediate parent. Trigger regexes recognize `.` as a name boundary, so `user.email` correctly fires the sensitivity criterion, `event.timestamp` fires units/format, and `address.zip_code` fires the coded criterion. The HTML scorecard indents nested entries under their parent so the structure is visible at a glance; the Markdown summary uses the dotted name itself to communicate hierarchy. See [`examples/sample_scorecard_light.html`](examples/sample_scorecard_light.html) for what nested rendering looks like.

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
