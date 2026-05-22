#!/usr/bin/env python3
"""Render committed sample outputs from a synthetic fixture store.

Builds a realistic but synthetic vector store (no live service, no API key),
runs it through the real checks and scoring, and writes sample outputs into
examples/ so users can see what a scorecard looks like without running the
skill. Run from the skill root:

    python examples/render_sample_outputs.py

The fixture seed and timestamp are fixed so re-running produces a stable diff.
Needs numpy and pyyaml (already required); no network and no API key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from adapters.base import StoreConfig, VectorRecord  # noqa: E402
from checks.tier1_config import run_tier1  # noqa: E402
from checks.tier2_content import run_tier2  # noqa: E402
from checks.tier3_retrieval import QueryEvaluation, run_tier3  # noqa: E402
from renderers import html_renderer, json_renderer  # noqa: E402
from rubric import Rubric  # noqa: E402
from scoring import compute_score  # noqa: E402

_SCORED_AT = "2026-05-20T12:00:00+00:00"
_PASSAGE = (
    "Encounters are recorded one row per visit. The grain is a single encounter keyed by the "
    "facility identifier and the patient account number. Use the latest-record flag to select "
    "the current version when the source system has issued corrections."
)


def _config() -> StoreConfig:
    return StoreConfig(
        store_type="pinecone",
        vector_count=12000,
        dimension=64,
        distance_metric="cosine",
        index_type="hnsw",
        index_parameters={"M": 16, "ef_search": 64},
        replica_count=1,
        shard_count=1,
        refresh_cadence=None,  # not reported -> a Tier 1 unknown
        metadata_schema={"source": "string", "model": "string", "section": "string"},
        namespaces=None,
        raw={},
    )


def _sample() -> list[VectorRecord]:
    rng = np.random.default_rng(7)
    vectors: list[VectorRecord] = []
    base = rng.standard_normal((140, 64))
    for i in range(140):
        metadata = {"source": f"policy_{i // 5}.md", "model": "text-embedding-3-small"}
        if i % 7 != 0:  # ~20 rows are missing the section field
            metadata["section"] = f"section_{i % 9}"
        vectors.append(VectorRecord(id=f"chunk_{i}", embedding=base[i].tolist(), metadata=metadata, content=_PASSAGE))
    # A handful of exact duplicates (raises the duplicate rate above 1 percent).
    duplicate = base[0].tolist()
    for j in range(5):
        vectors.append(VectorRecord(
            id=f"dup_{j}", embedding=duplicate,
            metadata={"source": "policy_0.md", "model": "text-embedding-3-small", "section": "section_0"},
            content=_PASSAGE,
        ))
    # A few near-empty chunks.
    for j in range(3):
        vectors.append(VectorRecord(
            id=f"empty_{j}", embedding=rng.standard_normal(64).tolist(),
            metadata={"source": "policy_x.md", "model": "text-embedding-3-small", "section": "s"},
            content="   ",
        ))
    return vectors


def _evaluations() -> list[QueryEvaluation]:
    evaluations: list[QueryEvaluation] = []
    for i in range(11):  # queries that retrieve their relevant chunk
        rid = f"chunk_{i}"
        evaluations.append(QueryEvaluation(
            query=f"How is metric {i} defined?",
            expected_ids={rid},
            retrieved_ids=[rid, f"chunk_{i + 20}", f"chunk_{i + 40}", f"chunk_{i + 60}", f"chunk_{i + 80}"],
            query_type="metric_definition",
        ))
    for i in range(4):  # queries that miss
        evaluations.append(QueryEvaluation(
            query=f"What is the rule for edge case {i}?",
            expected_ids={f"missing_{i}"},
            retrieved_ids=[f"chunk_{i}", f"chunk_{i + 1}", f"chunk_{i + 2}"],
            query_type="clinical_rule",
        ))
    return evaluations


def _failing_queries() -> list[dict]:
    return [
        {
            "query": "What is the rule for edge case 0?",
            "query_type": "clinical_rule",
            "expected_ids": ["missing_0"],
            "retrieved": [{"id": "chunk_0", "score": 0.41}, {"id": "chunk_1", "score": 0.39}],
            "classifications": [
                {"mode": "vocabulary_mismatch", "confidence": 0.78,
                 "explanation": "The query shares few terms with the relevant document, a vocabulary gap."},
            ],
        },
        {
            "query": "What is the rule for edge case 1?",
            "query_type": "clinical_rule",
            "expected_ids": ["missing_1"],
            "retrieved": [{"id": "chunk_1", "score": 0.55}, {"id": "chunk_2", "score": 0.52}],
            "classifications": [
                {"mode": "missing_metadata_filter", "confidence": 0.62,
                 "explanation": "Lexically similar documents rank while the relevant one is absent; a filter may be missing."},
            ],
        },
    ]


def _audit_scorecard(rubric, config, sample):
    results = run_tier1(rubric, config, vectors=sample) + run_tier2(rubric, config, sample)
    metadata = {
        "scored_at": _SCORED_AT,
        "store": {"store_type": "pinecone", "index": "knowledge-base"},
        "modes": ["audit-content"],
        "rubric": {"source": "builtin"},
        "sample_size": 10000,
        "sampled_count": len(sample),
        "full_read": False,
    }
    return compute_score(rubric, results, metadata=metadata)


def _eval_scorecard(rubric, config, sample):
    results = (
        run_tier1(rubric, config, vectors=sample)
        + run_tier2(rubric, config, sample)
        + run_tier3(rubric, _evaluations())
    )
    metadata = {
        "scored_at": _SCORED_AT,
        "store": {"store_type": "pinecone", "index": "knowledge-base"},
        "modes": ["evaluate"],
        "rubric": {"source": "builtin"},
        "sample_size": 10000,
        "sampled_count": len(sample),
        "k": [5, 10, 20],
        "ground_truth": {"path": "examples/ground_truth_template.csv", "query_count": 15, "evaluated": 15, "is_synthetic": False},
        "failing_queries": _failing_queries(),
        "llm_assist": False,
        "warnings": [],
    }
    return compute_score(rubric, results, metadata=metadata)


def main() -> int:
    rubric = Rubric.load(_SCRIPTS / "rubric.yaml")
    config = _config()
    sample = _sample()

    audit = _audit_scorecard(rubric, config, sample)
    (_HERE / "sample_audit.json").write_text(json_renderer.render(audit), encoding="utf-8")
    (_HERE / "sample_audit.html").write_text(html_renderer.render(audit, theme="auto"), encoding="utf-8")

    evaluation = _eval_scorecard(rubric, config, sample)
    (_HERE / "sample_eval.json").write_text(json_renderer.render(evaluation), encoding="utf-8")
    (_HERE / "sample_eval.html").write_text(html_renderer.render(evaluation, theme="auto"), encoding="utf-8")

    print(f"sample_audit: {audit.overall_score}/{audit.grade}; sample_eval: {evaluation.overall_score}/{evaluation.grade}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
