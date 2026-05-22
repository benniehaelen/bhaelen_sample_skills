"""Unit tests for the JSON renderer.

Builds a representative scorecard, renders it, and verifies the JSON parses,
carries the documented top-level keys, enriches each result with its rubric
name/tier/weight, preserves evidence (including numpy values), and reflects the
critical findings and top fixes.
"""

from __future__ import annotations

import json

import numpy as np

from renderers import json_renderer
from rubric import Rubric
from scoring import CheckResult, compute_score


def _rubric() -> Rubric:
    return Rubric.from_dict(
        {
            "tier_weights": {"tier_1_config": 25, "tier_2_content": 30, "tier_3_retrieval": 45},
            "criteria": [
                {"id": "c1", "name": "One", "tier": "tier_1_config", "weight": 60,
                 "severity": "critical", "description": "d", "threshold": {}},
                {"id": "c2", "name": "Two", "tier": "tier_1_config", "weight": 40,
                 "severity": "warn", "description": "d", "threshold": {}},
                {"id": "c3", "name": "Three", "tier": "tier_2_content", "weight": 100,
                 "severity": "fail", "description": "d", "threshold": {}},
                {"id": "c4", "name": "Four", "tier": "tier_3_retrieval", "weight": 100,
                 "severity": "fail", "description": "d", "threshold": {}},
            ],
        }
    )


def _scorecard():
    rubric = _rubric()
    results = [
        CheckResult("c1", passed=False, score=0.0, severity="critical", evidence={"dimension": None}, message="bad"),
        CheckResult("c2", passed=True, score=1.0, severity="warn", evidence={"ok": True}, message="fine"),
        CheckResult("c3", passed=False, score=0.5, severity="warn",
                    evidence={"rate": np.float64(0.12)}, message="dupes"),
    ]
    metadata = {"store": {"store_type": "pinecone", "index": "kb"}, "modes": ["audit-config"]}
    return compute_score(rubric, results, metadata=metadata)


def test_render_is_valid_json():
    text = json_renderer.render(_scorecard())
    data = json.loads(text)
    assert isinstance(data, dict)


def test_top_level_keys_present():
    data = json_renderer.to_dict(_scorecard())
    for key in (
        "schema_version", "scored_at", "overall_score", "grade", "tier_scores",
        "summary", "critical_findings", "top_fixes", "results", "metadata",
    ):
        assert key in data
    assert data["schema_version"] == json_renderer.SCHEMA_VERSION


def test_results_enriched_from_rubric():
    data = json_renderer.to_dict(_scorecard())
    by_id = {r["criterion_id"]: r for r in data["results"]}
    assert by_id["c1"]["name"] == "One"
    assert by_id["c1"]["tier"] == "tier_1_config"
    assert by_id["c1"]["weight"] == 60


def test_evidence_preserved_and_numpy_serializes():
    text = json_renderer.render(_scorecard())
    data = json.loads(text)
    c3 = next(r for r in data["results"] if r["criterion_id"] == "c3")
    # numpy float coerced to a JSON number.
    assert abs(c3["evidence"]["rate"] - 0.12) < 1e-9
    assert isinstance(c3["evidence"]["rate"], float)


def test_critical_findings_and_summary():
    data = json_renderer.to_dict(_scorecard())
    assert data["critical_findings"] == ["c1"]
    assert data["summary"]["criteria_evaluated"] == 3
    assert data["summary"]["passed"] == 1
    assert data["summary"]["failing_by_severity"]["critical"] == 1
    assert data["summary"]["failing_by_severity"]["warn"] == 1


def test_top_fixes_ordered_and_weighted():
    data = json_renderer.to_dict(_scorecard())
    fixes = data["top_fixes"]
    # c1 is critical so it ranks first; c3 is warn.
    assert fixes[0]["criterion_id"] == "c1"
    assert fixes[0]["weight"] == 60


def test_metadata_passed_through():
    data = json_renderer.to_dict(_scorecard())
    assert data["metadata"]["store"]["index"] == "kb"
    assert data["metadata"]["modes"] == ["audit-config"]


def test_scored_at_uses_metadata_when_present():
    rubric = _rubric()
    card = compute_score(rubric, [], metadata={"scored_at": "2026-01-01T00:00:00+00:00"})
    assert json_renderer.to_dict(card)["scored_at"] == "2026-01-01T00:00:00+00:00"


def test_output_validates_against_required_keys_of_schema():
    # Lightweight structural check against output_schema.json without a schema
    # library: every required top-level key the schema lists is present.
    from pathlib import Path

    schema_path = Path(__file__).resolve().parent.parent / "scripts" / "output_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    data = json_renderer.to_dict(_scorecard())
    for key in schema["required"]:
        assert key in data
