"""Unit tests for the BigQuery adapter.

A fake client dispatches canned rows by SQL substring (ARRAY_LENGTH,
VECTOR_INDEXES, TABLESAMPLE, VECTOR_SEARCH, plain SELECT) so describe, sampling,
full read, and search are exercised without a live warehouse. A live
integration test skips itself unless BIGQUERY_TEST_TABLE is set.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from adapters.base import RetrievalResult, StoreConfig, VectorRecord
from adapters.bigquery_adapter import BigQueryAdapter, _sample_percent
from adapters.errors import StoreConnectionError


def _field(name, field_type, mode="NULLABLE"):
    return SimpleNamespace(name=name, field_type=field_type, mode=mode)


SCHEMA = [
    _field("id", "STRING"),
    _field("embedding", "FLOAT64", "REPEATED"),
    _field("text", "STRING"),
    _field("source", "STRING"),
]


class FakeTable:
    def __init__(self, num_rows=1000, schema=None, clustering_fields=None):
        self.num_rows = num_rows
        self.schema = schema or SCHEMA
        self.time_partitioning = None
        self.range_partitioning = None
        self.clustering_fields = clustering_fields


class FakeJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return list(self._rows)


class FakeBQClient:
    """Returns canned rows by matching a substring of the SQL, in order."""

    def __init__(self, table, responses):
        self._table = table
        self._responses = responses  # list of (substring, rows)
        self.queries: list[str] = []

    def get_table(self, table_id):
        return self._table

    def query(self, sql, job_config=None):
        self.queries.append(sql)
        for substring, rows in self._responses:
            if substring in sql:
                return FakeJob(rows)
        return FakeJob([])

    def close(self):
        pass


def _adapter(responses, table=None):
    client = FakeBQClient(table or FakeTable(), responses)
    adapter = BigQueryAdapter(table="proj.ds.tbl", client=client)
    adapter.connect()
    return adapter, client


# ----- describe ------------------------------------------------------------


def test_describe():
    responses = [
        ("ARRAY_LENGTH", [{"dim": 4}]),
        ("VECTOR_INDEXES", [{"index_name": "idx", "index_status": "ACTIVE",
                             "index_type": "IVF", "distance_type": "COSINE", "coverage_percentage": 100}]),
    ]
    adapter, _ = _adapter(responses, FakeTable(num_rows=1000, clustering_fields=["source"]))
    config = adapter.describe()
    assert isinstance(config, StoreConfig)
    assert config.store_type == "bigquery"
    assert config.vector_count == 1000
    assert config.dimension == 4
    assert config.distance_metric == "cosine"
    assert config.index_type == "ivf"
    assert config.metadata_schema == {"id": "STRING", "text": "STRING", "source": "STRING"}
    assert config.raw["clustering"] == ["source"]
    assert config.index_parameters["index_name"] == "idx"


def test_describe_without_index_reports_unknown():
    responses = [("ARRAY_LENGTH", [{"dim": 8}]), ("VECTOR_INDEXES", [])]
    adapter, _ = _adapter(responses)
    config = adapter.describe()
    assert config.dimension == 8
    assert config.index_type is None
    assert config.distance_metric is None  # no index, unknown


def test_describe_no_vector_column_raises():
    schema = [_field("id", "STRING"), _field("text", "STRING")]
    adapter, _ = _adapter([], FakeTable(schema=schema))
    with pytest.raises(StoreConnectionError, match="No ARRAY"):
        adapter.describe()


# ----- iter_vectors --------------------------------------------------------


def test_iter_vectors_sample():
    sample_rows = [
        {"id": "v1", "embedding": [0.1, 0.2, 0.3, 0.4], "text": "hello", "source": "d1"},
        {"id": "v2", "embedding": [0.5, 0.6, 0.7, 0.8], "text": "world", "source": "d2"},
    ]
    adapter, client = _adapter([("TABLESAMPLE", sample_rows)])
    records = list(adapter.iter_vectors(sample_size=2))
    assert len(records) == 2
    assert all(isinstance(r, VectorRecord) for r in records)
    assert records[0].id == "v1"
    assert records[0].embedding == [0.1, 0.2, 0.3, 0.4]
    assert records[0].content == "hello"
    assert "embedding" not in records[0].metadata
    assert any("TABLESAMPLE" in q for q in client.queries)


def test_iter_vectors_full_read():
    full_rows = [
        {"id": "v1", "embedding": [0.1, 0.2], "text": "a", "source": "d1"},
        {"id": "v2", "embedding": [0.3, 0.4], "text": "b", "source": "d2"},
        {"id": "v3", "embedding": [0.5, 0.6], "text": "c", "source": "d3"},
    ]
    adapter, client = _adapter([("SELECT *", full_rows)])
    records = list(adapter.iter_vectors(sample_size=None))
    assert [r.id for r in records] == ["v1", "v2", "v3"]
    assert not any("TABLESAMPLE" in q for q in client.queries)


def test_iter_vectors_synthesizes_id_when_absent():
    schema = [_field("embedding", "FLOAT64", "REPEATED"), _field("text", "STRING")]
    rows = [{"embedding": [0.1, 0.2], "text": "a"}]
    adapter, _ = _adapter([("SELECT *", rows)], FakeTable(schema=schema))
    records = list(adapter.iter_vectors(sample_size=None))
    assert records[0].id == "row-0"  # no id column, synthesized


# ----- search --------------------------------------------------------------


def test_search(monkeypatch):
    search_rows = [
        {"id": "v1", "text": "hello", "source": "d1", "distance": 0.12},
        {"id": "v2", "text": "world", "source": "d2", "distance": 0.34},
    ]
    responses = [
        ("VECTOR_SEARCH", search_rows),
        ("VECTOR_INDEXES", [{"index_type": "IVF", "distance_type": "COSINE"}]),
    ]
    adapter, _ = _adapter(responses)
    # The query-parameter construction is thin SDK glue (covered by the
    # integration test); the fake client ignores job_config, so stub it out
    # to exercise the SQL building and result parsing without the SDK.
    monkeypatch.setattr(adapter, "_array_job_config", lambda query, k: None)
    results = adapter.search([0.1, 0.1, 0.1, 0.1], k=2)
    assert len(results) == 2
    assert all(isinstance(r, RetrievalResult) for r in results)
    assert results[0].doc_id == "v1"
    assert results[0].score == 0.12  # distance, lower is closer
    assert results[0].content == "hello"
    assert "distance" not in results[0].metadata


# ----- embed_text and errors -----------------------------------------------


def test_embed_text_returns_none():
    adapter, _ = _adapter([])
    assert adapter.embed_text("query") is None


def test_fetch_by_id(monkeypatch):
    rows = [{"id": "v1", "embedding": [0.1, 0.2, 0.3, 0.4], "text": "hello", "source": "d1"}]
    adapter, _ = _adapter([("UNNEST", rows)])
    monkeypatch.setattr(adapter, "_id_array_job_config", lambda ids: None)  # avoid the SDK
    out = adapter.fetch(["v1"])
    assert set(out) == {"v1"}
    assert out["v1"].content == "hello"


def test_fetch_no_id_column_returns_empty():
    schema = [_field("embedding", "FLOAT64", "REPEATED"), _field("text", "STRING")]
    adapter, _ = _adapter([], FakeTable(schema=schema))
    assert adapter.fetch(["v1"]) == {}


def test_describe_before_connect_raises():
    adapter = BigQueryAdapter(table="proj.ds.tbl")
    with pytest.raises(StoreConnectionError, match="not connected"):
        adapter.describe()


def test_bad_table_id_raises():
    adapter = BigQueryAdapter(table="justatable")
    with pytest.raises(StoreConnectionError, match="project.dataset.table"):
        adapter.connect()


def test_missing_package_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", None)
    adapter = BigQueryAdapter(table="proj.ds.tbl")  # no injected client
    with pytest.raises(StoreConnectionError, match="google-cloud-bigquery is not installed"):
        adapter.connect()


# ----- helpers -------------------------------------------------------------


def test_sample_percent():
    assert _sample_percent(2, 1000) == 0.4  # 100 * 2/1000 * 2
    assert _sample_percent(100, None) == 100.0
    assert _sample_percent(100, 0) == 100.0


# ----- live integration (skipped without a test table) ---------------------


@pytest.mark.integration
def test_bigquery_integration():
    table = os.environ.get("BIGQUERY_TEST_TABLE")
    if not table:
        pytest.skip("BIGQUERY_TEST_TABLE not set")
    adapter = BigQueryAdapter(table=table)
    adapter.connect()
    try:
        config = adapter.describe()
        assert config.store_type == "bigquery"
        assert config.dimension and config.dimension > 0
        sample = list(adapter.iter_vectors(sample_size=10))
        assert all(len(r.embedding) == config.dimension for r in sample if r.embedding)
    finally:
        adapter.close()
