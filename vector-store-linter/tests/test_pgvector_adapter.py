"""Unit tests for the pgvector adapter.

A fake connection dispatches canned (description, rows) by the SQL comment
marker the adapter prefixes onto each query, mirroring psycopg2's tuple rows
plus cursor.description. Describe, sampling, full read (server-side cursor),
search, and the no-index path are covered without a live database. A live
integration test skips itself unless PGVECTOR_TEST_DSN and PGVECTOR_TEST_TABLE
are set.
"""

from __future__ import annotations

import os
import sys

import pytest

from adapters.base import RetrievalResult, StoreConfig, VectorRecord
from adapters.errors import StoreConnectionError
from adapters.pgvector_adapter import PgVectorAdapter, _parse_vector


class FakeCursor:
    def __init__(self, responses, name=None):
        self._responses = responses
        self.description = None
        self._rows: list = []

    def execute(self, sql, params=None):
        for marker, cols, rows in self._responses:
            if marker in sql:
                self.description = [(c,) for c in cols]
                self._rows = list(rows)
                return
        self.description = []
        self._rows = []

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def close(self):
        pass


class FakeConn:
    def __init__(self, responses):
        self._responses = responses
        self.closed = False

    def cursor(self, name=None):
        return FakeCursor(self._responses, name)

    def close(self):
        self.closed = True


_COLUMNS = ("-- columns", ["column_name", "data_type"],
            [("id", "text"), ("embedding", "vector(4)"), ("text", "text"), ("source", "text")])
_COUNT = ("-- count_estimate", ["estimate"], [(1000,)])
_INDEX_HNSW = ("-- index", ["method", "indexdef"],
               [("hnsw", "CREATE INDEX idx ON public.t USING hnsw (embedding vector_cosine_ops) "
                         "WITH (m='16', ef_construction='64')")])


def _adapter(responses, table="embeddings"):
    adapter = PgVectorAdapter(connection_string="postgresql://u@h/db", table=table, conn=FakeConn(responses))
    adapter.connect()
    return adapter


# ----- describe ------------------------------------------------------------


def test_describe():
    adapter = _adapter([_COLUMNS, _COUNT, _INDEX_HNSW])
    config = adapter.describe()
    assert isinstance(config, StoreConfig)
    assert config.store_type == "pgvector"
    assert config.vector_count == 1000
    assert config.dimension == 4
    assert config.distance_metric == "cosine"
    assert config.index_type == "hnsw"
    assert config.index_parameters == {"m": 16, "ef_construction": 64}
    assert config.metadata_schema == {"id": "text", "text": "text", "source": "text"}
    assert config.raw["schema"] == "public"


def test_describe_no_index():
    adapter = _adapter([_COLUMNS, _COUNT, ("-- index", ["method", "indexdef"], [])])
    config = adapter.describe()
    assert config.index_type is None
    assert config.distance_metric is None  # cannot infer without an index


def test_describe_exact_count_fallback():
    columns = _COLUMNS
    count_zero = ("-- count_estimate", ["estimate"], [(0,)])  # estimate unavailable
    count_exact = ("-- count_exact", ["exact"], [(42,)])
    adapter = _adapter([columns, count_zero, count_exact, ("-- index", ["method", "indexdef"], [])])
    assert adapter.describe().vector_count == 42


def test_describe_no_vector_column_raises():
    columns = ("-- columns", ["column_name", "data_type"], [("id", "text"), ("body", "text")])
    adapter = _adapter([columns, _COUNT])
    with pytest.raises(StoreConnectionError, match="No pgvector"):
        adapter.describe()


# ----- iter_vectors --------------------------------------------------------


def test_iter_vectors_sample():
    sample = ("-- sample", ["id", "embedding", "text", "source"],
              [("v1", "[0.1,0.2,0.3,0.4]", "hello", "d1"), ("v2", "[0.5,0.6,0.7,0.8]", "world", "d2")])
    adapter = _adapter([_COLUMNS, _COUNT, sample])
    records = list(adapter.iter_vectors(sample_size=2))
    assert len(records) == 2
    assert all(isinstance(r, VectorRecord) for r in records)
    assert records[0].id == "v1"
    assert records[0].embedding == [0.1, 0.2, 0.3, 0.4]  # parsed from text
    assert records[0].content == "hello"
    assert "embedding" not in records[0].metadata


def test_iter_vectors_full_read():
    full = ("-- full", ["id", "embedding", "text", "source"],
            [("v1", "[0.1,0.2]", "a", "d1"), ("v2", "[0.3,0.4]", "b", "d2")])
    adapter = _adapter([_COLUMNS, _COUNT, full])
    records = list(adapter.iter_vectors(sample_size=None))
    assert [r.id for r in records] == ["v1", "v2"]
    assert records[1].embedding == [0.3, 0.4]


def test_iter_vectors_synthesizes_id():
    columns = ("-- columns", ["column_name", "data_type"], [("embedding", "vector(2)"), ("body", "text")])
    full = ("-- full", ["embedding", "body"], [("[0.1,0.2]", "a")])
    adapter = _adapter([columns, _COUNT, full])
    records = list(adapter.iter_vectors(sample_size=None))
    assert records[0].id == "row-0"


# ----- search --------------------------------------------------------------


def test_search_uses_cosine_operator():
    search = ("-- search", ["id", "text", "source", "distance"],
              [("v1", "hello", "d1", 0.12), ("v2", "world", "d2", 0.34)])
    captured = {}

    responses = [_COLUMNS, _COUNT, _INDEX_HNSW, search]
    adapter = PgVectorAdapter(connection_string="x", table="embeddings", conn=_CapturingConn(responses, captured))
    adapter.connect()
    results = adapter.search([0.1, 0.1, 0.1, 0.1], k=2)
    assert len(results) == 2
    assert all(isinstance(r, RetrievalResult) for r in results)
    assert results[0].doc_id == "v1"
    assert results[0].score == 0.12  # distance, lower is closer
    assert results[0].content == "hello"
    assert "distance" not in results[0].metadata
    # cosine index -> the <=> operator
    assert "<=>" in captured["search_sql"]


def test_search_no_index_defaults_to_cosine():
    search = ("-- search", ["id", "distance"], [("v1", 0.5)])
    captured = {}
    responses = [_COLUMNS, _COUNT, ("-- index", ["method", "indexdef"], []), search]
    adapter = PgVectorAdapter(connection_string="x", table="embeddings", conn=_CapturingConn(responses, captured))
    adapter.connect()
    results = adapter.search([0.1, 0.1], k=1)
    assert results[0].doc_id == "v1"
    assert "<=>" in captured["search_sql"]  # default cosine


class _CapturingCursor(FakeCursor):
    def __init__(self, responses, captured, name=None):
        super().__init__(responses, name)
        self._captured = captured

    def execute(self, sql, params=None):
        if "-- search" in sql:
            self._captured["search_sql"] = sql
        super().execute(sql, params)


class _CapturingConn(FakeConn):
    def __init__(self, responses, captured):
        super().__init__(responses)
        self._captured = captured

    def cursor(self, name=None):
        return _CapturingCursor(self._responses, self._captured, name)


# ----- embed_text and errors -----------------------------------------------


def test_embed_text_returns_none():
    adapter = _adapter([_COLUMNS, _COUNT])
    assert adapter.embed_text("query") is None


def test_fetch_by_id():
    fetch = ("-- fetch", ["id", "embedding", "text", "source"],
             [("v1", "[0.1,0.2,0.3,0.4]", "hello", "d1")])
    adapter = _adapter([_COLUMNS, _COUNT, fetch])
    out = adapter.fetch(["v1"])
    assert set(out) == {"v1"}
    assert out["v1"].content == "hello"
    assert out["v1"].embedding == [0.1, 0.2, 0.3, 0.4]


def test_fetch_no_id_column_returns_empty():
    columns = ("-- columns", ["column_name", "data_type"], [("embedding", "vector(2)"), ("body", "text")])
    adapter = _adapter([columns, _COUNT])
    assert adapter.fetch(["v1"]) == {}


def test_describe_before_connect_raises():
    adapter = PgVectorAdapter(connection_string="x", table="t")
    with pytest.raises(StoreConnectionError, match="not connected"):
        adapter.describe()


def test_bad_table_identifier_raises():
    adapter = PgVectorAdapter(connection_string="x", table="a.b.c", conn=FakeConn([]))
    with pytest.raises(StoreConnectionError, match="name or schema.name"):
        adapter.connect()


def test_no_connection_string_raises(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    adapter = PgVectorAdapter(connection_string=None, table="t")
    with pytest.raises(StoreConnectionError, match="No connection string"):
        adapter.connect()


def test_missing_psycopg2_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg2", None)
    adapter = PgVectorAdapter(connection_string="postgresql://u@h/db", table="t")
    with pytest.raises(StoreConnectionError, match="psycopg2 is not installed"):
        adapter.connect()


# ----- helpers -------------------------------------------------------------


def test_parse_vector():
    assert _parse_vector("[0.1,0.2,0.3]") == [0.1, 0.2, 0.3]
    assert _parse_vector("[]") == []
    assert _parse_vector(None) == []
    assert _parse_vector([1, 2]) == [1.0, 2.0]


# ----- live integration (skipped without a database) -----------------------


@pytest.mark.integration
def test_pgvector_integration():
    dsn = os.environ.get("PGVECTOR_TEST_DSN")
    table = os.environ.get("PGVECTOR_TEST_TABLE")
    if not dsn or not table:
        pytest.skip("PGVECTOR_TEST_DSN and PGVECTOR_TEST_TABLE not set")
    adapter = PgVectorAdapter(connection_string=dsn, table=table)
    adapter.connect()
    try:
        config = adapter.describe()
        assert config.store_type == "pgvector"
        assert config.dimension and config.dimension > 0
        sample = list(adapter.iter_vectors(sample_size=10))
        assert all(len(r.embedding) == config.dimension for r in sample if r.embedding)
    finally:
        adapter.close()
