# vector-store-linter

Audit and score a production vector store against a quality rubric covering configuration, content health, and retrieval performance. Produces a 0-100 score, an A-F grade, and a top-fixes list ranked by impact.

Designed for teams running RAG in production who want to know whether the foundation under their LLM is sound. Most "my RAG isn't working" problems are vector store problems, not model problems. This skill names them.

## What it scores

Three tiers, weighted by deployment context.

**Tier 1: Configuration and shape.** Dimension consistency, distance metric appropriateness, index type and parameters for the deployment scale, freshness configuration, replication, metadata schema, embedding model recording. Runs without reading any vectors.

**Tier 2: Content quality.** Exact and near-duplicate detection, chunk size distribution, empty content, metadata completeness, embedding distribution analysis, orphan references, embedding model homogeneity. Samples or reads the full store, configurable.

**Tier 3: Retrieval quality.** Recall, precision, NDCG, MRR, and hit rate at multiple k values, plus per-query failure analysis with classified failure modes. Requires a ground-truth file.

## Supported stores

- Pinecone
- BigQuery VECTOR_SEARCH
- Postgres + pgvector

The adapter interface is documented; additional stores can be added without changes to the rest of the skill.

## Install

```bash
pip install -r requirements.txt
```

Only the adapter packages for stores you actually use are required. The skill detects what's installed and reports clearly when a requested adapter is unavailable.

Required for all configurations:
- `numpy` (distribution analysis)
- `pandas` (ground-truth handling)
- `pyyaml` (rubric loading)

Adapter-specific:
- Pinecone: `pinecone-client`
- BigQuery: `google-cloud-bigquery`
- pgvector: `psycopg2-binary`

Optional:
- `anthropic` (only if using `--llm-assist` for failure-mode classification or `generate-ground-truth`)

## Quickstart

Audit a Pinecone index's configuration:

```bash
python scripts/lint.py audit-config \
  --store pinecone \
  --index my-knowledge-base \
  --output-format html \
  --output report.html
```

Audit content (samples 10,000 vectors by default):

```bash
python scripts/lint.py audit-content \
  --store bigquery \
  --table project.dataset.embeddings \
  --output-format markdown
```

Evaluate retrieval against ground truth:

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

The adapters do not embed query text (their `embed_text` returns None), so `evaluate` needs the query vectors supplied. `--query-embeddings` points at a JSON object mapping each query string to its embedding vector:

```json
{
  "What is our 30-day readmission rate?": [0.012, -0.044, 0.910, ...],
  "How are hospice discharges handled?": [0.103, 0.221, -0.087, ...]
}
```

Embed the queries with the same model that populated the store. Queries without an entry are skipped with a warning.

## Ground truth

Tier 3 requires a labeled CSV. One row per query.

```csv
query,relevant_doc_ids,query_type,notes
"What is our 30-day readmission rate?",doc_142;doc_891,metric_definition,"Tests metric retrieval"
"How are hospice discharges handled?",doc_237;doc_558,clinical_rule,"Tests rule retrieval"
```

If you don't have ground truth, the skill can generate a synthetic set from the store contents:

```bash
python scripts/lint.py generate-ground-truth \
  --store pinecone \
  --index my-kb \
  --output queries.csv \
  --queries-per-doc 2 \
  --sample-size 100
```

Synthetic ground truth has known limitations (queries generated from a document tend to use that document's vocabulary, inflating retrieval scores). The output is always marked as synthetic and the report flags Tier 3 results derived from synthetic ground truth.

## CI integration

Exit non-zero when expectations are not met:

```bash
python scripts/lint.py evaluate \
  --store pinecone --index my-kb \
  --ground-truth queries.csv \
  --query-embeddings query_vectors.json \
  --expect-min-recall-at-10 0.75 \
  --expect-max-duplicate-rate 0.02 \
  --expect-zero-orphans
```

Suitable for use as a GitHub Action step or pre-deployment gate. Exit codes: `0` success, `1` an expectation was not met, `2` a usage or input error, `3` a store connection error. The Tier 2 expectation flags require `audit-content` or `evaluate`, and `--expect-min-recall-at-10` requires `evaluate`; using one on a subcommand that does not reach that tier is a usage error.

## Output

Three formats, produced by the same scoring pass.

- **JSON** for CI, programmatic use, agent consumption.
- **Markdown** for terminal review and PR comments.
- **HTML dashboard** for sharing and for the per-query failure drill-down. Self-contained, no external dependencies, opens in any browser.

The HTML dashboard is the most useful for investigation. For each failing query it shows the expected documents, the actually retrieved documents, similarity scores, and a heuristic classification of why the retrieval missed (vocabulary mismatch, semantic distance, missing metadata filter, chunking artifact, boilerplate pollution, stale content, model drift).

Pre-rendered samples are committed in [`examples/`](examples/): `sample_audit.html` and `sample_audit.json` (a content audit) and `sample_eval.html` and `sample_eval.json` (a retrieval evaluation with the per-query drill-down). Regenerate them with `python examples/render_sample_outputs.py`.

## Custom rubrics

The default rubric in `scripts/rubric.yaml` reflects general best practice. Override with `--rubric-config path/to/custom.yaml` for domain-specific checks. The rubric format supports disabling criteria, changing weights, adding new criteria, and adjusting thresholds.

## Using with Claude (recommended)

The skill is designed to be invoked through Claude Code or Claude.ai:

```
claude-code install vector-store-linter
```

Example prompts:

> "Audit the configuration of my Pinecone index `my-kb`."

> "Run a content audit on the BigQuery vector table `project.dataset.embeddings`. Sample 5000 vectors."

> "Evaluate retrieval against the ground truth in `queries.csv` and tell me which queries are failing and why."

> "Generate a synthetic ground-truth file from my pgvector store, then evaluate against it. Be honest about what the synthetic results mean."

Direct CLI usage works in environments without an agent.

## Limitations

Worth knowing before you trust the output.

The skill audits embeddings; it does not see the embedding model. When the diagnosis points at "the embedding model is the problem," that's a guess based on retrieval failure patterns, not a verified finding.

Tier 3 metrics are only as good as the ground truth. Small ground truth produces noisy scores. Mislabeled ground truth produces wrong scores. Synthetic ground truth produces inflated scores.

Near-duplicate detection uses a default similarity threshold of 0.98. Configurable per rubric. Lower thresholds catch more duplicates and produce more false positives.

Full content audits on stores larger than 10M vectors are slow. Sampling (the default) is statistically representative but can miss localized issues in unsampled regions.

The failure-mode classifier is heuristic by default. Enable `--llm-assist` for better accuracy at the cost of API calls. Both paths classify against the failure-mode taxonomy in the rubric, so editing `failure_modes:` in a custom `--rubric-config` changes the modes the LLM is allowed to return.

Some failure-mode detectors compare against the relevant documents' own content (vocabulary mismatch, chunking artifact, model drift). The skill fetches those documents by id where the store supports it (Pinecone, and BigQuery or pgvector when an id column is present); when a store cannot fetch by id, those detectors stay quiet. Stale-content detection uses the newest timestamp found across the sampled vectors' date metadata as its reference for current, so it fires only when the sample carries recognizable date fields.

Retrieval scores are similarities for Pinecone (higher is closer) and distances for BigQuery and pgvector (lower is closer). Results are ordered best-first in all cases, so the metrics are unaffected, but raw score values are not comparable across stores.

## Related skills

This skill is part of a collection that audits the foundations of enterprise AI data platforms:

- [`bigquery-table-evaluator`](https://github.com/bhaelen/bhaelen_sample_skills/tree/main/bigquery-table-evaluator-skill): evaluates the structured data layer
- [`score-table-metadata`](https://github.com/bhaelen/bhaelen_sample_skills/tree/main/score-table-metadata-skill): scores documentation over the structured data
- [`mcp-schema-linter`](https://github.com/bhaelen/bhaelen_sample_skills/tree/main/mcp-schema-linter): audits the tool catalog the LLM uses
- **`vector-store-linter`**: audits the unstructured data layer (this skill)

Together they cover the data, metadata, tools, and retrieval surfaces of a typical enterprise AI platform.

## License

MIT.
