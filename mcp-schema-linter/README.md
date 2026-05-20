# MCP Schema Linter Skill

Audit, lint, and score the tool definitions exposed by an MCP server, with a primary focus on **what the catalog costs the model on every turn**. Reads only the tool definitions (name, description, input schema, annotations); it never invokes tools and never sees runtime traffic. Produces a per-tool 0-100 score, A-F letter grade, per-criterion evidence, a total catalog token cost, a top-offender list, and a list of concrete rewrite suggestions ranked by tokens saved.

The headline metric is **tokens per turn**, not a letter grade. Every tool in a server's `tools/list` response is serialized into the model's context on every message in a conversation, whether or not the tool is ever called. Most teams have no visibility into this cost. This skill makes it visible and ranks the refactors that recover the most tokens for the least effort.

There are two ways to run it:

- **[Using with Claude](#using-with-claude-recommended)**: ask Claude in plain English. Claude reads the rubric, fetches the tool list from an MCP server (or a file you provide), grades each criterion semantically, and writes the scorecard files. Recommended for the best-quality suggestions and overlap detection.
- **[Run locally with Python](#run-locally-with-python-path-b)**: bundled CLI that fetches the tool list itself and grades with a deterministic heuristic. Useful for CI pipelines or when no agent is in the loop.

Both paths produce the same JSON / Markdown / HTML output shape and share the same renderer.

## Using with Claude (recommended)

Claude reads `SKILL.md`, fetches the tool list, grades each tool against the token-economy rubric, and writes the scorecard files. The agent path produces better suggestions and catches semantic sibling-overlap cases that the heuristic misses. No local Python install is required for the JSON output; the renderer (stdlib only) runs to produce Markdown and HTML.

### Install the skill

#### Claude Code

Drop the skill directory into one of:

```bash
# User-level (available across all projects)
cp -r mcp-schema-linter ~/.claude/skills/mcp-schema-linter

# Or project-level (only this project)
mkdir -p .claude/skills && cp -r mcp-schema-linter .claude/skills/mcp-schema-linter
```

The **directory name becomes the slash-command name**, so keep it as `mcp-schema-linter` to match the `name:` in `SKILL.md`'s frontmatter and invoke it as `/mcp-schema-linter`. Claude Code hot-reloads skills inside an active session.

#### Claude.ai

Open **Workspace settings, then Custom skills** (or **Team settings** for org-wide installation) and upload the skill directory as a `.zip`.

### Example prompts

- *"Lint the MCP tools at `https://billing.example.com/mcp` and show me the token cost per turn."*
- *"Score this `tools/list` dump for token economy and rank the top refactors: `./tools.json`."*
- *"Audit my server's tools and fail if the catalog exceeds 8000 tokens."*
- *"Which of my tools overlap with each other, and what should I merge or rename?"*

You can also explicitly invoke `/mcp-schema-linter` in Claude Code.

## Run locally with Python (Path B)

The bundled CLI fetches the tool list itself, grades with the deterministic heuristic in `_rules/`, and writes the scorecard. Grading is heuristic (regex + keyword matching) rather than semantic, so suggestions are templates rather than tool-specific drafts. The JSON shape, renderer, and CLI flags are identical to Path A.

### Install

```bash
python -m venv .venv
source .venv/bin/activate          # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Dependencies are deliberately minimal: `tiktoken` (the default offline tokenizer) and `requests` (only for the `--server-url` fetcher). The renderer itself has no third-party dependencies.

### Quick start: three input modes

The linter accepts the tool list from a live server, a file, or an inline JSON string. Pick exactly one.

**1. Live MCP server URL** (Streamable HTTP transport; calls `tools/list`):

```bash
python scripts/lint_mcp_schema.py \
  --server-url https://billing.example.com/mcp \
  --output-json scorecard.json \
  --output-md scorecard.md \
  --output-html scorecard.html
```

**2. A JSON file** (a raw `tools/list` response, a server export, or a bare array of tool objects):

```bash
python scripts/lint_mcp_schema.py \
  --tools-file examples/good_server.json \
  --output-json scorecard.json --output-md scorecard.md
```

**3. An inline JSON string** (handy for ad-hoc scoring):

```bash
python scripts/lint_mcp_schema.py \
  --tools-json '{"tools":[{"name":"get_user","description":"Fetch a user by id.","inputSchema":{}}]}' \
  --output-json scorecard.json
```

Switch the tokenizer or serialization model with `--tokenizer {cl100k_base,claude}` and `--serialization {raw,anthropic}`. The `claude` tokenizer calls Anthropic's `count_tokens` endpoint and requires `ANTHROPIC_API_KEY` in the environment; `cl100k_base` is the offline default.

### Render an existing report

`render_scorecard.py` is a pure JSON-in / Markdown+HTML-out transformer with no MCP dependency. Path A writes the JSON, then calls it; you can re-render any saved report the same way:

```bash
python scripts/render_scorecard.py \
  --input scorecard.json --output-md scorecard.md --output-html scorecard.html --theme auto
```

## Example output

Linting the bundled [`examples/healthcare_server.json`](examples/healthcare_server.json) (a generic, anonymized patient-data gateway) produces a scorecard that opens like this:

```markdown
# MCP Schema Scorecard

_Server: `patient-data-gateway v1.4.2` · token-economy-default v1.0 (builtin) · tokenizer `cl100k_base` / serialization `raw`_

## Catalog

- **Total tokens:** 451
- **Catalog grade:** 69/100 (D)
- **Tool count:** 7
- **Addressable savings:** 14 measured, 6300 estimated

### Top offenders by token cost

| Tool | Tokens | % of catalog |
| --- | ---: | ---: |
| `schedule_appointment` | 94 | 20.8% |
| `get_patient_record` | 74 | 16.4% |
| `list_appointments` | 70 | 15.5% |
| `search_patients` | 63 | 14.0% |
| `cancel_appointment` | 62 | 13.7% |

### Top refactor opportunities

| Tool | Criterion | Tier | Tokens saved | Suggestion |
| --- | --- | :---: | ---: | --- |
| `schedule_appointment` | `param_format_specified` | T2 | 700 (est.) | State ISO 8601 / UUID formats for the date and id parameters. |
| `list_appointments` | `param_format_specified` | T2 | 700 (est.) | State ISO 8601 / UUID formats for the date and id parameters. |
| `schedule_appointment` | `has_when_to_use` | T2 | 550 (est.) | Add a 'Use this when ...' sentence positioning it against siblings. |

## Tools (7)

### `schedule_appointment` 62/100 (D)

**Tokens:** name 3 · description 30 · schema 47 · annotations 14 · **total 94**

**Annotations:** `destructiveHint=True` `idempotentHint=False`

| Criterion | Status | Evidence | Tokens saved |
| --- | --- | --- | ---: |
| `no_boilerplate_opener` | [fail] | Opens with 'This tool allows you to'. | 5 |
| `param_format_specified` | [fail] | No formats stated; applicable parameter(s): provider_id, starts_at. | 700 (est.) |
| `every_param_has_description` | [fail] | 4 parameter(s) without description: record_number, provider_id, starts_at. | 0 |
```

The per-tool sections continue for all seven tools, worst-first. The `--output-html` flag emits the same content as a single self-contained dashboard (inline CSS, no JavaScript) with a token-cost badge, A-F grade pills, per-criterion pass/partial/fail pills, the top-refactor table, and a collapsible breakdown per tool. Pass `--theme {auto,light,dark}` to control the palette.

Pre-rendered HTML samples for all three example servers are committed in [`examples/`](examples/) so you can see the output without running anything:

- [`examples/sample_good.html`](examples/sample_good.html) (catalog 89/B), [`examples/sample_bad.html`](examples/sample_bad.html) (41/F), and [`examples/sample_healthcare.html`](examples/sample_healthcare.html) (69/D), all in auto theme.
- [`examples/sample_healthcare_light.html`](examples/sample_healthcare_light.html) and [`examples/sample_healthcare_dark.html`](examples/sample_healthcare_dark.html) show the light and dark palettes.

Regenerate them after changing the rubric or renderer:

```bash
python examples/render_sample_scorecard.py
```

The generator scores from the example files, so it needs `tiktoken` but no network access or API key.

## Rubric (v1.0)

Each criterion scores 0/1/2 (fail / partial / pass) and carries a tier and a `tokens_saved` estimate used to rank refactors. Severity is encoded in the tier and the token impact, not a separate field.

**Tier 1: direct cost drivers** (paid on every turn; `tokens_saved` is measured)

| Criterion | What it checks |
| --- | --- |
| `description_length_appropriate` | Description sits in the 20-150 token band. |
| `no_boilerplate_opener` | No generic opener ("This tool allows you to..."). |
| `schema_compact` | Input schema serializes to a compact size. |
| `no_redundant_examples` | Examples live in one place, not duplicated. |
| `concise_parameter_names` | Parameter names are short and meaningful. |

**Tier 2: indirect cost drivers** (cause routing errors that burn whole turns; `tokens_saved` is estimated)

| Criterion | What it checks |
| --- | --- |
| `has_when_to_use` | Positive guidance on when to pick this tool. |
| `has_when_not_to_use` | Negative guidance when sibling tools exist. N/A if none. |
| `no_overlap_with_siblings` | Overlapping siblings carry distinguishing language. |
| `param_format_specified` | Date / timestamp / ID / URL params state their format. N/A if none. |
| `destructive_action_annotated` | Mutating tools carry annotations and call out the mutation. N/A for read-only. |

**Tier 3: hygiene** (small per-occurrence cost that accumulates; `tokens_saved` is 0)

| Criterion | What it checks |
| --- | --- |
| `name_is_verb_oriented` | Tool name uses an action verb. |
| `name_is_unique_in_catalog` | No near-synonymous names. |
| `every_param_has_description` | No parameter missing a description. |
| `enum_values_documented` | Enum params list values with semantic notes. N/A if none. |
| `units_specified` | Numeric params with ambiguous units state them. N/A if none. |

**Score**: `round(100 * sum(points) / sum(max))` across the criteria that apply to a tool (N/A criteria are excluded from the denominator). The **catalog grade** is the per-tool scores weighted by token cost, so a bloated tool's grade matters more to the catalog than a tiny one's. Default grades: 90+ A, 80-89 B, 70-79 C, 60-69 D, below 60 F.

The three bundled examples illustrate the range: [`good_server.json`](examples/good_server.json) scores high (verb names, when-to-use clauses, documented enums and formats), [`bad_server.json`](examples/bad_server.json) scores in the 40s (boilerplate openers, bloated descriptions, overlapping siblings, unannotated destructive tools), and [`healthcare_server.json`](examples/healthcare_server.json) is mid-tier with mixed quality.

## Custom rubric

Pass `--rubric-config <path>` to override token thresholds, boilerplate phrase lists, keyword sets, cost estimates, weights, or grade cutoffs. Sections you omit fall back to the built-in defaults. The full schema is documented in [`SKILL.md`](SKILL.md#configuring-the-rubric). A starter file ships at [`examples/rubric_default.json`](examples/rubric_default.json), which reproduces the built-in scoring exactly; copy and tweak it.

```bash
python scripts/lint_mcp_schema.py \
  --tools-file examples/good_server.json \
  --rubric-config examples/rubric_default.json \
  --output-json scorecard.json --output-md scorecard.md
```

The rubric source, name, version, and SHA-256 are stamped into the JSON output and surfaced in the scorecard subtitle so reviewers can tell which rubric was applied.

## CI integration

Two gates exit with code `3` (and still write the scorecard so you can inspect what failed):

- `--expect-max-tokens N` fails if the catalog's total token cost exceeds `N`.
- `--expect-min-score N` fails if any tool scores below `N`.

```bash
python scripts/lint_mcp_schema.py \
  --tools-file tools.json \
  --expect-max-tokens 8000 \
  --expect-min-score 70 \
  --output-json scorecard.json --output-md scorecard.md
```

A minimal GitHub Actions step that fails the build when the catalog grows past its budget:

```yaml
- name: Lint MCP tool catalog
  run: |
    python scripts/lint_mcp_schema.py \
      --server-url "$MCP_SERVER_URL" \
      --expect-max-tokens 8000 \
      --output-json scorecard.json --output-md scorecard.md
```

Exit codes: `0` clean, `1` MCP fetch error, `2` input/validation error, `3` expectation failure.

## Methodology footnote (token economy)

The token counts this skill reports are an approximation. Different model providers tokenize the same string slightly differently, and the way each provider's API serializes a tool block into the final prompt varies. This skill defaults to the `cl100k_base` tokenizer (GPT-4 family) because it is portable and offline, and serializes tool definitions as compact JSON. Both choices are documented assumptions, not ground truth.

What is robust across tokenizers and serializations is the **relative ranking** of tools and the **proportional savings** from each refactor. A tool that scores as a top offender under `cl100k_base` will be a top offender under any reasonable tokenizer; a refactor that saves 30% of its tokens under one tokenizer will save approximately 30% under another. Use this skill to find the cuts, then verify the absolute numbers against your production tokenizer if precision matters. The `--tokenizer claude` mode counts against Anthropic's `count_tokens` endpoint when you need numbers closer to what Claude actually sees.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/
```

The suite covers the fetchers (all three input modes, with a stubbed `requests` for the URL path), the tokenizer wrappers (cl100k_base and a stubbed `claude` path), each of the 15 rules (pass / partial / fail / N/A branches), the scoring rollup against hand-computed values, and a renderer smoke test that parses the HTML and checks for the token-cost badge. Tests that need `tiktoken` skip cleanly when it is not installed.

## Notes

This skill scores tools only. If a server also exposes resources or prompts, their presence is noted in the report's `warnings` array but they are not graded in v1. For the full specification, output schema, and the agent-driven (Path A) flow, see [`SKILL.md`](SKILL.md).
