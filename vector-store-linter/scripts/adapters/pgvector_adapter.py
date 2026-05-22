"""pgvector adapter.

Implements the adapter interface for Postgres with the pgvector extension.
Connects with a libpq connection string from the constructor or the DATABASE_URL
environment variable, using plain psycopg2 (the declared dependency). Because
the pgvector Python type is not registered, a vector column is read by casting
it to text and parsing the bracketed list.

describe reports the row count (a fast reltuples estimate, falling back to an
exact count only when the estimate is unavailable), the embedding column and its
dimension, and the vector index method and parameters parsed from the index
definition. The distance metric is inferred from the index opclass
(vector_l2_ops, vector_cosine_ops, vector_ip_ops).

Sampling uses ORDER BY random() on smaller tables, where it is exact, and
TABLESAMPLE SYSTEM on larger tables, where a full random scan would be slow. The
full read streams through a server-side cursor so the whole table never lands in
memory. Search uses the pgvector distance operator that matches the index
metric: <-> for L2, <=> for cosine, <#> for inner product. When the table has no
vector index the metric cannot be inferred, so search defaults to cosine and
warns.

Score semantics. The operators return distances (lower is closer), matching the
BigQuery adapter and opposite the Pinecone adapter, which returns similarity.

Each query is prefixed with a comment marker so it is easy to identify in logs
and tests; Postgres ignores SQL comments.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Iterator, Optional

from .base import RetrievalResult, StoreConfig, VectorRecord, VectorStoreAdapter
from .errors import StoreConnectionError

logger = logging.getLogger("vector_store_linter")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_VECTOR_TYPE_RE = re.compile(r"^vector\((\d+)\)", re.IGNORECASE)
_OPCLASS_RE = re.compile(r"vector_(l2|cosine|ip)_ops", re.IGNORECASE)
_WITH_PARAM_RE = re.compile(r"(\w+)\s*=\s*'?([^,'\)]+)'?")

_ID_NAME_HINTS = ("id", "doc_id", "document_id", "chunk_id", "pk", "key")
_CONTENT_NAME_HINTS = ("text", "content", "chunk_text", "page_content", "body", "document")

_OPCLASS_TO_METRIC = {"l2": "l2", "cosine": "cosine", "ip": "dot_product"}
_METRIC_TO_OPERATOR = {"l2": "<->", "cosine": "<=>", "dot_product": "<#>"}
_DEFAULT_OPERATOR = "<=>"  # cosine, used when the metric cannot be inferred

# Above this row count, prefer block sampling over a full random scan.
_RANDOM_SAMPLE_MAX_ROWS = 50000


class PgVectorAdapter(VectorStoreAdapter):
    """Adapter for a Postgres + pgvector table."""

    def __init__(
        self,
        connection_string: str | None = None,
        table: str | None = None,
        conn: Any | None = None,
    ) -> None:
        """Create an adapter for one pgvector table.

        Args:
            connection_string: libpq connection string. Falls back to DATABASE_URL.
            table: Table name, optionally schema-qualified (schema.table). The
                default schema is public.
            conn: Optional pre-built connection, used in tests.
        """
        self._connection_string = connection_string
        self._table_arg = table
        self._conn = conn
        self._schema_name = "public"
        self._table_name: str | None = None
        self._columns: list[str] | None = None
        self._column_types: dict[str, str] = {}
        self._vector_col: str | None = None
        self._dimension: int | None = None
        self._num_rows: int | None = None
        self._id_col: str | None = None
        self._content_col: str | None = None
        self._index_info: tuple[Optional[str], Optional[str], Optional[dict]] | None = None

    def connect(self) -> None:
        """Parse the table name and open the connection."""
        self._schema_name, self._table_name = self._parse_table(self._table_arg)
        if self._conn is None:
            self._conn = self._build_conn()

    def describe(self) -> StoreConfig:
        """Return the table configuration as a StoreConfig."""
        self._require_connected()
        self._ensure_meta()
        index_type, metric, index_parameters = self._get_index_info()
        metadata_schema = {c: self._column_types[c] for c in self._columns if c != self._vector_col}
        return StoreConfig(
            store_type="pgvector",
            vector_count=self._num_rows,
            dimension=self._dimension,
            distance_metric=metric,
            index_type=index_type,
            index_parameters=index_parameters,
            replica_count=None,
            shard_count=None,
            refresh_cadence=None,
            metadata_schema=metadata_schema or None,
            namespaces=None,
            raw={"schema": self._schema_name, "vector_column": self._vector_col},
        )

    def iter_vectors(self, sample_size: Optional[int] = None, seed: int = 42) -> Iterator[VectorRecord]:
        """Yield vectors, sampling unless sample_size is None."""
        self._require_connected()
        self._ensure_meta()
        select_list = ", ".join(
            f'"{c}"::text AS "{c}"' if c == self._vector_col else f'"{c}"' for c in self._columns
        )
        table_ref = self._quoted_table()
        if sample_size is None:
            sql = f"-- full\nSELECT {select_list} FROM {table_ref}"
            rows = self._stream(sql)
        else:
            rows = self._sample_rows(select_list, table_ref, sample_size, seed)
        for index, row in enumerate(rows):
            yield self._row_to_vector(row, index)

    def search(
        self,
        query_embedding: list[float],
        k: int,
        filter: Optional[dict] = None,
    ) -> list[RetrievalResult]:
        """Run a nearest-neighbor search with the index-appropriate operator."""
        self._require_connected()
        self._ensure_meta()
        if filter:
            logger.warning("pgvector adapter does not yet apply metadata filters in search; ignoring filter.")
        _, metric, _ = self._get_index_info()
        operator = _METRIC_TO_OPERATOR.get(metric or "", _DEFAULT_OPERATOR)
        if metric is None:
            logger.warning("No vector index found; defaulting search to cosine distance.")
        non_vector = [c for c in self._columns if c != self._vector_col]
        select_list = ", ".join(f'"{c}"' for c in non_vector)
        vec = self._vector_literal(query_embedding)
        sql = (
            f"-- search\nSELECT {select_list}, "
            f'("{self._vector_col}" {operator} %(q)s::vector) AS distance '
            f"FROM {self._quoted_table()} "
            f'ORDER BY "{self._vector_col}" {operator} %(q)s::vector LIMIT %(k)s'
        )
        rows = self._query(sql, {"q": vec, "k": int(k)})
        results: list[RetrievalResult] = []
        for index, row in enumerate(rows):
            distance = row.pop("distance", None)
            results.append(
                RetrievalResult(
                    doc_id=self._extract_id(row, index),
                    score=float(distance) if distance is not None else 0.0,
                    metadata=row,
                    content=self._extract_content(row),
                )
            )
        return results

    def embed_text(self, text: str) -> Optional[list[float]]:
        """Return None. The adapter does not embed; queries must be pre-embedded."""
        return None

    def fetch(self, ids: list[str]) -> dict:
        """Fetch rows by id, when an id column is present."""
        self._require_connected()
        self._ensure_meta()
        if not ids or not self._id_col:
            return {}
        select_list = ", ".join(
            f'"{c}"::text AS "{c}"' if c == self._vector_col else f'"{c}"' for c in self._columns
        )
        sql = (
            f"-- fetch\nSELECT {select_list} FROM {self._quoted_table()} "
            f'WHERE "{self._id_col}" = ANY(%(ids)s)'
        )
        out: dict[str, VectorRecord] = {}
        for index, row in enumerate(self._query(sql, {"ids": [str(i) for i in ids]})):
            record = self._row_to_vector(row, index)
            out[record.id] = record
        return out

    def close(self) -> None:
        """Close the connection."""
        close = getattr(self._conn, "close", None)
        if callable(close):
            close()
        self._columns = None

    # ----- internals -------------------------------------------------------

    def _parse_table(self, table: str | None) -> tuple[str, str]:
        if not table:
            raise StoreConnectionError("pgvector adapter requires a table name.")
        parts = table.split(".")
        if len(parts) == 1:
            schema, name = "public", parts[0]
        elif len(parts) == 2:
            schema, name = parts
        else:
            raise StoreConnectionError(f"Table must be name or schema.name, got {table!r}.")
        if not _IDENT_RE.match(schema) or not _IDENT_RE.match(name):
            raise StoreConnectionError(f"Unsafe schema or table identifier in {table!r}.")
        return schema, name

    def _build_conn(self) -> Any:
        conn_str = self._connection_string or os.environ.get("DATABASE_URL")
        if not conn_str:
            raise StoreConnectionError(
                "No connection string. Pass --connection-string or set DATABASE_URL."
            )
        try:
            import psycopg2
        except ImportError as exc:
            raise StoreConnectionError(
                "psycopg2 is not installed. Install it with: pip install psycopg2-binary"
            ) from exc
        try:
            return psycopg2.connect(conn_str)
        except Exception as exc:  # noqa: BLE001
            raise StoreConnectionError(f"Could not connect to Postgres: {exc}") from exc

    def _require_connected(self) -> None:
        if self._conn is None or self._table_name is None:
            raise StoreConnectionError("Adapter is not connected. Call connect() first.")

    def _quoted_table(self) -> str:
        return f'"{self._schema_name}"."{self._table_name}"'

    def _query(self, sql: str, params: Any | None = None) -> list[dict]:
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, params)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except StoreConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StoreConnectionError(f"Postgres query failed: {exc}") from exc
        finally:
            cursor.close()

    def _stream(self, sql: str, params: Any | None = None) -> Iterator[dict]:
        cursor = self._conn.cursor(name="vsl_full_read")
        try:
            cursor.execute(sql, params)
            columns: list[str] | None = None
            for row in cursor:
                if columns is None:
                    columns = [d[0] for d in cursor.description]
                yield dict(zip(columns, row))
        finally:
            cursor.close()

    def _ensure_meta(self) -> None:
        if self._columns is not None:
            return
        rows = self._query(
            "-- columns\n"
            "SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS data_type "
            "FROM pg_attribute a "
            "JOIN pg_class c ON a.attrelid = c.oid "
            "JOIN pg_namespace n ON c.relnamespace = n.oid "
            "WHERE c.relname = %(t)s AND n.nspname = %(s)s AND a.attnum > 0 AND NOT a.attisdropped "
            "ORDER BY a.attnum",
            {"t": self._table_name, "s": self._schema_name},
        )
        if not rows:
            raise StoreConnectionError(
                f"Table {self._schema_name}.{self._table_name} not found or has no columns."
            )
        self._columns = [r["column_name"] for r in rows]
        self._column_types = {r["column_name"]: r["data_type"] for r in rows}
        self._vector_col, self._dimension = self._detect_vector(rows)
        self._id_col = self._detect_named(_ID_NAME_HINTS)
        self._content_col = self._detect_named(_CONTENT_NAME_HINTS)
        self._num_rows = self._fetch_row_count()

    def _detect_vector(self, rows: list[dict]) -> tuple[str, Optional[int]]:
        for row in rows:
            data_type = str(row["data_type"])
            if data_type.lower().startswith("vector"):
                match = _VECTOR_TYPE_RE.match(data_type)
                dimension = int(match.group(1)) if match else None
                return row["column_name"], dimension
        raise StoreConnectionError("No pgvector (vector) column found in the table.")

    def _detect_named(self, hints: tuple[str, ...]) -> Optional[str]:
        for column in self._columns:
            if column.lower() in hints and column != self._vector_col:
                return column
        return None

    def _fetch_row_count(self) -> Optional[int]:
        rows = self._query(
            "-- count_estimate\n"
            "SELECT reltuples::bigint AS estimate FROM pg_class c "
            "JOIN pg_namespace n ON c.relnamespace = n.oid "
            "WHERE c.relname = %(t)s AND n.nspname = %(s)s",
            {"t": self._table_name, "s": self._schema_name},
        )
        estimate = rows[0]["estimate"] if rows else None
        if estimate and estimate > 0:
            return int(estimate)
        exact = self._query(f"-- count_exact\nSELECT count(*) AS exact FROM {self._quoted_table()}")
        return int(exact[0]["exact"]) if exact else None

    def _get_index_info(self) -> tuple[Optional[str], Optional[str], Optional[dict]]:
        if self._index_info is not None:
            return self._index_info
        rows = self._query(
            "-- index\n"
            "SELECT am.amname AS method, pg_get_indexdef(ix.indexrelid) AS indexdef "
            "FROM pg_index ix "
            "JOIN pg_class i ON i.oid = ix.indexrelid "
            "JOIN pg_class t ON t.oid = ix.indrelid "
            "JOIN pg_am am ON i.relam = am.oid "
            "JOIN pg_namespace n ON t.relnamespace = n.oid "
            "WHERE t.relname = %(t)s AND n.nspname = %(s)s AND am.amname IN ('ivfflat', 'hnsw')",
            {"t": self._table_name, "s": self._schema_name},
        )
        if not rows:
            self._index_info = (None, None, None)
            return self._index_info
        method = rows[0]["method"]
        indexdef = str(rows[0]["indexdef"])
        metric = self._metric_from_indexdef(indexdef)
        parameters = self._params_from_indexdef(indexdef) or None
        self._index_info = (method, metric, parameters)
        return self._index_info

    def _metric_from_indexdef(self, indexdef: str) -> Optional[str]:
        match = _OPCLASS_RE.search(indexdef)
        if not match:
            return None
        return _OPCLASS_TO_METRIC.get(match.group(1).lower())

    def _params_from_indexdef(self, indexdef: str) -> dict:
        with_match = re.search(r"WITH\s*\((.*?)\)", indexdef, re.IGNORECASE)
        if not with_match:
            return {}
        params: dict[str, Any] = {}
        for key, value in _WITH_PARAM_RE.findall(with_match.group(1)):
            value = value.strip()
            params[key] = int(value) if value.isdigit() else value
        return params

    def _sample_rows(self, select_list: str, table_ref: str, sample_size: int, seed: int) -> list[dict]:
        if self._num_rows and self._num_rows > _RANDOM_SAMPLE_MAX_ROWS:
            percent = round(min(100.0, max(0.01, 100.0 * sample_size / self._num_rows * 2.0)), 4)
            sql = (
                f"-- sample\nSELECT {select_list} FROM {table_ref} "
                f"TABLESAMPLE SYSTEM ({percent}) LIMIT {int(sample_size)}"
            )
        else:
            sql = f"-- sample\nSELECT {select_list} FROM {table_ref} ORDER BY random() LIMIT {int(sample_size)}"
        return self._query(sql)

    def _row_to_vector(self, row: dict, index: int) -> VectorRecord:
        embedding = _parse_vector(row.get(self._vector_col))
        metadata = {k: v for k, v in row.items() if k != self._vector_col}
        return VectorRecord(
            id=self._extract_id(row, index),
            embedding=embedding,
            metadata=metadata,
            content=self._extract_content(row),
        )

    def _extract_id(self, row: dict, index: int) -> str:
        if self._id_col and row.get(self._id_col) is not None:
            return str(row[self._id_col])
        return f"row-{index}"

    def _extract_content(self, row: dict) -> Optional[str]:
        if self._content_col:
            value = row.get(self._content_col)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _vector_literal(self, embedding: list[float]) -> str:
        return "[" + ",".join(str(float(x)) for x in embedding) + "]"


def _parse_vector(value: Any) -> list[float]:
    """Parse a pgvector value, whether returned as text or already a sequence."""
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip().strip("[]")
        if not stripped:
            return []
        return [float(part) for part in stripped.split(",")]
    return [float(x) for x in value]
