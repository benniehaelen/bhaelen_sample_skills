"""Tests for the loadable RubricConfig: round-trip, overrides, validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write(tmp_path: Path, body: dict) -> Path:
    p = tmp_path / "rubric.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Round-trip equivalence: shipped default JSON == DEFAULT_CONFIG
# ---------------------------------------------------------------------------

class TestDefaultRoundTrip:
    """The shipped ``examples/rubric_default.json`` must yield DEFAULT_CONFIG."""

    def test_round_trip(self, rubric_module):
        path = Path(__file__).resolve().parent.parent / "examples" / "rubric_default.json"
        loaded = rubric_module.load_rubric_config(path)
        d = rubric_module.DEFAULT_CONFIG
        assert loaded.weights == d.weights
        assert loaded.grade_cutoffs == d.grade_cutoffs
        assert loaded.thresholds == d.thresholds
        assert loaded.keywords == d.keywords
        assert loaded.type_echo_patterns == d.type_echo_patterns
        for trig in ("coded", "measure", "sensitive"):
            assert loaded.column_triggers[trig].pattern == d.column_triggers[trig].pattern

    def test_round_trip_scores_identically(self, rubric_module):
        """Scoring with the loaded default must equal scoring with DEFAULT_CONFIG."""
        path = Path(__file__).resolve().parent.parent / "examples" / "rubric_default.json"
        loaded = rubric_module.load_rubric_config(path)
        table = {
            "table_id": "p.d.t",
            "description": "Encounter records. Grain: one row per encounter version. "
                           "Composite key on (coid, encounter_id). Join to patient via empi_text. "
                           "Owner: data-team. Contains PHI. Type-2 SCD. Loaded from ADT source system.",
            "labels": {},
            "columns": [
                {"name": "encounter_id", "type": "STRING",
                 "description": "Stable encounter identifier from the source ADT feed."},
            ],
        }
        a = rubric_module.score_table(table)
        b = rubric_module.score_table(table, config=loaded)
        assert a["score"] == b["score"]
        assert a["grade"] == b["grade"]


# ---------------------------------------------------------------------------
# Targeted overrides
# ---------------------------------------------------------------------------

class TestWeightOverride:
    """``weights`` override changes the combined score deterministically."""

    def _table(self):
        return {
            "table_id": "p.d.t",
            "description": "Encounter records. Grain: one row per encounter. Primary key encounter_id. "
                           "Join to patient. Owner: data-team. Contains PHI. SCD type-2. "
                           "Loaded from ADT source system.",
            "labels": {},
            "columns": [
                {"name": "x", "type": "STRING", "description": "x"},  # bad column
            ],
        }

    def test_table_only_weights(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"weights": {"table": 1.0, "column": 0.0}})
        cfg = rubric_module.load_rubric_config(path)
        result = rubric_module.score_table(self._table(), config=cfg)
        # All weight on table → score equals 100 * table_ratio
        tr = result["table_metadata"]["points"] / result["table_metadata"]["max"]
        assert result["score"] == round(100 * tr)

    def test_column_only_weights(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"weights": {"table": 0.0, "column": 1.0}})
        cfg = rubric_module.load_rubric_config(path)
        result = rubric_module.score_table(self._table(), config=cfg)
        cm = result["column_metadata"]["mean_normalized"]
        assert result["score"] == round(100 * cm)


class TestGradeCutoffOverride:
    """``grade_cutoffs`` override changes letter without affecting numeric score."""

    def test_lower_cutoffs_award_higher_letter(self, rubric_module, tmp_path):
        # Tighter cutoffs: an 85 should be a B by default (>= 80) but only a C
        # if we raise the B cutoff to 90.
        path = _write(tmp_path, {"grade_cutoffs": {"A": 95, "B": 90, "C": 70, "D": 60}})
        cfg = rubric_module.load_rubric_config(path)
        assert rubric_module._grade(85, cfg) == "C"
        assert rubric_module._grade(91, cfg) == "B"
        assert rubric_module._grade(96, cfg) == "A"


class TestKeywordOverride:
    """Replacing a keyword bucket changes the criterion result."""

    def test_grain_keyword_override(self, rubric_module, tmp_path):
        # Default has "grain"/"one row per". Replace with a keyword the desc lacks.
        path = _write(tmp_path, {
            "keywords": {"grain_statement": {"strong": ["each tuple"]}}
        })
        cfg = rubric_module.load_rubric_config(path)
        # Description that previously passed "grain_statement" now fails:
        result = rubric_module.score_table_metadata(
            "Encounter records. Grain: one row per encounter version.", {}, config=cfg,
        )
        grain = next(c for c in result["criteria"] if c["name"] == "grain_statement")
        assert grain["points"] == 0


class TestTriggerOverride:
    """Overriding a column trigger regex stops/starts a conditional criterion firing."""

    def test_disable_coded_trigger(self, rubric_module, tmp_path):
        # Make the coded trigger never match.
        path = _write(tmp_path, {
            "column_triggers": {"coded": "^__never__$"}
        })
        cfg = rubric_module.load_rubric_config(path)
        col = {"name": "discharge_disposition_code", "type": "STRING",
               "description": "Coded discharge status; values map to home, transfer."}
        result = rubric_module.score_column_metadata(col, config=cfg)
        names = [c["name"] for c in result["criteria"]]
        assert "coded_field_explained" not in names


class TestThresholdOverride:
    """Length thresholds for column descriptions can be tuned."""

    def test_stricter_full_credit_threshold(self, rubric_module, tmp_path):
        # Default: ≥15 chars = full credit on not_type_echo.
        # Make it 50 chars; a 20-char description should drop to partial.
        path = _write(tmp_path, {
            "thresholds": {
                "table_desc_min": 30,
                "column_desc_min_partial": 8,
                "column_desc_min_full": 50,
                "evidence_max_chars": 90,
            }
        })
        cfg = rubric_module.load_rubric_config(path)
        col = {"name": "encounter_id", "type": "STRING",
               "description": "Encounter identifier."}  # ~20 chars
        result = rubric_module.score_column_metadata(col, config=cfg)
        not_te = next(c for c in result["criteria"] if c["name"] == "not_type_echo")
        assert not_te["points"] == 1


# ---------------------------------------------------------------------------
# Partial configs: omitted sections fall back to defaults
# ---------------------------------------------------------------------------

class TestPartialConfig:
    """Partial configs inherit untouched sections from DEFAULT_CONFIG."""

    def test_only_weights_specified(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"weights": {"table": 0.5, "column": 0.5}})
        cfg = rubric_module.load_rubric_config(path)
        # Everything else should match the default.
        d = rubric_module.DEFAULT_CONFIG
        assert cfg.grade_cutoffs == d.grade_cutoffs
        assert cfg.thresholds == d.thresholds
        assert cfg.keywords == d.keywords

    def test_partial_keyword_override_keeps_other_buckets(self, rubric_module, tmp_path):
        # Override only the strong bucket of primary_keys — weak should stay.
        path = _write(tmp_path, {
            "keywords": {"primary_keys": {"strong": ["custom-pk-marker"]}}
        })
        cfg = rubric_module.load_rubric_config(path)
        d = rubric_module.DEFAULT_CONFIG
        assert cfg.keywords["primary_keys"]["strong"] == ("custom-pk-marker",)
        assert cfg.keywords["primary_keys"]["weak"] == d.keywords["primary_keys"]["weak"]
        # Other criteria unchanged
        assert cfg.keywords["join_guidance"] == d.keywords["join_guidance"]


# ---------------------------------------------------------------------------
# Validation: malformed configs raise ValueError
# ---------------------------------------------------------------------------

class TestValidation:
    """``load_rubric_config`` rejects malformed inputs with informative errors."""

    def test_unknown_top_level_key(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"weights": {"table": 0.5, "column": 0.5}, "bogus": 1})
        with pytest.raises(ValueError, match="bogus"):
            rubric_module.load_rubric_config(path)

    def test_invalid_regex_trigger(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"column_triggers": {"coded": "[unclosed"}})
        with pytest.raises(ValueError, match="invalid regex"):
            rubric_module.load_rubric_config(path)

    def test_invalid_regex_generic(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"generic_table_desc_re": "[bad"})
        with pytest.raises(ValueError, match="invalid regex"):
            rubric_module.load_rubric_config(path)

    def test_invalid_regex_type_echo(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"type_echo_patterns": ["[also-bad"]})
        with pytest.raises(ValueError, match="invalid regex"):
            rubric_module.load_rubric_config(path)

    def test_weights_must_sum_to_one(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"weights": {"table": 0.3, "column": 0.3}})
        with pytest.raises(ValueError, match="sum to ~1.0"):
            rubric_module.load_rubric_config(path)

    def test_negative_weight(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"weights": {"table": -0.1, "column": 1.1}})
        with pytest.raises(ValueError, match="non-negative"):
            rubric_module.load_rubric_config(path)

    def test_grade_cutoffs_must_decrease(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"grade_cutoffs": {"A": 80, "B": 90, "C": 70, "D": 60}})
        with pytest.raises(ValueError, match="strictly less"):
            rubric_module.load_rubric_config(path)

    def test_unknown_keyword_criterion(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"keywords": {"made_up_criterion": {"strong": ["x"]}}})
        with pytest.raises(ValueError, match="unknown criterion"):
            rubric_module.load_rubric_config(path)

    def test_unknown_keyword_bucket(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"keywords": {"grain_statement": {"middling": ["x"]}}})
        with pytest.raises(ValueError, match="unknown bucket"):
            rubric_module.load_rubric_config(path)

    def test_unknown_trigger(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"column_triggers": {"sneaky": "."}})
        with pytest.raises(ValueError, match="unknown trigger"):
            rubric_module.load_rubric_config(path)

    def test_invalid_json(self, rubric_module, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            rubric_module.load_rubric_config(path)

    def test_threshold_must_be_non_negative(self, rubric_module, tmp_path):
        path = _write(tmp_path, {
            "thresholds": {
                "table_desc_min": -1,
                "column_desc_min_partial": 8,
                "column_desc_min_full": 15,
                "evidence_max_chars": 90,
            }
        })
        with pytest.raises(ValueError, match="non-negative integer"):
            rubric_module.load_rubric_config(path)


# ---------------------------------------------------------------------------
# Metadata stamping
# ---------------------------------------------------------------------------

class TestMetadataBlock:
    """``rubric_config_metadata`` stamps the right fields into the report."""

    def test_builtin_metadata(self, rubric_module):
        m = rubric_module.rubric_config_metadata(rubric_module.DEFAULT_CONFIG, None)
        assert m["source"] == "builtin"
        assert m["name"] == "data-steward-default"
        assert m["sha256"] == ""

    def test_custom_metadata(self, rubric_module, tmp_path):
        path = _write(tmp_path, {"name": "my-rubric", "version": "2.0"})
        cfg = rubric_module.load_rubric_config(path)
        m = rubric_module.rubric_config_metadata(cfg, path)
        assert m["source"] == str(path)
        assert m["name"] == "my-rubric"
        assert m["version"] == "2.0"
        assert len(m["sha256"]) == 64


# ---------------------------------------------------------------------------
# CLI integration: --rubric-config flag on score_table_metadata.py
# ---------------------------------------------------------------------------

class TestCliRubricConfigFlag:
    """``--rubric-config`` is accepted, errors flow to exit 2, output stamps the rubric."""

    def test_cli_accepts_default_example(self, score_script_module, tmp_path, monkeypatch):
        from tests.test_score_script import _FakeClient, _good_schema, _FakeTable  # type: ignore[import-not-found]
        # Set up fake client identical to the existing CLI tests.
        import sys as _sys
        bq = _sys.modules["google.cloud.bigquery"]
        client = _FakeClient()
        client.add_table("p.d.t", description="Some description here, longer than 30 chars.",
                         schema=_good_schema())
        monkeypatch.setattr(bq, "Client", lambda *a, **kw: client)

        cfg_path = Path(__file__).resolve().parent.parent / "examples" / "rubric_default.json"
        out_json = tmp_path / "s.json"
        monkeypatch.setattr("sys.argv", [
            "score_table_metadata.py", "--tables", "p.d.t",
            "--output-json", str(out_json),
            "--output-md", str(tmp_path / "s.md"),
            "--rubric-config", str(cfg_path),
        ])
        rc = score_script_module.main()
        assert rc == 0
        report = json.loads(out_json.read_text(encoding="utf-8"))
        assert report["rubric_config"]["source"] == str(cfg_path)
        assert report["rubric_config"]["name"] == "data-steward-default"
        assert len(report["rubric_config"]["sha256"]) == 64

    def test_cli_bad_config_returns_2(self, score_script_module, tmp_path, monkeypatch):
        bad = tmp_path / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr("sys.argv", [
            "score_table_metadata.py", "--tables", "p.d.t",
            "--output-json", str(tmp_path / "s.json"),
            "--output-md", str(tmp_path / "s.md"),
            "--rubric-config", str(bad),
        ])
        rc = score_script_module.main()
        assert rc == 2

    def test_cli_missing_config_file_returns_2(self, score_script_module, tmp_path, monkeypatch):
        missing = tmp_path / "does-not-exist.json"
        monkeypatch.setattr("sys.argv", [
            "score_table_metadata.py", "--tables", "p.d.t",
            "--output-json", str(tmp_path / "s.json"),
            "--output-md", str(tmp_path / "s.md"),
            "--rubric-config", str(missing),
        ])
        rc = score_script_module.main()
        assert rc == 2
