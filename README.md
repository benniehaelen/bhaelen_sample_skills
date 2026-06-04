# bhaelen_sample_skills

A collection of sample [Claude Code](https://claude.com/claude-code) skills.

Each top-level directory is one self-contained skill with its own `SKILL.md`, scripts, tests, and README.

## Skills

| Skill | What it does |
| --- | --- |
| [`bigquery-table-evaluator-skill/`](bigquery-table-evaluator-skill/) | Evaluate, audit, profile, or health-check a Google BigQuery table. Reports metadata, schema, partitioning, freshness, duplicate keys, and per-column null rates. Supports CI-style expectation flags and emits JSON / Markdown / a self-contained HTML dashboard. |
| [`score-table-metadata-skill/`](score-table-metadata-skill/) | Score authored metadata quality on BigQuery tables against the data-steward rubric (8 table-level criteria, 6 column-level criteria). Accepts a dataset or list of tables; produces a 0-100 score and A-F grade per table with per-criterion evidence and an actionable issues list. Supports CI-style `--expect-min-score` gate and custom rubrics via `--rubric-config`. |
| [`mcp-schema-linter/`](mcp-schema-linter/) | Lint and score the tool definitions exposed by an MCP server against a token-economy rubric (3 tiers, 15 criteria), with tokens-per-turn as the headline metric. Accepts a live MCP server URL, a `tools/list` dump, or a file; produces a per-tool 0-100 score and A-F grade, the total catalog token cost, and a top-refactor list ranked by tokens saved. Supports CI gates via `--expect-max-tokens` and `--expect-min-score` and custom rubrics via `--rubric-config`. No BigQuery dependency. |
| [`vector-store-linter/`](vector-store-linter/) | Audit a production vector store (Pinecone, BigQuery VECTOR_SEARCH, or Postgres pgvector) against a three-tier quality rubric (configuration, content, retrieval). Produces a 0-100 score and A-F grade with per-criterion findings, a top-fixes list, and per-query failure classification. Read-only, sampling by default with `--full` opt-in; retrieval scoring uses a CSV ground-truth file with optional synthetic generation. Emits JSON / Markdown / a self-contained HTML dashboard and supports CI gates via expectation flags. |
| [`sql-guardrail-linter/`](sql-guardrail-linter/) | Lint generated SQL against a guardrail control catalog (3 tiers, 12 controls) before it runs, answering "would this query be allowed through the control plane." Checks read-only/single-statement safety, comment injection, partition filters, scan cost within a ceiling, cartesian joins, SELECT *, unbounded results, tenant predicates, PII column access, fully qualified tables, and ORDER BY without LIMIT. Accepts a single statement, a file, or a batch from a usage-log table; produces a per-statement 0-100 score and A-F grade with a per-control pass/warn/fail/na finding and a severity-ranked top-fixes list. Never executes the audited SQL; cost is measured with dry-run only. Supports CI gates via `--expect-min-score`, `--expect-no-violations-at`, and `--expect-max-bytes` and custom rubrics via `--rubric-config`. `static` mode needs only `sqlglot`. |

The two BigQuery skills are designed to be invoked through Claude Code or Claude.ai with a shared BigQuery MCP connector. The MCP schema linter is independent: it reads tool definitions from any MCP server (or a static dump) and has no BigQuery dependency. The vector store linter is also independent: it connects directly to the target store and depends only on the adapter package for the store in use. See each skill's README **Using with Claude** section for install steps and example prompts:

- [`bigquery-table-evaluator-skill/README.md#using-with-claude-recommended`](bigquery-table-evaluator-skill/README.md#using-with-claude-recommended)
- [`score-table-metadata-skill/README.md#using-with-claude-recommended`](score-table-metadata-skill/README.md#using-with-claude-recommended)
- [`mcp-schema-linter/README.md#using-with-claude-recommended`](mcp-schema-linter/README.md#using-with-claude-recommended)
- [`vector-store-linter/README.md#using-with-claude-recommended`](vector-store-linter/README.md#using-with-claude-recommended)
- [`sql-guardrail-linter/README.md#using-with-claude-recommended`](sql-guardrail-linter/README.md#using-with-claude-recommended)

For the two BigQuery skills the MCP server setup is identical: install it once and both will use it. Every skill also bundles a Python CLI fallback (Path B) for environments without an agent in the loop. The linter's dependencies are minimal (`tiktoken` and `requests`); the BigQuery skills use `google-cloud-bigquery`; the vector store linter pulls in only the adapter package for the store being audited (`pinecone-client`, `google-cloud-bigquery`, or `psycopg2-binary`).

## License

[MIT](LICENSE)
