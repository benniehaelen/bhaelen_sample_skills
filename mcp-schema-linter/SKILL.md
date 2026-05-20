---
name: mcp-schema-linter
description: Use this skill when a user wants to audit, lint, score, or grade the tool definitions exposed by an MCP server, with a primary focus on token cost. It scores each tool against a token-economy rubric (3 tiers of criteria covering direct cost drivers, indirect cost drivers, and hygiene), reports the total catalog cost per turn in tokens, ranks the top refactor opportunities by tokens saved, and emits a scorecard with per-tool evidence and concrete rewrite suggestions. Accepts either a live MCP server URL, a `tools/list` JSON dump, or a file containing tool definitions. Optional CI-style gate (`--expect-max-tokens`) exits non-zero if the catalog exceeds a budget.
---

# MCP Schema Linter

## Purpose

Lint and score the tool definitions exposed by an MCP server, with a primary focus on **what the catalog costs the model on every turn**. Reads only the tool definitions (name, description, input schema, annotations) — never invokes tools, never sees runtime traffic. Produces a per-tool 0-100 score, A-F letter grade, per-criterion evidence, a total catalog token cost, a top-offender list, and a list of actionable rewrite suggestions ranked by tokens saved.

The headline metric is **tokens per turn**, not a letter grade. Every tool in the server's `tools/list` response is serialized into the model's context on every message in a conversation, regardless of whether the tool is ever called. Most teams have no visibility into this cost. This skill makes it visible and ranks the refactors that recover the most tokens for the least effort.

## Required inputs

One of:
- `server_url`: an MCP server URL the linter can call `tools/list` against (HTTP or SSE transport).
- `tools_file`: a path to a JSON file containing either a raw `tools/list` response, an array of tool objects, or a server export.
- `tools_json`: a JSON string pasted inline (useful for ad-hoc scoring).

## Optional inputs

- `expect_max_tokens`: integer; exit 3 if the total catalog cost exceeds this budget (CI gate). Default unset.
- `expect_min_score`: integer 0-100; exit 3 if any tool scores below it (CI gate). Default unset.
- `rubric_config`: path to a JSON file overriding the rubric data (weights, token thresholds, boilerplate phrases, grade cutoffs). Omitted sections fall back to the built-in defaults. See "Configuring the rubric" below.
- `tokenizer`: `cl100k_base` (default; portable GPT-4-family reference) or `claude` (uses the Anthropic tokenizer if reachable). The relative ranking of tools is stable across tokenizers; absolute counts vary by ~10-20%.
- `serialization`: `raw` (default; tool definition serialized as compact JSON) or `anthropic` (approximates how the Anthropic API renders the tool block in the prompt). The default is honest about being an approximation.
- HTML theme: `auto` (default) / `light` / `dark`.

## Rubric (v1.0)

Each criterion has a **tier** (1 = direct cost, 2 = indirect cost, 3 = hygiene), a **points value** (0/1/2 fail/partial/pass), and a **tokens_saved estimate** that the linter uses to rank refactor opportunities. Severity is encoded in the tier and the tokens_saved figure, not a separate severity field — the goal is for every issue to translate directly into a token impact.

### Tier 1 — Direct cost drivers (paid on every turn)

These criteria measure tokens consumed by the catalog as written. Each failure has a directly observable token cost.

1. `description_length_appropriate` (max 2) — description sits in the 20-150 token band.
   - Pass (2): 20-150 tokens.
   - Partial (1): 150-300 tokens, or 10-20 tokens.
   - Fail (0): <10 tokens (under-described, causes router failures) or >300 tokens (bloated).
   - `tokens_saved`: actual tokens over the upper threshold, or 0 if under (under-description costs the router, not the catalog).

2. `no_boilerplate_opener` (max 2) — description does not open with a generic phrase like "This tool allows you to," "Use this tool when you want to," "The purpose of this tool is to," "This function will."
   - Pass (2): no boilerplate opener detected.
   - Partial (1): mild redundancy (e.g. "This retrieves...").
   - Fail (0): explicit boilerplate opener.
   - `tokens_saved`: token length of the detected boilerplate phrase.

3. `schema_compact` (max 2) — input schema is not bloated by deep nesting, redundant `oneOf`/`anyOf` unions, or fields that exist only to describe edge cases.
   - Pass (2): schema serializes to ≤200 tokens.
   - Partial (1): 200-500 tokens.
   - Fail (0): >500 tokens.
   - `tokens_saved`: tokens over the upper threshold.

4. `no_redundant_examples` (max 2) — examples are either in the description OR in a separate examples field, not both. Examples in the schema's `default` or `examples` keys get serialized.
   - Pass (2): examples in exactly one place, or no examples (acceptable but flagged at Tier 2).
   - Fail (0): same example content appears in description and schema.
   - `tokens_saved`: token length of the duplicated content.

5. `concise_parameter_names` (max 2) — parameter names are short and meaningful, not over-descriptive (`customer_email`, not `the_customer_email_address`).
   - Pass (2): no parameter name exceeds 25 characters or 6 tokens.
   - Partial (1): one parameter over the threshold.
   - Fail (0): multiple parameters over the threshold.
   - `tokens_saved`: estimated tokens recovered by renaming, summed across the catalog (parameter names are repeated in every call).

### Tier 2 — Indirect cost drivers (cause routing errors that burn whole turns)

These criteria do not directly consume tokens, but their absence causes the model to pick the wrong tool or ask clarifying questions, which burns turns. The `tokens_saved` figure here is an **estimated** per-occurrence cost based on typical recovery patterns (one or two extra turns at typical turn size).

6. `has_when_to_use` (max 2) — description includes explicit guidance on when to pick this tool over siblings ("Use this when...", "Choose this if...", or equivalent positive guidance).
   - Pass (2): explicit when-to-use clause.
   - Partial (1): implicit guidance.
   - Fail (0): no guidance.
   - `tokens_saved`: estimated 300-800 tokens per routing miss (configurable in the rubric).

7. `has_when_not_to_use` (max 2) — description includes explicit negative guidance when siblings exist.
   - Pass (2): explicit negative guidance.
   - Partial (1): implicit ("for X, use Y instead" somewhere in the description).
   - Fail (0): no negative guidance, and ≥1 sibling tool with overlapping purpose exists in the catalog.
   - N/A: no siblings detected, this criterion is omitted.
   - `tokens_saved`: estimated 300-800 tokens per routing miss.

8. `no_overlap_with_siblings` (max 2) — when multiple tools have semantically similar descriptions, each calls out the distinction.
   - Pass (2): no overlapping siblings, or all overlapping pairs have explicit distinguishing language.
   - Partial (1): one overlapping pair without distinguishing language.
   - Fail (0): multiple overlapping pairs without distinguishing language.
   - `tokens_saved`: estimated 500-1000 tokens per overlapping pair.

9. `param_format_specified` (max 2 per parameter, averaged) — date/timestamp/ID parameters state their format.
   - Pass (2): every applicable parameter states format (ISO 8601, UUID, etc.).
   - Partial (1): some parameters state format.
   - Fail (0): no formats stated, applicable parameters exist.
   - N/A: no parameters of these types in the schema.
   - `tokens_saved`: estimated 200-500 tokens per format-mismatch recovery.

10. `destructive_action_annotated` (max 2) — tools that delete, send, charge, or otherwise mutate state are flagged via the MCP `annotations` block (`destructiveHint: true`, `readOnlyHint: false`, `idempotentHint` set) AND the description states the action explicitly.
    - Pass (2): both annotation and description present.
    - Partial (1): one but not both.
    - Fail (0): mutating tool with neither.
    - N/A: read-only tool (correctly annotated or inferred from name/description).
    - `tokens_saved`: not a token saver directly, but graded for catalog correctness. Recorded as a critical safety issue regardless of token impact.

### Tier 3 — Hygiene (small per-occurrence cost, accumulates across the catalog)

11. `name_is_verb_oriented` (max 2) — tool name uses an action verb.
    - Pass (2): clear verb (`create_invoice`, `search_users`, `delete_record`).
    - Partial (1): nominal but unambiguous (`invoice_creator`).
    - Fail (0): pure noun (`invoice`) or ambiguous (`process`).
    - `tokens_saved`: minimal direct cost; counted as routing-quality contributor.

12. `name_is_unique_in_catalog` (max 2) — no near-synonymous names (`get_user` and `fetch_user_by_id` in the same catalog).
    - Pass (2): no near-synonymous siblings.
    - Fail (0): near-synonymous siblings present.
    - `tokens_saved`: counted as a routing-quality contributor; the real fix is consolidating the tools.

13. `every_param_has_description` (max 2) — no parameter without a `description`.
    - Pass (2): all parameters described.
    - Partial (1): one undescribed parameter.
    - Fail (0): two or more undescribed parameters.
    - `tokens_saved`: counted as routing-quality contributor (undescribed params cause argument-fabrication failures).

14. `enum_values_documented` (max 2) — enum parameters list their valid values with a one-line semantic note per value (not just the raw list).
    - Pass (2): every enum value has a semantic gloss in the description.
    - Partial (1): values listed without glosses.
    - Fail (0): enum constrained in schema but values not documented.
    - N/A: no enum parameters.
    - `tokens_saved`: counted as routing-quality contributor.

15. `units_specified` (max 2) — numeric parameters state units (seconds, bytes, USD cents).
    - Pass (2): all numeric params with ambiguous units state them.
    - Partial (1): some specified.
    - Fail (0): none specified, applicable parameters exist.
    - N/A: no numeric parameters with ambiguous units.
    - `tokens_saved`: counted as routing-quality contributor.

### Scoring formula

For each tool:
- `tool_points = sum(criterion.points)` across applicable criteria
- `tool_max = sum(criterion.max)` across applicable criteria
- `score = round(100 * tool_points / tool_max)`
- Default letter grades: 90+ A, 80-89 B, 70-79 C, 60-69 D, <60 F.

Catalog-level metrics:
- `total_tokens` — sum of per-tool token costs under the chosen tokenizer + serialization.
- `addressable_savings` — sum of `tokens_saved` across all failed Tier 1 criteria. Tier 2 and Tier 3 savings are reported separately because they are estimated, not measured.
- `catalog_grade` — weighted mean of tool grades, weighted by tool token cost (a bloated tool's grade matters more to the catalog than a tiny one's).

### Configuring the rubric

The rubric *data* — boilerplate phrase lists, token thresholds, weights, grade cutoffs, routing-miss cost estimates — can be overridden by a JSON config file. The check *logic* is fixed in code. Pass the file via `--rubric-config <path>` to the bundled CLI; Path A agents should read the file and respect its contents when grading.

Sections you omit fall back to the built-in defaults. The shipped `examples/rubric_default.json` reproduces the built-in scoring exactly and is a useful starting template.

Schema (all sections optional except where noted):

```jsonc
{
  "name": "token-economy-default",
  "version": "1.0",
  "tokenizer": "cl100k_base",                    // default tokenizer
  "serialization": "raw",                        // default serialization
  "grade_cutoffs": {"A": 90, "B": 80, "C": 70, "D": 60},
  "thresholds": {
    "description_tokens_min": 20,                // below = under-described
    "description_tokens_good_max": 150,          // sweet spot upper bound
    "description_tokens_bloated": 300,           // hard upper bound
    "schema_tokens_good_max": 200,
    "schema_tokens_bloated": 500,
    "parameter_name_chars_max": 25,
    "parameter_name_tokens_max": 6,
    "routing_miss_cost_low": 300,                // estimated tokens per routing miss
    "routing_miss_cost_high": 800,
    "overlap_cost_low": 500,
    "overlap_cost_high": 1000,
    "format_recovery_cost_low": 200,
    "format_recovery_cost_high": 500
  },
  "boilerplate_openers": [
    "this tool allows you to",
    "use this tool when you want to",
    "the purpose of this tool is",
    "this function will",
    "this endpoint",
    "this method"
  ],
  "siblings_similarity_threshold": 0.7,          // cosine sim or token-overlap threshold
  "destructive_verbs": [                         // names/descriptions containing these are mutating
    "delete", "remove", "drop", "purge",
    "send", "publish", "post",
    "charge", "refund", "transfer",
    "create", "update", "modify", "patch"
  ],
  "format_keywords": {                           // parameter names that should specify format
    "date": ["date", "datetime", "timestamp", "_at", "_on"],
    "id":   ["_id", "uuid", "guid"],
    "url":  ["url", "uri", "endpoint"]
  }
}
```

**Path A note (semantic grading with a custom config):** if the user supplies a rubric config, read it before scoring. The boilerplate phrase list and keyword matchers are still guides — apply judgment when a tool plainly satisfies a criterion despite not matching a listed keyword. But the *token thresholds*, *grade cutoffs*, *cost estimates*, and *weights* are authoritative — apply them exactly so Path A and Path B agree on the numeric score and token totals.

Provenance is stamped into the report under the top-level `rubric_config` key:

```jsonc
{
  "rubric_config": {
    "source": "/abs/path/to/rubric.json",   // or "builtin"
    "name": "token-economy-default",
    "version": "1.0",
    "sha256": "a1b2c3..."                   // sha256 of the file bytes; "" for builtin
  }
}
```

The renderer surfaces this in the scorecard subtitle so reviewers can tell at a glance which rubric was applied.

## Implementation

Two execution paths. **Prefer Path A** when scoring should be semantic — the heuristic Python implementation in Path B is fast and deterministic, but Path A produces better-quality suggestions and catches overlap cases the heuristic misses.

### Path A — Agent-driven semantic grading (preferred for new audits)

**Step 1 — fetch the tool list.**

If the user provided `server_url`, call `tools/list` against it (via the MCP transport the server exposes) and capture the response. If they provided `tools_file` or `tools_json`, load and parse it. Normalize all three inputs into a canonical internal representation:

```json
{
  "server": {
    "name": "production-billing-server",
    "version": "2.4.1",
    "source": "https://billing.example.com/mcp"
  },
  "tools": [
    {
      "name": "create_invoice",
      "description": "...",
      "inputSchema": { ... },
      "annotations": { "destructiveHint": true, "idempotentHint": false }
    },
    ...
  ]
}
```

If the server also exposes `resources` or `prompts`, note their presence in the report's `warnings` array with the message "Server exposes N resources / M prompts; this skill scores tools only." Do not lint them in v1.

**Step 2 — measure token cost.**

For each tool, compute:
- `description_tokens` — token count of the description string.
- `schema_tokens` — token count of the input schema serialized as compact JSON.
- `annotations_tokens` — token count of the annotations block (usually small).
- `total_tokens` — sum of the above plus the tool name.

Use the tokenizer specified by `--tokenizer`. Default is `cl100k_base` (portable, no network dependency). The relative ranking of tools is stable across tokenizers; the report should call this out in its methodology footnote.

**Step 3 — grade each criterion semantically.**

For every criterion in the rubric, decide pass / partial / fail. You are the grader. The keyword matchers and boilerplate lists are guides, not rules:

- Pass: the criterion is clearly satisfied.
- Partial: partially satisfied.
- Fail: not satisfied.
- N/A (conditional criteria only): the criterion does not apply (e.g. no enum parameters, so `enum_values_documented` is omitted).

For each criterion, capture a short evidence snippet (≤120 chars, single line) — the part of the tool definition that justifies the score. For fails, leave evidence empty if nothing is relevant.

For Tier 2 overlap detection (`has_when_not_to_use`, `no_overlap_with_siblings`): compare each tool's description against every other tool's description. Two tools "overlap" if their core purposes are semantically near-duplicates. Examples of overlap: `get_user` and `fetch_user`; `search_orders` and `find_orders`; `create_record` and `add_record`. Examples of legitimate siblings: `create_invoice` and `void_invoice` (different verbs, clearly distinguished).

**Step 4 — compute tokens_saved estimates.**

For each failed Tier 1 criterion, compute the concrete token impact:
- `description_length_appropriate` (>150): `(description_tokens - 150) * frequency_weight`. Frequency weight is 1.0 for v1 (every tool serialized on every turn). Report as "tokens recoverable by trimming."
- `no_boilerplate_opener`: count the tokens in the detected boilerplate phrase.
- `schema_compact` (>200): `schema_tokens - 200`.
- `no_redundant_examples`: count the tokens of the duplicated content.
- `concise_parameter_names`: estimate based on character count difference from a "good" name length, ×2 (names appear in both schema and tool call).

For Tier 2 criteria, use the cost estimates in the rubric config (`routing_miss_cost_low`, etc.) as the `tokens_saved` figure, but mark them as `estimated: true` in the JSON output so the scorecard can render them differently from measured savings.

For Tier 3 criteria, `tokens_saved` is 0 directly but the criterion contributes to the overall tool score and is reported in the suggestions list.

**Step 5 — rank refactor opportunities.**

Sort all failed criteria across all tools by `tokens_saved` descending. The top 10 form the "Top refactor opportunities" section of the report. For each, produce a concrete suggestion:

> **`create_invoice`** — Failed `description_length_appropriate` (description is 412 tokens, over by 262)
> Suggested: Trim the description to ~120 tokens, removing the "Background" paragraph and the inline example. Move the example to a separate `examples` field or to a README.
> Estimated savings: 262 tokens per turn.

The suggestion should be specific enough that an engineer can act on it in 5-10 minutes per item.

**Step 6 — assemble the report dict.**

```json
{
  "rubric_version": "1.0",
  "rubric_config": {
    "source": "builtin",
    "name": "token-economy-default",
    "version": "1.0",
    "sha256": ""
  },
  "tokenizer": "cl100k_base",
  "serialization": "raw",
  "scored_at": "2026-05-19T17:32:00+00:00",
  "server": {
    "name": "production-billing-server",
    "version": "2.4.1",
    "source": "https://billing.example.com/mcp"
  },
  "catalog": {
    "tool_count": 14,
    "total_tokens": 6840,
    "addressable_savings_measured": 770,
    "addressable_savings_estimated": 1900,
    "catalog_grade": "B-",
    "catalog_score": 78,
    "top_offenders": [
      {"tool": "create_invoice", "tokens": 812, "pct_of_catalog": 11.9},
      {"tool": "search_transactions", "tokens": 740, "pct_of_catalog": 10.8},
      {"tool": "update_customer", "tokens": 618, "pct_of_catalog": 9.0}
    ],
    "top_refactors": [
      {
        "tool": "create_invoice",
        "criterion": "description_length_appropriate",
        "tier": 1,
        "tokens_saved": 262,
        "estimated": false,
        "message": "Description is 412 tokens, over the 150-token sweet spot upper bound.",
        "suggestion": "Trim to ~120 tokens; move the inline example to a separate field or README."
      },
      ...
    ]
  },
  "tools": [
    {
      "name": "create_invoice",
      "score": 62,
      "grade": "D",
      "tokens": {
        "description": 412,
        "schema": 184,
        "annotations": 12,
        "name": 4,
        "total": 612
      },
      "annotations": {"destructiveHint": false, "readOnlyHint": false},
      "criteria": [
        {"name": "description_length_appropriate", "tier": 1, "points": 0, "max": 2, "passed": false, "evidence": "412 tokens; threshold 150", "tokens_saved": 262, "estimated": false},
        {"name": "no_boilerplate_opener", "tier": 1, "points": 0, "max": 2, "passed": false, "evidence": "Opens with 'This tool allows you to...'", "tokens_saved": 8, "estimated": false},
        {"name": "schema_compact", "tier": 1, "points": 2, "max": 2, "passed": true, "evidence": "184 tokens; under 200", "tokens_saved": 0, "estimated": false},
        ...
      ],
      "issues": [
        {
          "criterion": "destructive_action_annotated",
          "tier": 2,
          "message": "Tool creates database records but `destructiveHint` is false and description does not call out the mutation.",
          "suggestion": "Set `annotations.destructiveHint: true` and add to description: 'This tool creates a new invoice record in the billing database.'"
        },
        ...
      ]
    },
    ...
  ],
  "expectations": [],
  "warnings": [
    "Server exposes 3 resources; this skill scores tools only.",
    "Tokenizer is cl100k_base; absolute counts vary ~10-20% for Claude. Relative ranking is stable."
  ]
}
```

The `score`, `grade`, `tokens`, totals, and the `passed` boolean must all be self-consistent. Compute them; do not invent.

**Issues with suggested fixes (Path A): write concrete, tool-specific suggestions.**

Each entry in `issues` is a dict with `criterion`, `tier`, `message`, `suggestion`. The `message` describes what's missing; the `suggestion` is a concrete proposed fix that the engineer can apply with minimal editing. Suggestions should:

- Reference real parameter names from the schema, not placeholders.
- For description trimming, suggest a specific target token count and which sections to cut.
- For boilerplate, propose the rewritten opener.
- For missing annotations, propose the exact JSON block to add.
- For overlapping siblings, propose either a consolidation ("merge `get_user` and `fetch_user_by_id` into `get_user`") or a distinguishing edit to both descriptions.
- For parameter-format issues, name the format explicitly (`ISO 8601`, `UUID v4`, `RFC 3339`).

Suggestions are best-effort proposals, not authoritative facts. **Never fabricate** facts you cannot infer from the tool definition — e.g. do not claim a tool charges money unless the description or annotations already hint at it.

**Step 7 — render.**

Write the report dict as JSON, then run:

```bash
python scripts/render_scorecard.py --input scorecard.json --output-md scorecard.md --output-html scorecard.html --theme auto
```

To gate against budgets:

```bash
python scripts/render_scorecard.py --input scorecard.json --output-md scorecard.md --expect-max-tokens 8000 --expect-min-score 70
```

`render_scorecard.py` has no MCP dependency — Path A works in any environment with Python + the skill files.

### Path B — Bundled Python script (fallback)

Use this for CI runs or when the input is already a static file. The heuristic implementation does not perform semantic overlap detection (it falls back to a token-overlap heuristic) and produces template-based suggestions; results are still useful but the agent path is better.

```bash
python scripts/lint_mcp_schema.py \
  --server-url https://billing.example.com/mcp \
  --output-json scorecard.json \
  --output-md scorecard.md \
  --output-html scorecard.html \
  --expect-max-tokens 8000
```

Or from a file:

```bash
python scripts/lint_mcp_schema.py \
  --tools-file tools.json \
  --output-json scorecard.json --output-md scorecard.md
```

To apply a custom rubric:

```bash
python scripts/lint_mcp_schema.py \
  --tools-file tools.json \
  --rubric-config path/to/rubric.json \
  --output-json scorecard.json --output-md scorecard.md
```

To switch tokenizer or serialization:

```bash
python scripts/lint_mcp_schema.py \
  --tools-file tools.json \
  --tokenizer claude \
  --serialization anthropic \
  --output-json scorecard.json
```

## Output

- **JSON** — machine-readable scorecard, suitable as input to `render_scorecard.py` or downstream tooling. The catalog-level `top_refactors` array is the most actionable section.
- **Markdown** — human review; a catalog summary at the top (token cost, grade, top offenders, top refactors), followed by one section per tool with criteria tables and an issues list.
- **HTML scorecard** (optional `--output-html`) — single self-contained file with inline CSS, token-cost badges, A-F grade pills, per-criterion pass/partial/fail pills, a top-refactor table with tokens-saved column, and a collapsible per-tool breakdown. No external assets, no JavaScript. Pass `--theme {auto,light,dark}` (default `auto`).

Tools are listed worst-first (lowest score, then highest token cost as tiebreaker) so the most actionable items appear at the top of the scorecard.

## Methodology footnote (always shown in output)

The token counts reported by this skill are an approximation. Different model providers tokenize the same string slightly differently, and the way each provider's API serializes a tool block into the final prompt varies. This skill defaults to the `cl100k_base` tokenizer (GPT-4 family) because it is portable and offline, and serializes tool definitions as compact JSON. Both choices are documented assumptions, not ground truth.

What is robust across tokenizers and serializations is the **relative ranking** of tools and the **proportional savings** from each refactor. A tool that scores as a top offender under `cl100k_base` will be a top offender under any reasonable tokenizer; a refactor that saves 30% of its tokens under one tokenizer will save approximately 30% under another. Use this skill to find the cuts, then verify the absolute numbers against your production tokenizer if precision matters.

## Exit codes

- `0` — clean (all tools scored, all expectations passed if any).
- `1` — MCP fetch or input parse error.
- `2` — input/validation error.
- `3` — expectation failure (`--expect-max-tokens` budget exceeded or `--expect-min-score` threshold not met).
