---
name: vector-store-linter
description: Audit and score a production vector store (Pinecone, BigQuery VECTOR_SEARCH, or Postgres pgvector) against a quality rubric covering configuration, content health, and retrieval performance. Use this skill when the user wants to evaluate, audit, profile, or health-check a vector store; diagnose why RAG retrieval is performing poorly; find duplicate or near-duplicate vectors; check whether the store's configuration is sensible for its scale; or measure recall and precision against a ground-truth query set. Produces a 0-100 score and A-F grade per store, per-criterion findings, and a top-fixes list ranked by impact. Supports CI-style gates via expectation flags. Three modes: configuration audit (fast, no data read), content audit (samples or full read of vectors), and retrieval evaluation (requires ground-truth file).
license: MIT
---

# vector-store-linter

## What this skill does

Audits a vector store against a three-tier quality rubric and emits a scorecard with actionable findings.

Tier 1 covers configuration and shape: dimension consistency, distance metric appropriateness, index type and parameters, freshness configuration, metadata schema.

Tier 2 covers content quality: duplicate detection, chunk size distribution, embedding distribution analysis, metadata completeness, orphan and stale entries, embedding model consistency.

Tier 3 covers retrieval quality: recall, precision, NDCG, MRR, hit rate at k, per-query failure analysis. Requires a ground-truth file.

## When to use it

Use this skill whenever the user wants to:

- Audit a vector store as part of a platform health check
- Diagnose why RAG retrieval is returning the wrong documents
- Find and remove duplicate vectors that are inflating retrieval precision artificially
- Verify that the store's configuration matches its deployment scale
- Measure retrieval quality against a labeled query set and produce CI-gateable metrics
- Compare two stores or two snapshots of the same store over time

Do not use this skill for:

- Generating embeddings (the skill audits existing embeddings, it does not produce them)
- Tuning the embedding model (the skill diagnoses symptoms; model selection is upstream)
- Migrating between vector stores (the skill scores stores, it does not move data)

## Supported stores

Three adapters at launch:

- **Pinecone**: managed vector database, most widely used in production RAG deployments
- **BigQuery VECTOR_SEARCH**: native to Google Cloud, common for teams already on BigQuery
- **Postgres + pgvector**: increasingly common as the "good enough" option for teams running Postgres

The adapter interface is documented; additional stores can be added without changes to the rest of the skill.

## How to invoke

Three subcommands.

### Configuration audit

Fast, reads only the store's metadata. Scores Tier 1.

```bash
python scripts/lint.py audit-config \
  --store pinecone \
  --index my-knowledge-base \
  --output-format html \
  --output report.html
```

```bash
python scripts/lint.py audit-config \
  --store bigquery \
  --table project.dataset.embeddings \
  --output-format json
```

```bash
python scripts/lint.py audit-config \
  --store pgvector \
  --connection-string "postgresql://user@host/db" \
  --table embeddings
```

### Content audit

Reads vector content. Scores Tier 1 + Tier 2. Sample by default (10,000 vectors), full read with `--full`.

```bash
python scripts/lint.py audit-content \
  --store pinecone \
  --index my-kb \
  --sample-size 10000 \
  --output-format markdown
```

```bash
python scripts/lint.py audit-content \
  --store bigquery \
  --table project.dataset.embeddings \
  --full \
  --output-format html \
  --output content-report.html
```

### Retrieval evaluation

Requires a ground-truth CSV. Scores all three tiers.

```bash
python scripts/lint.py evaluate \
  --store pinecone \
  --index my-kb \
  --ground-truth queries.csv \
  --query-embeddings query_vectors.json \
  --k 5,10,20 \
  --output-format html \
  --output retrieval-report.html
```

The adapters do not embed query text (`embed_text` returns None), so `evaluate` reads the query vectors from `--query-embeddings`: a JSON object mapping each query string to its embedding vector, embedded with the same model that populated the store. Queries without an entry are skipped with a warning.

Failure-mode classification is heuristic by default. Add `--llm-assist` to enable LLM-based classification of why specific queries missed (requires `ANTHROPIC_API_KEY` or compatible). Both classifiers score against the same failure-mode taxonomy defined under `failure_modes:` in the rubric, so a custom `--rubric-config` that edits the taxonomy is reflected in the LLM prompt and its accepted modes.

For a multi-tenant Pinecone index, pass `--namespace <name>` to scope every read (sampling, search, and id fetch) to one namespace. The flag applies to `--store pinecone` only; supplying it for another store is a usage error.

## Ground-truth format

CSV, one row per query. Required columns: `query`, `relevant_doc_ids` (semicolon-separated). Optional columns: `query_type`, `notes`, `expected_k`.

```csv
query,relevant_doc_ids,query_type,notes
"What is the 30-day readmission rate?",doc_142;doc_891,metric_definition,"Tests metric retrieval"
"How are hospice discharges handled?",doc_237;doc_558,clinical_rule,"Tests rule retrieval"
```

If ground truth is not available, the skill can generate a synthetic set from the store's contents using an LLM. This is opt-in and prominently labeled in output:

```bash
python scripts/lint.py generate-ground-truth \
  --store pinecone \
  --index my-kb \
  --output queries.csv \
  --queries-per-doc 2 \
  --sample-size 100
```

Synthetic ground truth has known limitations (generated queries tend to use the source document's vocabulary, inflating retrieval scores). Output is always marked as synthetic.

## Output formats

Three formats. The HTML dashboard is the most useful for sharing and for the per-query drill-down.

- **JSON** for CI integration and programmatic use
- **Markdown** for command-line review and PR comments
- **HTML** for the full self-contained dashboard with per-query failure drill-down

All three are produced by the same scoring pass; the format flag selects which is written.

## CI gates

Exit non-zero when expectations are not met:

```bash
--expect-min-score 80
--expect-min-recall-at-10 0.75
--expect-max-duplicate-rate 0.02
--expect-zero-orphans
--expect-zero-dimension-mismatches
```

`--expect-min-score` and `--expect-zero-dimension-mismatches` apply to any subcommand. The duplicate-rate and orphan gates require `audit-content` or `evaluate`; `--expect-min-recall-at-10` requires `evaluate`. Using a gate on a subcommand that does not reach its tier is a usage error.

Exit codes: `0` success, `1` an expectation was not met, `2` a usage or input error (bad flags, an invalid combination, a missing or malformed file), `3` a store connection error.

## Rubric

The rubric is in `scripts/rubric.yaml`. Override with `--rubric-config path/to/custom.yaml`. Each criterion has a tier, a weight, a check function, and a description used in output. Custom rubrics can disable criteria, change weights, or add domain-specific checks.

## Files

```
vector-store-linter/
├── README.md
├── SKILL.md
├── requirements.txt
├── requirements-dev.txt
├── scripts/
│   ├── lint.py                 # main entry: argparse, dispatch, gates
│   ├── rubric.py               # rubric loader and validator
│   ├── rubric.yaml             # default rubric
│   ├── scoring.py              # scoring engine, Scorecard
│   ├── _validation.py          # rubric and CLI argument validation
│   ├── output_schema.json      # documented JSON output shape
│   ├── adapters/
│   │   ├── base.py             # adapter interface
│   │   ├── errors.py           # StoreConnectionError
│   │   ├── pinecone_adapter.py
│   │   ├── bigquery_adapter.py
│   │   └── pgvector_adapter.py
│   ├── checks/
│   │   ├── _common.py          # shared result and severity helpers
│   │   ├── tier1_config.py
│   │   ├── tier2_content.py
│   │   └── tier3_retrieval.py
│   ├── failure_classifier/
│   │   ├── heuristic.py
│   │   └── llm_assisted.py
│   ├── ground_truth/
│   │   ├── loader.py
│   │   └── generator.py
│   └── renderers/
│       ├── json_renderer.py
│       ├── markdown_renderer.py
│       └── html_renderer.py
├── examples/
│   ├── ground_truth_template.csv
│   ├── render_sample_outputs.py
│   ├── sample_audit.json
│   ├── sample_audit.html
│   ├── sample_eval.json
│   └── sample_eval.html
└── tests/
```

## Using with Claude (recommended)

The skill is designed to be invoked through Claude Code or Claude.ai. Install once and Claude can run it directly:

```
claude-code install vector-store-linter
```

Example prompts:

> "Audit the configuration of my Pinecone index `my-kb`."

> "Run a content audit on the BigQuery vector table `project.dataset.embeddings`. Sample 5000 vectors."

> "Evaluate retrieval against the ground truth in `queries.csv` and tell me what's failing."

> "Generate a ground-truth file from my pgvector index, then evaluate against it."

For environments without an agent in the loop, the Python CLI is the fallback (every example above also works as a direct CLI invocation).

## Limitations

Documented honestly because they affect interpretation:

- The skill audits embeddings, it does not see the embedding model. When the diagnosis points at "the embedding model is the problem," that is a guess based on retrieval failure patterns, not a verified finding.
- The skill does not embed query text. Tier 3 evaluation reads query vectors from `--query-embeddings`; the caller embeds the queries with the store's model.
- Tier 3 metrics are only as good as the ground truth. Synthetic ground truth produces inflated scores; small ground truth produces noisy scores; mislabeled ground truth produces wrong scores.
- Some failure-mode detectors (vocabulary mismatch, chunking artifact, model drift) compare against the relevant documents' content. The skill fetches those documents by id where the store supports it (Pinecone always, BigQuery and pgvector when an id column is present); where it cannot, these detectors stay quiet. Stale-content detection uses the newest date found across the sampled vectors' metadata as its reference for "current," so it fires only when the sample carries recognizable date fields.
- Retrieval scores are similarities for Pinecone (higher is closer) and distances for BigQuery and pgvector (lower is closer). Results are ordered best-first either way, so the metrics are unaffected, but raw score values are not comparable across stores.
- The chunk-size check estimates token counts from character length; there is no tokenizer dependency.
- The duplicate-detection threshold (cosine similarity above 0.98 by default) is a heuristic. Configurable per rubric. Near-duplicate detection is capped at a sample of vectors and subsamples above the cap.
- Performance on very large stores (>10M vectors) in content-audit mode depends on sampling. Full reads at that scale should be expected to take hours.

## License

MIT.
