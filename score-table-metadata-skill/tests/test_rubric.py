"""Rubric tests: pass / partial / fail cases for each criterion + aggregation."""

from __future__ import annotations

import pytest


def _criteria_by_name(block):
    """Index a criterion list by ``name`` for lookup-style assertions."""
    return {c["name"]: c for c in block["criteria"]}


# ---------------------------------------------------------------------------
# Table-level criteria
# ---------------------------------------------------------------------------

class TestTableCriteria:
    """Pass / partial / fail coverage for each of the 8 table-level criteria."""

    def test_business_description_pass(self, rubric_module):
        result = rubric_module.score_table_metadata(
            "Encounter records for inpatient, outpatient, and ED visits.", {}
        )
        assert _criteria_by_name(result)["business_description"]["points"] == 2

    def test_business_description_partial_for_short(self, rubric_module):
        result = rubric_module.score_table_metadata("Events table.", {})
        assert _criteria_by_name(result)["business_description"]["points"] == 1

    def test_business_description_fail_when_missing(self, rubric_module):
        result = rubric_module.score_table_metadata(None, {})
        assert _criteria_by_name(result)["business_description"]["points"] == 0

    def test_grain_pass(self, rubric_module):
        result = rubric_module.score_table_metadata(
            "Encounter records. Grain: one row per encounter version.", {}
        )
        assert _criteria_by_name(result)["grain_statement"]["points"] == 2

    def test_grain_fail(self, rubric_module):
        result = rubric_module.score_table_metadata("Encounter records.", {})
        assert _criteria_by_name(result)["grain_statement"]["points"] == 0

    def test_primary_keys_strong(self, rubric_module):
        result = rubric_module.score_table_metadata(
            "Composite key on (coid, encounter_id).", {}
        )
        assert _criteria_by_name(result)["primary_keys"]["points"] == 2

    def test_primary_keys_weak(self, rubric_module):
        result = rubric_module.score_table_metadata("Has a key field for joining.", {})
        assert _criteria_by_name(result)["primary_keys"]["points"] == 1

    def test_join_guidance_strong(self, rubric_module):
        result = rubric_module.score_table_metadata(
            "Join to patient using foreign key empi_text.", {}
        )
        assert _criteria_by_name(result)["join_guidance"]["points"] == 2

    def test_ownership_via_label(self, rubric_module):
        result = rubric_module.score_table_metadata(
            "An events table.", {"owner": "data-eng"}
        )
        assert _criteria_by_name(result)["ownership"]["points"] == 2

    def test_ownership_via_description(self, rubric_module):
        result = rubric_module.score_table_metadata(
            "An events table. Owner: clinical-data-team.", {}
        )
        assert _criteria_by_name(result)["ownership"]["points"] == 2

    def test_ownership_fail(self, rubric_module):
        result = rubric_module.score_table_metadata("Events table.", {})
        assert _criteria_by_name(result)["ownership"]["points"] == 0

    def test_sensitivity_via_label(self, rubric_module):
        result = rubric_module.score_table_metadata("Events table.", {"phi": "true"})
        assert _criteria_by_name(result)["sensitivity"]["points"] == 2

    def test_sensitivity_via_description_strong(self, rubric_module):
        result = rubric_module.score_table_metadata(
            "Events table. Contains PHI from clinical systems.", {}
        )
        assert _criteria_by_name(result)["sensitivity"]["points"] == 2

    def test_history_rule_pass(self, rubric_module):
        result = rubric_module.score_table_metadata(
            "Type-2 SCD; filter latest_record_ind=1 for current state.", {}
        )
        assert _criteria_by_name(result)["history_rule"]["points"] == 2

    def test_lineage_via_label(self, rubric_module):
        result = rubric_module.score_table_metadata(
            "Events table.", {"source": "adt-feed"}
        )
        assert _criteria_by_name(result)["lineage"]["points"] == 2

    def test_lineage_via_description(self, rubric_module):
        result = rubric_module.score_table_metadata(
            "Loaded from the ADT source system nightly.", {}
        )
        assert _criteria_by_name(result)["lineage"]["points"] == 2


# ---------------------------------------------------------------------------
# Column-level criteria
# ---------------------------------------------------------------------------

class TestColumnCriteria:
    """Per-criterion behavior for column-level scoring, including conditional applicability."""

    def test_has_description_pass(self, rubric_module):
        result = rubric_module.score_column_metadata({
            "name": "user_id", "type": "STRING",
            "description": "Stable identifier for the user across systems.",
        })
        assert _criteria_by_name(result)["has_description"]["points"] == 2

    def test_has_description_partial(self, rubric_module):
        result = rubric_module.score_column_metadata({
            "name": "user_id", "type": "STRING", "description": "user id",
        })
        assert _criteria_by_name(result)["has_description"]["points"] == 1

    def test_has_description_fail(self, rubric_module):
        result = rubric_module.score_column_metadata({
            "name": "user_id", "type": "STRING", "description": None,
        })
        assert _criteria_by_name(result)["has_description"]["points"] == 0

    def test_not_type_echo_fail_on_type_only(self, rubric_module):
        result = rubric_module.score_column_metadata({
            "name": "user_id", "type": "STRING", "description": "string field",
        })
        assert _criteria_by_name(result)["not_type_echo"]["points"] == 0

    def test_not_type_echo_fail_on_name_echo(self, rubric_module):
        result = rubric_module.score_column_metadata({
            "name": "user_id", "type": "STRING", "description": "user_id field",
        })
        assert _criteria_by_name(result)["not_type_echo"]["points"] == 0

    def test_not_type_echo_pass(self, rubric_module):
        result = rubric_module.score_column_metadata({
            "name": "user_id", "type": "STRING",
            "description": "Stable identifier for the user across systems.",
        })
        assert _criteria_by_name(result)["not_type_echo"]["points"] == 2

    def test_coded_field_only_applies_to_coded_names(self, rubric_module):
        # Non-coded name: criterion absent
        plain = rubric_module.score_column_metadata({
            "name": "patient_age", "type": "INT64", "description": "Age in years.",
        })
        assert "coded_field_explained" not in _criteria_by_name(plain)
        # Coded name without explanation: present and failing
        coded_bad = rubric_module.score_column_metadata({
            "name": "discharge_disposition_code", "type": "STRING",
            "description": "The discharge disposition assigned at the encounter close.",
        })
        assert _criteria_by_name(coded_bad)["coded_field_explained"]["points"] == 0
        # Coded name with explanation: passing
        coded_good = rubric_module.score_column_metadata({
            "name": "discharge_disposition_code", "type": "STRING",
            "description": "Coded discharge status; values map to home, transfer, expired, hospice.",
        })
        assert _criteria_by_name(coded_good)["coded_field_explained"]["points"] == 2

    def test_units_or_format_only_applies_to_measure_names(self, rubric_module):
        plain = rubric_module.score_column_metadata({
            "name": "user_id", "type": "STRING", "description": "Stable user identifier.",
        })
        assert "units_or_format" not in _criteria_by_name(plain)
        ts_no_format = rubric_module.score_column_metadata({
            "name": "event_timestamp", "type": "TIMESTAMP",
            "description": "When the event happened.",
        })
        assert _criteria_by_name(ts_no_format)["units_or_format"]["points"] == 0
        ts_with_format = rubric_module.score_column_metadata({
            "name": "event_timestamp", "type": "TIMESTAMP",
            "description": "Event time in UTC, ISO 8601 format.",
        })
        assert _criteria_by_name(ts_with_format)["units_or_format"]["points"] == 2

    def test_sensitivity_via_policy_tags(self, rubric_module):
        result = rubric_module.score_column_metadata({
            "name": "patient_email", "type": "STRING",
            "description": "Patient email address.",
            "policy_tags": ["projects/p/locations/us/taxonomies/1/policyTags/2"],
        })
        assert _criteria_by_name(result)["sensitivity_flagged"]["points"] == 2

    def test_sensitivity_via_description(self, rubric_module):
        result = rubric_module.score_column_metadata({
            "name": "patient_email", "type": "STRING",
            "description": "Patient email — PII, do not export.",
        })
        assert _criteria_by_name(result)["sensitivity_flagged"]["points"] == 2

    def test_sensitivity_fail_when_unflagged(self, rubric_module):
        result = rubric_module.score_column_metadata({
            "name": "patient_email", "type": "STRING",
            "description": "The email address provided at registration.",
        })
        assert _criteria_by_name(result)["sensitivity_flagged"]["points"] == 0

    def test_caveats_only_count_when_present(self, rubric_module):
        result_no = rubric_module.score_column_metadata({
            "name": "user_id", "type": "STRING", "description": "Stable user identifier.",
        })
        caveat_crit = _criteria_by_name(result_no)["caveats_present"]
        assert caveat_crit["points"] == 0 and caveat_crit["max"] == 0
        result_yes = rubric_module.score_column_metadata({
            "name": "user_id", "type": "STRING",
            "description": "Stable user identifier. Deprecated — use canonical_user_id instead.",
        })
        caveat_crit = _criteria_by_name(result_yes)["caveats_present"]
        assert caveat_crit["points"] == 2 and caveat_crit["max"] == 2

    @pytest.mark.parametrize("desc", [
        "Order identifier. Primary key — declared but NOT enforced; ~50 duplicates exist by design.",
        "Optional. Note: may be null when the encounter is in progress.",
        "Encounter type. Be aware: legacy values may still appear.",
        "Status code. Caution: overloaded with multiple meanings.",
        "Active flag. Not unique across facilities.",
    ])
    def test_caveats_extended_keywords(self, rubric_module, desc):
        result = rubric_module.score_column_metadata({
            "name": "user_id", "type": "STRING", "description": desc,
        })
        caveat_crit = _criteria_by_name(result)["caveats_present"]
        assert caveat_crit["points"] == 2, f"caveat not detected in: {desc!r}"

    @pytest.mark.parametrize("desc", [
        "Stable identifier. Foreign key to customers.customer_id.",
        "Order total in USD; calculated from order_lines.",
        "Encounter status, from the source ADT feed.",
        "UUID; auto-generated at row creation.",
        "Source-native from upstream ADT system.",
        "Lineage: derived from raw_orders.amount.",
        "Surrogate key.",
    ])
    def test_derived_or_source_status_pass(self, rubric_module, desc):
        result = rubric_module.score_column_metadata({
            "name": "user_id", "type": "STRING", "description": desc,
        })
        crit = _criteria_by_name(result)["derived_or_source_status"]
        assert crit["points"] == 2, f"source/derived not detected in: {desc!r}"

    @pytest.mark.parametrize("desc", [
        "Customer identifier.",
        "Email address provided at signup.",
        "Created on the first interaction.",
    ])
    def test_derived_or_source_status_fail(self, rubric_module, desc):
        result = rubric_module.score_column_metadata({
            "name": "user_id", "type": "STRING", "description": desc,
        })
        crit = _criteria_by_name(result)["derived_or_source_status"]
        assert crit["points"] == 0

    def test_derived_or_source_is_always_applicable(self, rubric_module):
        """Unlike coded/measure/sensitivity criteria, derived_or_source_status applies to every column."""
        result = rubric_module.score_column_metadata({
            "name": "anything", "type": "STRING",
            "description": "A field with no provenance information.",
        })
        assert "derived_or_source_status" in _criteria_by_name(result)


class TestDescriptionSurfacing:
    """The rubric output must carry full descriptions through for the renderer."""

    def test_column_metadata_includes_description(self, rubric_module):
        result = rubric_module.score_column_metadata({
            "name": "x", "type": "STRING", "description": "the actual full text",
        })
        assert result["description"] == "the actual full text"

    def test_table_block_includes_description_and_labels(self, rubric_module):
        result = rubric_module.score_table({
            "table_id": "p.d.t",
            "description": "Full table description here.",
            "labels": {"owner": "team-x", "phi": "true"},
            "columns": [],
        })
        tm = result["table_metadata"]
        assert tm["description"] == "Full table description here."
        assert tm["labels"] == {"owner": "team-x", "phi": "true"}


# ---------------------------------------------------------------------------
# Aggregation: scoring + grade boundaries
# ---------------------------------------------------------------------------

class TestAggregation:
    """End-to-end scoring: weights, grade boundaries, issues list assembly."""

    @pytest.mark.parametrize("score,expected_grade", [
        (100, "A"), (90, "A"), (89, "B"), (80, "B"), (79, "C"),
        (70, "C"), (69, "D"), (60, "D"), (59, "F"), (0, "F"),
    ])
    def test_grade_boundaries(self, rubric_module, score, expected_grade):
        assert rubric_module._grade(score) == expected_grade

    def test_perfect_table_scores_100(self, rubric_module):
        good_desc = (
            "Encounter records for inpatient, outpatient, and ED visits. "
            "Grain: one row per encounter version per coid. "
            "Composite key on (coid, encounter_id). "
            "Join to patient via foreign key empi_text. "
            "Owner: clinical-data-team. "
            "Contains PHI. "
            "Type-2 SCD; filter latest_record_ind=1 for current state. "
            "Loaded from the ADT source system."
        )
        result = rubric_module.score_table({
            "table_id": "p.d.t",
            "description": good_desc,
            "labels": {},
            "columns": [
                {"name": "encounter_id", "type": "STRING",
                 "description": "Stable encounter identifier; foreign key to encounters.id."},
                {"name": "discharge_disposition_code", "type": "STRING",
                 "description": "Coded discharge status from the source ADT feed; values map to home, transfer, expired."},
                {"name": "event_timestamp", "type": "TIMESTAMP",
                 "description": "Encounter close time in UTC, ISO 8601 format. Source-native from ADT."},
            ],
        })
        assert result["score"] == 100
        assert result["grade"] == "A"

    def test_empty_metadata_scores_zero(self, rubric_module):
        result = rubric_module.score_table({
            "table_id": "p.d.t",
            "description": None,
            "labels": {},
            "columns": [
                {"name": "x", "type": "STRING", "description": None},
            ],
        })
        assert result["score"] == 0
        assert result["grade"] == "F"
        assert any("description" in i.lower() for i in result["issues"])

    def test_no_columns_does_not_crash(self, rubric_module):
        result = rubric_module.score_table({
            "table_id": "p.d.t",
            "description": "Has only a description.",
            "labels": {},
            "columns": [],
        })
        assert result["column_metadata"]["column_count"] == 0
        assert "score" in result and 0 <= result["score"] <= 100

    def test_issues_list_includes_table_failures(self, rubric_module):
        result = rubric_module.score_table({
            "table_id": "p.d.t",
            "description": "Just a short blurb that does not say much.",
            "labels": {},
            "columns": [{"name": "id", "type": "STRING", "description": "id field"}],
        })
        joined = " ".join(result["issues"]).lower()
        assert "grain" in joined or "primary" in joined

    def test_issues_list_capped(self, rubric_module):
        result = rubric_module.score_table({
            "table_id": "p.d.t",
            "description": None,
            "labels": {},
            "columns": [
                {"name": f"col_{i}", "type": "STRING", "description": None}
                for i in range(20)
            ],
        })
        # 8 table issues + 5 column issues max
        assert len(result["issues"]) <= 8 + 5


# ---------------------------------------------------------------------------
# Pattern detectors
# ---------------------------------------------------------------------------

class TestNamePatternDetectors:
    """Verify which column names trip the conditional column criteria."""

    @pytest.mark.parametrize("name,expected", [
        ("discharge_disposition_code", True),
        ("status", False),
        ("encounter_status", True),
        ("admission_type", True),
        ("active_flag", True),
        ("user_id", False),
    ])
    def test_is_coded_name(self, rubric_module, name, expected):
        assert rubric_module._is_coded_name(name) is expected

    @pytest.mark.parametrize("name,expected", [
        ("event_timestamp", True),
        ("amount_usd", True),
        ("user_count", True),
        ("response_rate", True),
        ("user_id", False),
    ])
    def test_is_measure_name(self, rubric_module, name, expected):
        assert rubric_module._is_measure_name(name) is expected

    @pytest.mark.parametrize("name,expected", [
        ("patient_email", True),
        ("ssn", True),
        ("first_name", True),
        ("zip_code", True),
        ("user_id", False),
    ])
    def test_is_sensitive_name(self, rubric_module, name, expected):
        assert rubric_module._is_sensitive_name(name) is expected
