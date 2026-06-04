#!/usr/bin/env bash
# Example: lint the bundled batch of candidate queries in static mode with a
# tenant registry and a CI gate. Run from the skill root.
#
#   bash examples/lint_queries.sh
#
set -euo pipefail

python scripts/lint_sql.py \
  --sql-file examples/candidate_queries.sql \
  --mode static \
  --tenant-registry examples/tenants.json \
  --output-json report.json \
  --output-md report.md \
  --output-html report.html \
  --theme auto \
  --expect-no-violations-at high || true   # gate exits 3 on the dirty examples; keep going so the report still lands

echo "Wrote report.json, report.md, report.html"
