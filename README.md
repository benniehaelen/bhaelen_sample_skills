# bhaelen_sample_skills

A collection of sample [Claude Code](https://claude.com/claude-code) skills.

Each top-level directory is one self-contained skill with its own `SKILL.md`, scripts, tests, and README.

## Skills

| Skill | What it does |
| --- | --- |
| [`bigquery-table-evaluator-skill/`](bigquery-table-evaluator-skill/) | Evaluate, audit, profile, or health-check a Google BigQuery table. Reports metadata, schema, partitioning, freshness, duplicate keys, and per-column null rates. Supports CI-style expectation flags and emits JSON / Markdown / a self-contained HTML dashboard. |
| [`score-table-metadata-skill/`](score-table-metadata-skill/) | Score authored metadata quality on BigQuery tables against the data-steward rubric (8 table-level criteria, 6 column-level criteria). Accepts a dataset or list of tables; produces a 0-100 score and A-F grade per table with per-criterion evidence and an actionable issues list. Supports CI-style `--expect-min-score` gate and custom rubrics via `--rubric-config`. |
| [`mcp-schema-linter/`](mcp-schema-linter/) | Lint and score the tool definitions exposed by an MCP server against a token-economy rubric (3 tiers, 15 criteria), with tokens-per-turn as the headline metric. Accepts a live MCP server URL, a `tools/list` dump, or a file; produces a per-tool 0-100 score and A-F grade, the total catalog token cost, and a top-refactor list ranked by tokens saved. Supports CI gates via `--expect-max-tokens` and `--expect-min-score` and custom rubrics via `--rubric-config`. No BigQuery dependency. |

The two BigQuery skills are designed to be invoked through Claude Code or Claude.ai with a shared BigQuery MCP connector. The MCP schema linter is independent: it reads tool definitions from any MCP server (or a static dump) and has no BigQuery dependency. See each skill's README **Using with Claude** section for install steps and example prompts:

- [`bigquery-table-evaluator-skill/README.md#using-with-claude-recommended`](bigquery-table-evaluator-skill/README.md#using-with-claude-recommended)
- [`score-table-metadata-skill/README.md#using-with-claude-recommended`](score-table-metadata-skill/README.md#using-with-claude-recommended)
- [`mcp-schema-linter/README.md#using-with-claude-recommended`](mcp-schema-linter/README.md#using-with-claude-recommended)

For the two BigQuery skills the MCP server setup is identical: install it once and both will use it. Every skill also bundles a Python CLI fallback (Path B) for environments without an agent in the loop. The linter's dependencies are minimal (`tiktoken` and `requests`); the BigQuery skills use `google-cloud-bigquery`.

## License

[MIT](LICENSE)
