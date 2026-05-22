"""Unit tests for the rubric loader and validator.

Covers a valid load (both the shipped rubric.yaml and a minimal in-memory
rubric) and each documented failure case: missing field, duplicate id, weights
summing wrong (both at the tier level and within a tier), invalid severity,
invalid tier, and a few edge cases the validator is expected to catch.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from rubric import (
    Criterion,
    FailureMode,
    Rubric,
    RubricError,
    validate_rubric,
)

_RUBRIC_YAML = Path(__file__).resolve().parent.parent / "scripts" / "rubric.yaml"


def _valid_rubric_dict() -> dict:
    """A minimal rubric that passes validation.

    One criterion per tier, each weighted 100 so the per-tier sums hold; tier
    weights sum to 100. Tests deep-copy and mutate this for the failure cases.
    """
    return {
        "tier_weights": {"tier_1_config": 25, "tier_2_content": 30, "tier_3_retrieval": 45},
        "criteria": [
            {
                "id": "c_one",
                "name": "One",
                "tier": "tier_1_config",
                "weight": 100,
                "severity": "critical",
                "description": "First criterion.",
                "threshold": {},
            },
            {
                "id": "c_two",
                "name": "Two",
                "tier": "tier_2_content",
                "weight": 100,
                "severity": "warn",
                "description": "Second criterion.",
                "threshold": {"max_duplicate_rate": 0.01},
            },
            {
                "id": "c_three",
                "name": "Three",
                "tier": "tier_3_retrieval",
                "weight": 100,
                "severity": "fail",
                "description": "Third criterion.",
                "threshold": {"min": 0.7},
            },
        ],
        "failure_modes": [
            {"id": "fm_one", "name": "Mode one", "description": "A failure mode."},
        ],
    }


# ----- valid loads ---------------------------------------------------------


def test_load_shipped_rubric():
    rubric = Rubric.load(_RUBRIC_YAML)
    assert isinstance(rubric, Rubric)
    assert len(rubric.criteria) == 25
    assert rubric.tier_weights == {
        "tier_1_config": 25,
        "tier_2_content": 30,
        "tier_3_retrieval": 45,
    }
    assert len(rubric.by_tier("tier_1_config")) == 9
    assert len(rubric.by_tier("tier_2_content")) == 8
    assert len(rubric.by_tier("tier_3_retrieval")) == 8
    assert len(rubric.failure_modes) == 7


def test_shipped_rubric_tier_weights_sum_to_100():
    rubric = Rubric.load(_RUBRIC_YAML)
    for tier in rubric.tier_weights:
        assert sum(c.weight for c in rubric.by_tier(tier)) == 100


def test_by_id_returns_criterion():
    rubric = Rubric.load(_RUBRIC_YAML)
    criterion = rubric.by_id("c_dimension_consistency")
    assert isinstance(criterion, Criterion)
    assert criterion.tier == "tier_1_config"
    assert criterion.severity == "critical"


def test_by_id_missing_raises_keyerror():
    rubric = Rubric.load(_RUBRIC_YAML)
    with pytest.raises(KeyError, match="c_does_not_exist"):
        rubric.by_id("c_does_not_exist")


def test_from_dict_valid():
    rubric = Rubric.from_dict(_valid_rubric_dict())
    assert len(rubric.criteria) == 3
    assert isinstance(rubric.failure_modes[0], FailureMode)
    assert rubric.by_id("c_two").threshold == {"max_duplicate_rate": 0.01}


def test_threshold_defaults_to_empty_dict_when_omitted():
    data = _valid_rubric_dict()
    del data["criteria"][0]["threshold"]
    rubric = Rubric.from_dict(data)
    assert rubric.by_id("c_one").threshold == {}


def test_failure_modes_optional():
    data = _valid_rubric_dict()
    del data["failure_modes"]
    rubric = Rubric.from_dict(data)
    assert rubric.failure_modes == []


# ----- failure cases -------------------------------------------------------


def test_missing_required_field():
    data = _valid_rubric_dict()
    del data["criteria"][1]["name"]
    with pytest.raises(RubricError, match="missing required field 'name'"):
        validate_rubric(data)


def test_missing_field_present_but_none():
    data = _valid_rubric_dict()
    data["criteria"][1]["description"] = None
    with pytest.raises(RubricError, match="missing required field 'description'"):
        validate_rubric(data)


def test_duplicate_criterion_id():
    data = _valid_rubric_dict()
    data["criteria"][1]["id"] = "c_one"
    with pytest.raises(RubricError, match="Duplicate criterion id 'c_one'"):
        validate_rubric(data)


def test_tier_weights_wrong_sum():
    data = _valid_rubric_dict()
    data["tier_weights"]["tier_1_config"] = 50  # now sums to 125
    with pytest.raises(RubricError, match="tier_weights must sum to 100"):
        validate_rubric(data)


def test_criterion_weights_wrong_sum_within_tier():
    data = _valid_rubric_dict()
    data["criteria"][0]["weight"] = 90  # tier_1_config now sums to 90
    with pytest.raises(RubricError, match="tier 'tier_1_config' must sum to 100"):
        validate_rubric(data)


def test_invalid_severity():
    data = _valid_rubric_dict()
    data["criteria"][0]["severity"] = "blocker"
    with pytest.raises(RubricError, match="invalid severity 'blocker'"):
        validate_rubric(data)


def test_invalid_tier():
    data = _valid_rubric_dict()
    data["criteria"][0]["tier"] = "tier_4_magic"
    with pytest.raises(RubricError, match="invalid tier 'tier_4_magic'"):
        validate_rubric(data)


def test_unknown_tier_in_tier_weights():
    data = _valid_rubric_dict()
    data["tier_weights"]["tier_9_unknown"] = 0
    with pytest.raises(RubricError, match="Unknown tier 'tier_9_unknown'"):
        validate_rubric(data)


def test_bool_weight_rejected():
    data = _valid_rubric_dict()
    data["criteria"][0]["weight"] = True
    with pytest.raises(RubricError, match="weight must be an integer"):
        validate_rubric(data)


def test_threshold_wrong_type():
    data = _valid_rubric_dict()
    data["criteria"][0]["threshold"] = ["not", "a", "dict"]
    with pytest.raises(RubricError, match="threshold must be a mapping"):
        validate_rubric(data)


def test_empty_criteria_list():
    data = _valid_rubric_dict()
    data["criteria"] = []
    with pytest.raises(RubricError, match="non-empty 'criteria' list"):
        validate_rubric(data)


def test_top_level_not_a_mapping():
    with pytest.raises(RubricError, match="must be a mapping at the top level"):
        validate_rubric(["not", "a", "mapping"])


def test_failure_mode_missing_field():
    data = _valid_rubric_dict()
    del data["failure_modes"][0]["description"]
    with pytest.raises(RubricError, match=r"failure_modes\[0\] is missing required field 'description'"):
        validate_rubric(data)


def test_criterion_tier_not_in_tier_weights():
    # A criterion using a valid tier that the tier_weights mapping omits.
    data = _valid_rubric_dict()
    del data["tier_weights"]["tier_3_retrieval"]
    data["tier_weights"]["tier_1_config"] = 70  # keep tier_weights summing to 100 (70 + 30)
    with pytest.raises(RubricError, match="not present in tier_weights"):
        validate_rubric(data)


def test_load_missing_file():
    with pytest.raises(RubricError, match="Rubric file not found"):
        Rubric.load(_RUBRIC_YAML.parent / "no_such_rubric.yaml")
