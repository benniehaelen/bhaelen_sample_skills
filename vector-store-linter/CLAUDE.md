# Hand-off notes for Claude Code

This document is the first thing to read when picking up implementation work on this skill. It explains the architectural decisions already made, the patterns to follow, the constraints that bound the work, and where to find the detailed work plan.

## What this skill is, in one paragraph

`vector-store-linter` audits a production vector store (Pinecone, BigQuery VECTOR_SEARCH, or Postgres pgvector) against a three-tier quality rubric. Tier 1 is configuration. Tier 2 is content. Tier 3 is retrieval. The skill produces a 0-100 score, a letter grade, per-criterion findings, and an actionable top-fixes list. Output formats are JSON, Markdown, and a self-contained HTML dashboard. CI gates via expectation flags.

Read `SKILL.md` for the user-facing description. Read `README.md` for the GitHub front door. Read this file for the implementation context.

## Status as of hand-off

Complete and not to be redesigned without asking:

- `SKILL.md` (user-facing capability description)
- `README.md` (GitHub front page)
- `LICENSE` (MIT)
- `requirements.txt` (dependencies, with optional adapter packages commented)
- `scripts/rubric.yaml` (the default rubric, 25 criteria across 3 tiers)
- `scripts/adapters/base.py` (the adapter interface, stable contract)
- `examples/ground_truth_template.csv` (sample format)

Stubbed and waiting for implementation:

- `scripts/lint.py` (the main entry point, argparse + dispatch)
- `scripts/adapters/{pinecone,bigquery,pgvector}_adapter.py` (the three launch adapters)
- `scripts/checks/{tier1_config,tier2_content,tier3_retrieval}.py` (the check implementations)
- `scripts/failure_classifier/{heuristic,llm_assisted}.py` (why-did-this-query-miss analysis)
- `scripts/ground_truth/{loader,generator}.py` (CSV loading, synthetic generation)
- `scripts/renderers/{json,markdown,html}_renderer.py` (output formats)
- `tests/` (pytest, see Testing below)

`IMPLEMENTATION_PLAN.md` has the ordered work plan. Read it after this file.

## Sibling skills (read these for pattern conformity)

This skill is the fourth in a collection. The first three are at `bhaelen/bhaelen_sample_skills` on GitHub:

- `bigquery-table-evaluator-skill`
- `score-table-metadata-skill`
- `mcp-schema-linter`

When in doubt about a convention (CLI flag naming, output file naming, JSON schema shape, rubric YAML structure, HTML dashboard styling), look at how the three siblings do it. Follow their lead. Consistency across the collection is more valuable than local optimization. If you find a place where this skill genuinely needs to diverge from the siblings, stop and surface the divergence rather than just making the call.

## Architectural decisions already made (do not relitigate)

These are settled. If you find yourself wanting to change one, stop and ask.

**Three adapters at launch. No more, no fewer.** Pinecone, BigQuery VECTOR_SEARCH, pgvector. Adding more is a follow-on, not in scope. If implementing one is blocked, surface the blocker, do not silently add a fourth.

**Sampling is the default for content audits. Full read is opt-in.** The `--full` flag overrides the default `--sample-size 10000`. Sampling strategy is adapter-specific but must be statistically representative (random, not "first N").

**Failure classifier is heuristic by default. LLM-assisted is opt-in.** The `--llm-assist` flag enables the LLM path. Heuristic path must work without any API keys.

**Three output formats. Always all three available.** JSON, Markdown, HTML. The HTML is the showpiece (per-query failure drill-down). All three are produced by the same scoring pass; the format flag selects which is written.

**Rubric is YAML, configurable via `--rubric-config`.** The default lives at `scripts/rubric.yaml`. Users can override with a custom YAML. Both must validate against the same schema.

**Ground truth is CSV.** Not JSON, not Parquet, not a proprietary format. CSV with `query`, `relevant_doc_ids` (semicolon-separated), and optional columns. Simple, portable, version-controllable.

**Synthetic ground truth is opt-in and clearly labeled.** When used, the output prominently flags Tier 3 results as derived from synthetic ground truth, with an explanation of the limitation (queries tend to use source-document vocabulary, inflating scores).

## Constraints that bound the work

**Dependencies kept tight.** `numpy`, `pandas`, `pyyaml` always. `pinecone-client`, `google-cloud-bigquery`, `psycopg2-binary` per adapter (optional). `anthropic` only when using `--llm-assist`. No PyTorch. No FAISS. No embedding-model loading inside the skill. The skill audits existing embeddings; it does not produce them.

**Python 3.10+.** Match the sibling skills. Use modern type hints (`list[str]` not `List[str]`, `str | None` not `Optional[str]`). The `base.py` adapter currently uses `Optional` in a few places; keep consistent within the module or migrate the whole file at once.

**No web framework, no UI framework.** The HTML dashboard is generated as a single self-contained file with inline CSS and inline JS (no external scripts, no CDN dependencies). Open in any browser, works offline. Look at how `mcp-schema-linter` does its HTML for the pattern.

**No mutating writes.** The skill is read-only against the target store. No `--fix` mode that modifies the store directly. Suggestions go in the output; the user applies them.

**Honest about limitations.** Output should never overclaim. If the skill can't tell whether the embedding model is the problem, it says "consistent with embedding model issues" rather than "the embedding model is the problem." If ground truth is small, the report warns about confidence intervals. The skill's value depends on it being trusted; don't oversell.

## Testing approach

Pytest. Each module has a corresponding test file under `tests/`. Three categories:

**Unit tests.** Each check function tested in isolation with synthetic inputs. Fast, deterministic, no external dependencies. The bulk of the test suite.

**Adapter integration tests.** Marked `@pytest.mark.integration`, skipped by default. Run against real (or local mocked) Pinecone, BigQuery, pgvector instances. Required to pass before adapter changes ship.

**End-to-end tests.** A handful, covering the three subcommands (`audit-config`, `audit-content`, `evaluate`) against a fixture vector store. Verify exit codes, output files exist, JSON schema validates.

Aim for ~80% line coverage on the check modules and the rubric loader. Adapters and renderers can be lower (more I/O, harder to mock, lower payoff from coverage).

## Common pitfalls

**Don't load the entire store into memory.** Even with sampling, prefer streaming iteration. The adapter `iter_vectors` returns an iterator for a reason. Tier 2 checks should consume it in a single pass where possible.

**Don't assume metadata schemas are consistent.** Different stores have different conventions. The base `VectorRecord.metadata` is `dict[str, Any]`. Checks that depend on specific fields should declare them and degrade gracefully when they're missing.

**Don't trust adapter `describe()` to be complete.** Many stores don't report every field. `StoreConfig` fields are mostly Optional. Checks tolerate None as "unknown" and downgrade severity accordingly (warn → info, fail → warn).

**Don't conflate similarity score with relevance.** A vector with a 0.95 cosine similarity to a query is not necessarily relevant. Tier 3 scoring uses ground truth labels, not similarity scores. Resist the temptation to "validate" relevance by similarity threshold.

**Don't reimplement what the store does well.** Each store has native sampling, deduplication detection, and stats APIs. Use them where available. Falling back to client-side computation is fine, but native is faster and more accurate.

## When to stop and ask

Surface these rather than deciding silently:

- A check would require a dependency outside the constraint list (PyTorch, FAISS, an embedding model, etc.).
- A criterion in the rubric has thresholds that need domain-specific calibration (e.g., "what's a reasonable max_cluster_concentration?"). Make a choice but flag it.
- An adapter's API has changed in a way that breaks the base contract. Surface, don't silently work around.
- A check would require write access to the store. Don't.
- A check would require the embedding model. Don't unless explicitly through the adapter's `embed_text` method.
- The siblings' patterns suggest one approach but it doesn't fit this skill cleanly. Surface the conflict.

## Sequencing

Read `IMPLEMENTATION_PLAN.md` next. It has the ordered task list. Implement in the order given. The order is dependency-driven; later tasks rely on earlier ones being stable.

## Style and conventions

Match the sibling skills. A few specifics worth naming:

- Type hints throughout, modern syntax.
- Docstrings on every public function and class, Google style.
- Logging via `logging` module, not `print`. INFO for progress, WARNING for surprises, ERROR for failures.
- Click for CLI rather than argparse if the siblings use it; argparse if they do. Verify against `mcp-schema-linter/scripts/`.
- Black formatting, line length 100.
- No emoji in code, comments, or output. Plain text only.
- No exclamation points in user-facing output. Plain, factual reporting.

## The user behind this hand-off

The skill is being built by Bennie Haelen, a Principal Data and AI Architect at Insight. He has three other skills already published in the same collection and writes for O'Reilly on enterprise AI architecture. His style is direct, technically precise, and avoids over-claiming. When in doubt about tone in user-facing strings (CLI help text, output messages, README content), err toward understatement and toward naming limitations honestly. He explicitly does not use em-dashes. The collection has a consistent voice; preserve it.
