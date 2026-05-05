"""CLI integration tests for score_table_metadata.py.

Stubs out the BigQuery client so the test runs without GCP credentials.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


class _FakeSchemaField:
    def __init__(self, name, field_type, mode="NULLABLE", description=None, policy_tags=None):
        self.name = name
        self.field_type = field_type
        self.mode = mode
        self.description = description
        self.policy_tags = policy_tags


class _PolicyTagList:
    def __init__(self, names):
        self.names = names


class _FakeTable:
    def __init__(self, full_id, description=None, labels=None, schema=None):
        self.full_table_id = full_id
        self.description = description
        self.labels = labels or {}
        self.schema = schema or []


class _FakeListedTable:
    def __init__(self, table_id):
        self.table_id = table_id


class _FakeClient:
    """Stubbed bigquery.Client: returns canned tables and dataset listings."""

    def __init__(self, *args, **kwargs):
        self._tables = {}
        self._dataset_listings = {}

    def add_table(self, table_id, **kwargs):
        full_id = table_id.replace(".", ":", 1)  # bigquery uses project:dataset.table
        self._tables[table_id] = _FakeTable(full_id, **kwargs)

    def add_dataset(self, dataset_id, table_short_ids):
        self._dataset_listings[dataset_id] = [_FakeListedTable(t) for t in table_short_ids]

    def get_table(self, table_id):
        if table_id not in self._tables:
            raise KeyError(table_id)
        return self._tables[table_id]

    def list_tables(self, dataset_ref):
        if dataset_ref not in self._dataset_listings:
            return []
        return self._dataset_listings[dataset_ref]


@pytest.fixture
def fake_client(monkeypatch):
    """Install the fake client into google.cloud.bigquery for the test."""
    bq = sys.modules["google.cloud.bigquery"]
    client = _FakeClient()
    monkeypatch.setattr(bq, "Client", lambda *a, **kw: client)
    return client


def _good_schema():
    return [
        _FakeSchemaField("encounter_id", "STRING", description="Stable encounter identifier."),
        _FakeSchemaField("event_timestamp", "TIMESTAMP", description="Event time in UTC, ISO 8601."),
        _FakeSchemaField("discharge_disposition_code", "STRING",
                         description="Coded discharge status; values map to home, transfer, expired."),
    ]


def _bad_schema():
    return [
        _FakeSchemaField("user_id", "STRING", description=None),
        _FakeSchemaField("amount", "FLOAT64", description="amount field"),
    ]


class TestNormalizeTable:
    def test_normalize_drops_colon_in_full_id(self, score_script_module):
        tbl = _FakeTable("p:d.t", description="x", labels={}, schema=_good_schema())
        norm = score_script_module._normalize_table(tbl)
        assert norm["table_id"] == "p.d.t"

    def test_normalize_extracts_columns(self, score_script_module):
        tbl = _FakeTable("p:d.t", description="x", labels={}, schema=_good_schema())
        norm = score_script_module._normalize_table(tbl)
        assert [c["name"] for c in norm["columns"]] == [
            "encounter_id", "event_timestamp", "discharge_disposition_code"
        ]

    def test_normalize_carries_policy_tags(self, score_script_module):
        sf = _FakeSchemaField("email", "STRING", description="...",
                              policy_tags=_PolicyTagList(["pt1"]))
        tbl = _FakeTable("p:d.t", description="x", labels={}, schema=[sf])
        norm = score_script_module._normalize_table(tbl)
        assert norm["columns"][0]["policy_tags"] == ["pt1"]


class TestCsvArgTables:
    def test_valid_list(self, score_script_module):
        result = score_script_module.csv_arg_tables("p.d.t1, p.d.t2")
        assert result == ["p.d.t1", "p.d.t2"]

    def test_empty_returns_empty(self, score_script_module):
        assert score_script_module.csv_arg_tables(None) == []
        assert score_script_module.csv_arg_tables("") == []

    def test_invalid_id_raises(self, score_script_module):
        with pytest.raises(ValueError):
            score_script_module.csv_arg_tables("not-a-table-id")


class TestMainTablesMode:
    def test_full_run_writes_artifacts(self, fake_client, score_script_module, tmp_path, monkeypatch):
        fake_client.add_table(
            "p.d.good",
            description=(
                "Encounter records for inpatient, outpatient, and ED visits. "
                "Grain: one row per encounter version per coid. "
                "Composite key on (coid, encounter_id). "
                "Join to patient via foreign key empi_text. "
                "Owner: clinical-data-team. Contains PHI. "
                "Type-2 SCD; filter latest_record_ind=1 for current state. "
                "Loaded from the ADT source system."
            ),
            labels={},
            schema=_good_schema(),
        )
        fake_client.add_table(
            "p.d.bad",
            description="Users table.",
            labels={},
            schema=_bad_schema(),
        )

        out_json = tmp_path / "scorecard.json"
        out_md = tmp_path / "scorecard.md"
        out_html = tmp_path / "scorecard.html"

        monkeypatch.setattr(sys, "argv", [
            "score_table_metadata.py",
            "--tables", "p.d.good,p.d.bad",
            "--output-json", str(out_json),
            "--output-md", str(out_md),
            "--output-html", str(out_html),
        ])
        rc = score_script_module.main()
        assert rc == 0
        assert out_json.exists() and out_md.exists() and out_html.exists()
        report = json.loads(out_json.read_text(encoding="utf-8"))
        ids = sorted(t["table_id"] for t in report["tables"])
        assert ids == ["p.d.bad", "p.d.good"]
        good = [t for t in report["tables"] if t["table_id"] == "p.d.good"][0]
        bad = [t for t in report["tables"] if t["table_id"] == "p.d.bad"][0]
        assert good["score"] > bad["score"]
        assert good["grade"] in ("A", "B")
        assert bad["grade"] in ("D", "F")

    def test_expect_min_score_pass(self, fake_client, score_script_module, tmp_path, monkeypatch):
        fake_client.add_table(
            "p.d.good",
            description=(
                "Encounter records for inpatient and outpatient visits. "
                "Grain: one row per encounter version. Composite key on encounter_id. "
                "Join to patient via foreign key empi_text. "
                "Owner: data-team. Contains PHI. SCD type-2. Loaded from ADT source system."
            ),
            schema=_good_schema(),
        )
        out_json = tmp_path / "s.json"
        monkeypatch.setattr(sys, "argv", [
            "score_table_metadata.py", "--tables", "p.d.good",
            "--output-json", str(out_json), "--output-md", str(tmp_path / "s.md"),
            "--expect-min-score", "70",
        ])
        rc = score_script_module.main()
        assert rc == 0

    def test_expect_min_score_fail_returns_3(self, fake_client, score_script_module, tmp_path, monkeypatch):
        fake_client.add_table("p.d.bad", description="Users.", schema=_bad_schema())
        monkeypatch.setattr(sys, "argv", [
            "score_table_metadata.py", "--tables", "p.d.bad",
            "--output-json", str(tmp_path / "s.json"),
            "--output-md", str(tmp_path / "s.md"),
            "--expect-min-score", "70",
        ])
        rc = score_script_module.main()
        assert rc == 3

    def test_invalid_table_id_returns_2(self, score_script_module, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", [
            "score_table_metadata.py", "--tables", "not-a-valid-id",
            "--output-json", str(tmp_path / "s.json"),
            "--output-md", str(tmp_path / "s.md"),
        ])
        rc = score_script_module.main()
        assert rc == 2


class TestMainDatasetMode:
    def test_dataset_enumerates_tables(self, fake_client, score_script_module, tmp_path, monkeypatch):
        fake_client.add_dataset("p.d", ["t1", "t2"])
        for short in ("t1", "t2"):
            fake_client.add_table(f"p.d.{short}", description="Some description here.", schema=_good_schema())
        monkeypatch.setattr(sys, "argv", [
            "score_table_metadata.py", "--dataset", "p.d",
            "--output-json", str(tmp_path / "s.json"),
            "--output-md", str(tmp_path / "s.md"),
        ])
        rc = score_script_module.main()
        assert rc == 0
        report = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
        assert sorted(t["table_id"] for t in report["tables"]) == ["p.d.t1", "p.d.t2"]

    def test_invalid_dataset_returns_2(self, score_script_module, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", [
            "score_table_metadata.py", "--dataset", "not-a-valid-dataset-id",
            "--output-json", str(tmp_path / "s.json"),
            "--output-md", str(tmp_path / "s.md"),
        ])
        rc = score_script_module.main()
        assert rc == 2

    def test_empty_dataset_warns(self, fake_client, score_script_module, tmp_path, monkeypatch):
        fake_client.add_dataset("p.empty", [])
        monkeypatch.setattr(sys, "argv", [
            "score_table_metadata.py", "--dataset", "p.empty",
            "--output-json", str(tmp_path / "s.json"),
            "--output-md", str(tmp_path / "s.md"),
        ])
        rc = score_script_module.main()
        assert rc == 0
        report = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
        assert report["tables"] == []
        assert any("no tables" in w.lower() for w in report["warnings"])


class TestRenderScript:
    def test_render_from_json(self, render_script_module, tmp_path, monkeypatch):
        sample = {
            "rubric_version": "1.0", "scope": {"tables": ["p.d.t"]},
            "tables": [{
                "table_id": "p.d.t", "score": 88, "grade": "B",
                "table_metadata": {"points": 14, "max": 16, "criteria": []},
                "column_metadata": {"mean_normalized": 0.86, "column_count": 0, "columns": []},
                "issues": [],
            }],
            "warnings": [],
        }
        in_json = tmp_path / "in.json"
        in_json.write_text(json.dumps(sample), encoding="utf-8")
        out_md = tmp_path / "out.md"
        out_html = tmp_path / "out.html"
        monkeypatch.setattr(sys, "argv", [
            "render_scorecard.py", "--input", str(in_json),
            "--output-md", str(out_md), "--output-html", str(out_html),
        ])
        rc = render_script_module.main()
        assert rc == 0
        assert "Metadata Scorecard" in out_md.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in out_html.read_text(encoding="utf-8")

    def test_render_with_min_score_fail(self, render_script_module, tmp_path, monkeypatch):
        sample = {
            "rubric_version": "1.0", "scope": {"tables": ["p.d.t"]},
            "tables": [{
                "table_id": "p.d.t", "score": 40, "grade": "F",
                "table_metadata": {"points": 0, "max": 16, "criteria": []},
                "column_metadata": {"mean_normalized": 0.0, "column_count": 0, "columns": []},
                "issues": [],
            }],
            "warnings": [],
        }
        in_json = tmp_path / "in.json"
        in_json.write_text(json.dumps(sample), encoding="utf-8")
        monkeypatch.setattr(sys, "argv", [
            "render_scorecard.py", "--input", str(in_json),
            "--output-md", str(tmp_path / "out.md"),
            "--expect-min-score", "70",
        ])
        rc = render_script_module.main()
        assert rc == 3
