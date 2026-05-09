# bhaelen_sample_skills

A collection of sample [Claude Code](https://claude.com/claude-code) skills.

Each top-level directory is one self-contained skill with its own `SKILL.md`, scripts, tests, and README.

## Skills

| Skill | What it does |
| --- | --- |
| [`bigquery-table-evaluator-skill/`](bigquery-table-evaluator-skill/) | Evaluate, audit, profile, or health-check a Google BigQuery table. Reports metadata, schema, partitioning, freshness, duplicate keys, and per-column null rates. Supports CI-style expectation flags and emits JSON / Markdown / a self-contained HTML dashboard. |
| [`score-table-metadata-skill/`](score-table-metadata-skill/) | Score authored metadata quality on BigQuery tables against the data-steward rubric (8 table-level criteria, 6 column-level criteria). Accepts a dataset or list of tables; produces a 0-100 score and A-F grade per table with per-criterion evidence and an actionable issues list. Supports CI-style `--expect-min-score` gate and custom rubrics via `--rubric-config`. |

Both skills are designed to be invoked through Claude Code or Claude.ai with a shared BigQuery MCP connector — see each skill's README **Using with Claude** section for install steps and example prompts:

- [`bigquery-table-evaluator-skill/README.md#using-with-claude-recommended`](bigquery-table-evaluator-skill/README.md#using-with-claude-recommended)
- [`score-table-metadata-skill/README.md#using-with-claude-recommended`](score-table-metadata-skill/README.md#using-with-claude-recommended)

The MCP server setup is identical for both — install it once and both skills will use it. Each skill also bundles a Python CLI fallback (Path B) for environments without an MCP server.

## License

[MIT](LICENSE)
