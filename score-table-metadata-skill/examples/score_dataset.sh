#!/usr/bin/env bash
# Score every table in a dataset, with a CI-style threshold.
set -euo pipefail

python ../scripts/score_table_metadata.py \
  --dataset "${BQ_DATASET:?Set BQ_DATASET as project.dataset}" \
  ${BQ_EXPECT_MIN_SCORE:+--expect-min-score "$BQ_EXPECT_MIN_SCORE"} \
  --output-json scorecard.json \
  --output-md scorecard.md \
  --output-html scorecard.html \
  --theme "${BQ_THEME:-auto}"
