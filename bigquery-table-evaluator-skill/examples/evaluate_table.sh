#!/usr/bin/env bash
# Demonstrates a CI-style run: full data checks, scan-cap, expectations, and
# optional schema-drift comparison against a prior report.
set -euo pipefail

python ../scripts/evaluate_bigquery_table.py \
  --table "${BQ_TABLE:?Set BQ_TABLE as project.dataset.table}" \
  --run-data-checks \
  --check-partitions \
  --freshness-col "${BQ_FRESHNESS_COL:-}" \
  --key-cols "${BQ_KEY_COLS:-}" \
  --where "${BQ_WHERE:-}" \
  --max-bytes-billed "${BQ_MAX_BYTES_BILLED:-1073741824}" \
  --sample-limit "${BQ_SAMPLE_LIMIT:-5}" \
  ${BQ_BASELINE:+--baseline "$BQ_BASELINE"} \
  ${BQ_EXPECT_MIN_ROWS:+--expect-min-rows "$BQ_EXPECT_MIN_ROWS"} \
  ${BQ_EXPECT_FRESHNESS_WITHIN:+--expect-freshness-within "$BQ_EXPECT_FRESHNESS_WITHIN"} \
  ${BQ_EXPECT_ZERO_DUPLICATES:+--expect-zero-duplicates} \
  ${BQ_EXPECT_NO_SCHEMA_DRIFT:+--expect-no-schema-drift} \
  --output-json report.json \
  --output-md report.md \
  --output-html report.html
