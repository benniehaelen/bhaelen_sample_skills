# Implementation Plan

Read `CLAUDE.md` first if you have not already.

This document is the ordered task list for completing the skill. Tasks are sequenced by dependency. Within a phase, tasks can be done in parallel; across phases, earlier phases should be stable before later ones begin.

Each task has:
- **Scope:** what's in and what's out.
- **Done criteria:** specific, testable.
- **Contract:** the input and output shape for the module.
- **Notes:** known gotchas, open questions, where to look for prior art.

## Phase 1: Foundation (no skill behavior yet, but everything depends on it)

### Task 1.1: Rubric loader and validator

**Scope.** Load `scripts/rubric.yaml` into typed Python objects. Validate that the rubric structure is correct, weights sum to expected totals, severities are valid, criteria IDs are unique. Support loading a custom rubric path via `--rubric-config`.

**Done criteria.**
- `Rubric.load(path)` returns a typed `Rubric` object with criteria grouped by tier.
- Invalid rubrics fail loudly with a clear error message naming the problem.
- Unit tests cover: valid load, missing required field, duplicate criterion ID, weights summing wrong, invalid severity, invalid tier.

**Contract.**

```python
@dataclass
class Criterion:
    id: str
    name: str
    tier: str  # "tier_1_config" | "tier_2_content" | "tier_3_retrieval"
    weight: int
    severity: str  # "info" | "warn" | "fail" | "critical"
    description: str
    threshold: dict

@dataclass
class Rubric:
    tier_weights: dict[str, int]
    criteria: list[Criterion]
    failure_modes: list[FailureMode]

    def by_tier(self, tier: str) -> list[Criterion]: ...
    def by_id(self, id: str) -> Criterion: ...

    @classmethod
    def load(cls, path: str | Path) -> "Rubric": ...
```

**Notes.** Use `pyyaml` for parsing. Keep validation logic in a separate function so it can be reused if rubrics are ever loaded from sources other than files. No external schema library (avoid jsonschema as a dependency); validate by hand against the documented shape.

### Task 1.2: Scoring engine

**Scope.** Given a rubric and a set of check results, compute the overall score, the letter grade, and the per-tier breakdown. Handle the rebalance case (Tier 3 skipped when no ground truth).

**Done criteria.**
- `compute_score(rubric, results)` returns a `Scorecard` object.
- Letter grade derived from score: A (90+), B (80-89), C (70-79), D (60-69), F (<60).
- Per-tier scores computed independently and combined via tier weights.
- When Tier 3 is skipped, tier weights rebalance to 40/60/0 automatically.
- Unit tests cover: full evaluation, no-ground-truth case, single tier failure, mixed severities.

**Contract.**

```python
@dataclass
class CheckResult:
    criterion_id: str
    passed: bool
    score: float  # 0.0 to 1.0, fractional credit allowed
    severity: str  # may be downgraded from rubric default
    evidence: dict  # criterion-specific
    message: str  # human-readable finding

@dataclass
class Scorecard:
    overall_score: int  # 0-100
    grade: str  # "A" | "B" | "C" | "D" | "F"
    tier_scores: dict[str, int]  # tier_id -> 0-100
    results: list[CheckResult]
    rubric: Rubric
    metadata: dict  # store info, run timestamp, modes used, etc.

    def critical_findings(self) -> list[CheckResult]: ...
    def top_fixes(self, n: int = 5) -> list[CheckResult]: ...
```

**Notes.** Severity downgrade rule: a criterion with severity "critical" applied to a Tier 1 field that is `None` in the store config should downgrade to "warn" rather than fire critical. The check function decides the downgrade; the scoring engine respects it. `top_fixes` ranks by severity (critical > fail > warn > info) and within severity by weighted score impact.

### Task 1.3: CLI scaffold

**Scope.** The main `lint.py` entry point with three subcommands (`audit-config`, `audit-content`, `evaluate`) plus a fourth (`generate-ground-truth`). Argument parsing only; dispatch to stub functions that print "not implemented" for now.

**Done criteria.**
- `python scripts/lint.py --help` works.
- Each subcommand has its own help.
- All flags from `SKILL.md` documented (`--store`, `--index`, `--table`, `--connection-string`, `--sample-size`, `--full`, `--ground-truth`, `--k`, `--rubric-config`, `--output-format`, `--output`, `--llm-assist`, expectation flags).
- Invalid combinations rejected with a clear error (e.g., `--full` and `--sample-size` together).

**Notes.** Match the sibling skills' CLI library choice. The argparse vs Click decision should follow `mcp-schema-linter`. The CI gate flags (`--expect-min-score` etc.) should produce a non-zero exit code when the expectation isn't met. Use exit codes consistently: 0 success, 1 expectation not met, 2 usage error, 3 store connection error.

## Phase 2: First adapter, first checks (the minimum viable slice)

The goal of Phase 2 is to make `audit-config --store pinecone` work end to end. Once that works, the other adapters and tiers slot in mechanically.

### Task 2.1: Pinecone adapter

**Scope.** Implement `PineconeAdapter(VectorStoreAdapter)` covering `connect`, `describe`, `iter_vectors`, `search`, and `close`. `embed_text` returns None (Pinecone supports server-side embedding via integrations but only for some indexes; safer to require pre-embedded queries).

**Done criteria.**
- Connects to a Pinecone index given an API key and an index name (or host).
- `describe()` returns a populated `StoreConfig` with dimension, vector count, metric, index type, replica/shard counts when available.
- `iter_vectors(sample_size=N)` yields N random vectors with metadata.
- `iter_vectors(sample_size=None)` yields all vectors (paginated, never the full set in memory).
- `search(query_embedding, k)` returns top k results as `RetrievalResult` objects.
- Integration test against a Pinecone test index (marked `@pytest.mark.integration`).

**Notes.** Pinecone's API has changed several times. Target the current stable `pinecone-client` (v3+). API key from `PINECONE_API_KEY` env var by default; allow override. The sampling strategy: Pinecone doesn't have native random sampling, so fetch by random ID prefixes or use `query()` against random query vectors with a high k. Document the strategy chosen.

### Task 2.2: Tier 1 checks (configuration)

**Scope.** Implement all nine Tier 1 criteria from `rubric.yaml`. Each check is a function that takes a `StoreConfig` and returns a `CheckResult`.

**Done criteria.**
- All nine checks implemented:
  - `c_dimension_consistency`
  - `c_distance_metric_appropriate`
  - `c_index_type_for_scale`
  - `c_hnsw_parameters_tuned`
  - `c_freshness_configured`
  - `c_replication_for_scale`
  - `c_metadata_schema_declared`
  - `c_embedding_model_recorded`
  - `c_namespace_or_tenant_isolation`
- Each check tolerates `None` for fields it depends on (downgrades severity).
- Unit tests cover the pass case, the fail case, and the `None`/unknown case for each.

**Contract.** Each check is a function:

```python
def check_dimension_consistency(config: StoreConfig, threshold: dict) -> CheckResult: ...
```

A registry in `checks/tier1_config.py` maps criterion IDs to functions, used by the scoring engine.

**Notes.** `c_distance_metric_appropriate` is the only Tier 1 check that needs to read vectors (to check whether embeddings are normalized). Use a small sample (1000 vectors per the rubric's `sample_size_for_normalization_check`). All other Tier 1 checks are pure config.

### Task 2.3: JSON renderer

**Scope.** Render a `Scorecard` to JSON.

**Done criteria.**
- Output is valid JSON, parseable by standard libraries.
- Schema is stable and documented (consider a separate `output_schema.json` file).
- Includes overall score, grade, per-tier scores, every check result with full evidence, run metadata.
- Unit tests verify the JSON structure for representative scorecards.

**Notes.** The JSON output is what CI consumes. Keep it stable; breaking changes require a version bump in the metadata. Match the sibling skills' JSON schema where possible.

### Task 2.4: End-to-end wire-up for `audit-config`

**Scope.** Connect the pieces: CLI parses args, loads rubric, instantiates Pinecone adapter, runs Tier 1 checks, computes score, renders JSON.

**Done criteria.**
- `python scripts/lint.py audit-config --store pinecone --index test-index --output-format json --output report.json` produces a valid scorecard.
- CI gate flags work (e.g., `--expect-min-score 80` exits non-zero when score is below 80).
- Error handling: missing API key, index not found, connection failure each produce a clean error message and the documented exit code.

**Notes.** This is the milestone that proves the architecture. Once this works, Phase 3 is mostly mechanical.

## Phase 3: Remaining adapters

Phase 3 expands store coverage. Each task is independent of the others.

### Task 3.1: BigQuery adapter

**Scope.** Implement `BigQueryAdapter`. Targets BigQuery VECTOR_SEARCH tables, not generic BigQuery tables.

**Done criteria.**
- Connects given a project, dataset, and table name; authenticates via Application Default Credentials.
- `describe()` reports row count, schema (including vector column), partitioning, clustering.
- `iter_vectors` uses `TABLESAMPLE` for sampling.
- `search` uses `VECTOR_SEARCH` SQL function.
- `embed_text` returns None.
- Integration tests against a BigQuery test table.

**Notes.** BigQuery VECTOR_SEARCH is relatively new; some features (HNSW index, IVF index) may not be available depending on the project's region. The adapter should report what's available rather than assuming the full feature set. Look at `bigquery-table-evaluator-skill` for authentication and connection patterns to reuse.

### Task 3.2: pgvector adapter

**Scope.** Implement `PgVectorAdapter` for Postgres + pgvector extension.

**Done criteria.**
- Connects given a connection string and a table name.
- `describe()` reports row count, vector column dimension, index type (IVF, HNSW), index parameters.
- `iter_vectors` uses `TABLESAMPLE` or `ORDER BY random()` (sample size dependent).
- `search` uses pgvector's `<->` (L2), `<=>` (cosine), or `<#>` (dot) operators based on the index's metric.
- `embed_text` returns None.
- Integration tests against a local pgvector test database.

**Notes.** Connection string from the `--connection-string` flag or `DATABASE_URL` env var. Distance metric detection: query the index's `opclass` to determine which operator to use. Handle the case where the table has no vector index (warn but allow).

## Phase 4: Tier 2 (content checks)

### Task 4.1: Tier 2 checks

**Scope.** Implement all eight Tier 2 criteria. These read vectors through `iter_vectors`.

**Done criteria.**
- All eight checks implemented:
  - `c_no_exact_duplicates`
  - `c_no_near_duplicates`
  - `c_chunk_size_distribution`
  - `c_no_empty_content`
  - `c_metadata_completeness`
  - `c_embedding_distribution`
  - `c_no_orphan_references`
  - `c_embedding_model_homogeneous`
- Checks consume `iter_vectors` once where possible (single pass).
- Sampling respected: if `iter_vectors(sample_size=10000)` is called, checks operate on those 10,000.
- Unit tests using synthetic vector sets.

**Notes.**

`c_no_exact_duplicates` and `c_no_near_duplicates` are the most expensive. Exact duplicate detection is straightforward (hash the vector bytes). Near-duplicate detection at scale is genuinely hard; consider using locality-sensitive hashing or accepting O(n^2) on the sampled set with a documented size limit. The default sample size of 10,000 makes O(n^2) tolerable; document that full-read mode may take a long time.

`c_embedding_distribution` analyzes clustering. Use k-means or DBSCAN on a sample, look at cluster sizes, flag if any cluster is over 30% of vectors. Don't import scikit-learn; implement k-means in numpy. It's a few dozen lines.

`c_no_orphan_references` is the most adapter-dependent. Some stores track source documents in metadata, some don't. The check should degrade gracefully when source tracking isn't present.

### Task 4.2: Wire up `audit-content` end to end

**Scope.** Make `audit-content` work for all three adapters with all eight Tier 2 checks.

**Done criteria.** Same as Task 2.4 but for `audit-content` and Tier 2.

## Phase 5: Tier 3 (retrieval checks)

### Task 5.1: Ground-truth loader

**Scope.** Load and validate ground-truth CSVs.

**Done criteria.**
- `GroundTruth.load(path)` returns a typed object.
- Validates required columns, parses `relevant_doc_ids` from semicolon-separated strings, normalizes optional fields.
- Reports parse errors with line numbers.

**Contract.**

```python
@dataclass
class GroundTruthQuery:
    query: str
    relevant_doc_ids: set[str]
    query_type: str | None
    notes: str | None
    expected_k: int | None

@dataclass
class GroundTruth:
    queries: list[GroundTruthQuery]
    is_synthetic: bool  # True if generated, False if hand-labeled
    metadata: dict  # source info, generation params if synthetic
```

### Task 5.2: Tier 3 metrics

**Scope.** Implement the six retrieval metrics from the rubric (recall@k, precision@k, ndcg@k, mrr, hit_rate@k, query_failure_concentration, no_universal_results).

**Done criteria.**
- Each metric implemented as a function over a list of `(query, expected_ids, retrieved_results)` triples.
- Unit tests cover known-correct examples (e.g., recall@5 with 3 of 5 expected = 0.6).
- Standard metric implementations match well-known references.

**Notes.** Implement these from scratch in numpy/pandas rather than pulling in `pytrec_eval` or similar. The implementations are short and the dependency cost isn't justified.

### Task 5.3: Heuristic failure classifier

**Scope.** Given a query that retrieved poorly, classify the failure mode using only the data available (the query, the retrieved results, the expected documents, similarity scores).

**Done criteria.**
- Implements detection for each failure mode in `rubric.yaml`:
  - `vocabulary_mismatch` (low lexical overlap between query and expected doc)
  - `semantic_distance` (similarity score below threshold)
  - `missing_metadata_filter` (expected doc not in top k, but other less-relevant docs sharing query terms are)
  - `chunking_artifact` (expected info in another chunk of the same source document)
  - `boilerplate_pollution` (one chunk appears in many query results)
  - `stale_content` (timestamp-based heuristic)
  - `model_drift` (embedding model metadata differs)
- Returns one or more classified modes per failing query with confidence.
- Unit tests cover each mode.

**Notes.** Each detector is a function returning a confidence score. Classifications below 0.5 confidence are not reported. The detectors are heuristic; document their limits. False positives are tolerable; false confidence is not.

### Task 5.4: LLM-assisted failure classifier (optional path)

**Scope.** Same interface as the heuristic but uses an LLM to classify. Triggered by `--llm-assist`.

**Done criteria.**
- Uses `anthropic` SDK with a prompt that takes a query, expected docs, retrieved docs, and returns a classified failure mode with reasoning.
- Falls back to heuristic if API key missing or call fails.
- Unit tests use a mocked client.

**Notes.** The prompt should reference the same failure mode taxonomy as the heuristic so output is comparable. Costs roughly $0.01-0.05 per failed query depending on context size; document the cost.

### Task 5.5: Synthetic ground-truth generator

**Scope.** Generate a ground-truth CSV from the store's contents using an LLM. For each sampled document, generate N queries that the document should answer.

**Done criteria.**
- `python scripts/lint.py generate-ground-truth --store ... --queries-per-doc 2 --sample-size 100 --output queries.csv` produces a valid ground-truth file.
- Output is marked as synthetic in the metadata header (a CSV comment or a separate metadata field).
- Documentation explains the limitations prominently.

**Notes.** The prompt to the LLM should not include the source document's exact wording in the generated query (otherwise retrieval is trivially solved). Ask the LLM to generate queries a user might ask whose answer is in the document, using different vocabulary where possible. This won't fully prevent the inflation problem but mitigates it.

### Task 5.6: Wire up `evaluate` end to end

**Scope.** Make `evaluate` work for all three adapters with all Tier 3 metrics, failure classification, and ground truth.

**Done criteria.** Same as Task 2.4 but for `evaluate` and Tier 3.

## Phase 6: Renderers (output polish)

### Task 6.1: Markdown renderer

**Scope.** Render `Scorecard` to Markdown suitable for terminal or PR comment.

**Done criteria.**
- Output renders cleanly in GitHub's markdown, in terminal viewers, in editor previews.
- Includes summary (score, grade), per-tier breakdown, top fixes, failing queries table for Tier 3.
- Sample output verified against `mcp-schema-linter`'s markdown renderer for stylistic consistency.

### Task 6.2: HTML dashboard renderer

**Scope.** Render `Scorecard` to a self-contained HTML file.

**Done criteria.**
- Single HTML file, no external CSS or JS, no CDN dependencies.
- Sections: header (score, grade, run metadata), tier breakdowns, failing queries table with drill-down (click to expand showing expected vs. retrieved docs with similarity scores), top fixes.
- Responsive enough to read on a phone.
- Matches the visual language of the sibling skills' HTML dashboards.

**Notes.** This is the showpiece. Spend the time. Look at `mcp-schema-linter`'s HTML for the pattern (color palette, typography, structure). The drill-down per failing query is the feature that distinguishes a useful report from a useless one.

## Phase 7: Polish, tests, examples

### Task 7.1: Example outputs

**Scope.** Generate sample outputs (`examples/sample_audit.html`, `examples/sample_eval.html`, `examples/sample_audit.json`, etc.) using a known fixture store. Commit them so users can see what to expect without running the skill themselves.

### Task 7.2: End-to-end tests

**Scope.** Test the three subcommands against fixtures.

**Done criteria.** Each subcommand has an end-to-end test that runs the CLI, inspects the output, and verifies exit codes.

### Task 7.3: Documentation pass

**Scope.** Review SKILL.md, README.md, and the rubric YAML for accuracy after implementation. Update where reality diverged from the spec.

**Done criteria.** All examples in the docs match actual behavior. All flags documented match the CLI. All limitations honestly named.

## When to surface to Bennie

Stop and ask before doing any of these:

- Changing the rubric structure or adding/removing criteria
- Adding a new dependency
- Adding a fourth adapter
- Designing a new output format
- Implementing anything that requires write access to the target store
- Implementing anything that requires loading an embedding model

Otherwise, follow the plan in order, surface blockers, and produce work that matches the sibling skills' patterns.
