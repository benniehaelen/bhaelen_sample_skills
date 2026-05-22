"""BigQuery adapter.

Targets BigQuery tables used with VECTOR_SEARCH, not generic tables. The
embedding column is an ARRAY<FLOAT64> (REPEATED FLOAT64) column auto-detected
from the schema, preferring a name that looks like an embedding. Authenticates
through Application Default Credentials.

Sampling uses TABLESAMPLE SYSTEM, which samples storage blocks, so it is
representative but block-granular rather than perfectly uniform; a LIMIT caps
the rows at the requested sample size. Search uses the VECTOR_SEARCH table
function. embed_text returns None, so queries must be pre-embedded.

Feature availability varies by region and table. Vector index metadata (index
type and distance type) is read best-effort from INFORMATION_SCHEMA.VECTOR_INDEXES
and reported as unknown when the view is unavailable or the table has no index,
rather than assumed.

Score semantics. VECTOR_SEARCH returns a distance (lower is closer), whereas the
Pinecone adapter returns a similarity (higher is closer). RetrievalResult.score
carries a distance here. Results are ordered best-first either way, which is
what the Tier 3 metrics rely on, but code that compares score magnitudes across
adapters must account for the direction.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterator, Optional

from .base import RetrievalResult, StoreConfig, VectorRecord, VectorStoreAdapter
from .errors import StoreConnectionError

logger = logging.getLogger("vector_store_linter")

_TABLE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_$-]+$")

_VECTOR_NAME_HINTS = ("embedding", "embeddings", "vector", "vec", "feature")
_ID_NAME_HINTS = ("id", "doc_id", "document_id", "chunk_id", "_id", "pk", "key")
_CONTENT_NAME_HINTS = ("text", "content", "chunk_text", "page_content", "body", "document")
_FLOAT_TYPES = ("FLOAT64", "FLOAT")

_METRIC_MAP = {
    "cosine": "cosine",
    "euclidean": "l2",
    "l2": "l2",
    "dot_product": "dot_product",
    "dotproduct": "dot_product",
}


class BigQueryAdapter(VectorStoreAdapter):
    """Adapter for a BigQuery table used with VECTOR_SEARCH."""

    def __init__(self, table: str | None = None, project: str | None = None, client: Any | None = None) -> None:
        """Create an adapter for one BigQuery table.

        Args:
            table: Fully qualified table id in project.dataset.table form.
            project: Optional project override for the client; defaults to the
                project parsed from the table id.
            client: Optional pre-built client, used in tests.
        """
        self._table_id = table
        self._project_override = project
        self._client = client
        self._project: str | None = None
        self._dataset: str | None = None
        self._table: str | None = None
        self._schema: list | None = None
        self._num_rows: int | None = None
        self._partitioning: dict | None = None
        self._clustering: list | None = None
        self._vector_col: str | None = None
        self._id_col: str | None = None
        self._content_col: str | None = None
        self._index_info: tuple[Optional[str], Optional[str], Optional[dict]] | None = None

    def connect(self) -> None:
        """Validate the table id and create the client."""
        if not self._table_id or not _TABLE_ID_RE.match(self._table_id):
            raise StoreConnectionError(
                f"BigQuery table must be in project.dataset.table form, got {self._table_id!r}."
            )
        self._project, self._dataset, self._table = self._table_id.split(".", 2)
        self._client = self._client or self._build_client()

    def describe(self) -> StoreConfig:
        """Return the table configuration as a StoreConfig."""
        self._require_connected()
        self._ensure_meta()
        index_type, distance_type, index_parameters = self._get_index_info()
        metadata_schema = {
            field.name: field.field_type for field in self._schema if field.name != self._vector_col
        }
        return StoreConfig(
            store_type="bigquery",
            vector_count=self._num_rows,
            dimension=self._fetch_dimension(),
            distance_metric=_normalize_metric(distance_type),
            index_type=index_type,
            index_parameters=index_parameters,
            replica_count=None,  # not applicable to BigQuery
            shard_count=None,
            refresh_cadence=None,
            metadata_schema=metadata_schema or None,
            namespaces=None,  # BigQuery has no namespace concept
            raw={
                "partitioning": self._partitioning,
                "clustering": self._clustering,
                "vector_column": self._vector_col,
            },
        )

    def iter_vectors(self, sample_size: Optional[int] = None, seed: int = 42) -> Iterator[VectorRecord]:
        """Yield vectors, sampling with TABLESAMPLE unless sample_size is None."""
        self._require_connected()
        self._ensure_meta()
        table_ref = self._quoted_table()
        if sample_size is None:
            sql = f"SELECT * FROM {table_ref}"
        else:
            percent = _sample_percent(sample_size, self._num_rows)
            sql = f"SELECT * FROM {table_ref} TABLESAMPLE SYSTEM ({percent} PERCENT) LIMIT {int(sample_size)}"
        for index, row in enumerate(self._run(sql)):
            yield self._row_to_vector(dict(row), index)

    def search(
        self,
        query_embedding: list[float],
        k: int,
        filter: Optional[dict] = None,
    ) -> list[RetrievalResult]:
        """Run a VECTOR_SEARCH and return the top k results (ordered by distance)."""
        self._require_connected()
        self._ensure_meta()
        if filter:
            logger.warning("BigQuery adapter does not yet apply metadata filters in search; ignoring filter.")
        _, distance_type, _ = self._get_index_info()
        distance = (distance_type or "COSINE").upper()
        table_ref = self._quoted_table()
        sql = (
            f"SELECT base.* EXCEPT(`{self._vector_col}`), distance "
            f"FROM VECTOR_SEARCH(TABLE {table_ref}, '{self._vector_col}', "
            f"(SELECT @query AS `{self._vector_col}`), top_k => @k, distance_type => '{distance}')"
        )
        job_config = self._array_job_config(query_embedding, k)
        results: list[RetrievalResult] = []
        for index, row in enumerate(self._run(sql, job_config=job_config)):
            mapping = dict(row)
            dist = mapping.pop("distance", None)
            results.append(
                RetrievalResult(
                    doc_id=self._extract_id(mapping, index),
                    score=float(dist) if dist is not None else 0.0,
                    metadata=mapping,
                    content=self._extract_content(mapping),
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
        sql = f"SELECT * FROM {self._quoted_table()} WHERE `{self._id_col}` IN UNNEST(@ids)"
        out: dict[str, VectorRecord] = {}
        for row in self._run(sql, job_config=self._id_array_job_config(ids)):
            record = self._row_to_vector(dict(row), 0)
            out[record.id] = record
        return out

    def _id_array_job_config(self, ids: list[str]) -> Any:
        from google.cloud import bigquery

        return bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", [str(i) for i in ids])]
        )

    def close(self) -> None:
        """Close the client if it exposes close, and drop cached metadata."""
        close = getattr(self._client, "close", None)
        if callable(close):
            close()
        self._schema = None

    # ----- internals -------------------------------------------------------

    def _build_client(self) -> Any:
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise StoreConnectionError(
                "google-cloud-bigquery is not installed. Install it with: pip install google-cloud-bigquery"
            ) from exc
        try:
            return bigquery.Client(project=self._project_override or self._project)
        except Exception as exc:  # noqa: BLE001
            raise StoreConnectionError(f"Could not create a BigQuery client: {exc}") from exc

    def _require_connected(self) -> None:
        if self._client is None or self._table is None:
            raise StoreConnectionError("Adapter is not connected. Call connect() first.")

    def _quoted_table(self) -> str:
        return f"`{self._project}.{self._dataset}.{self._table}`"

    def _run(self, sql: str, job_config: Any | None = None):
        try:
            return self._client.query(sql, job_config=job_config).result()
        except Exception as exc:  # noqa: BLE001
            raise StoreConnectionError(f"BigQuery query failed: {exc}") from exc

    def _array_job_config(self, query_embedding: list[float], k: int) -> Any:
        from google.cloud import bigquery

        return bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("query", "FLOAT64", [float(x) for x in query_embedding]),
                bigquery.ScalarQueryParameter("k", "INT64", int(k)),
            ]
        )

    def _get_table(self) -> Any:
        try:
            return self._client.get_table(f"{self._project}.{self._dataset}.{self._table}")
        except Exception as exc:  # noqa: BLE001
            raise StoreConnectionError(f"Could not read BigQuery table metadata: {exc}") from exc

    def _ensure_meta(self) -> None:
        if self._schema is not None:
            return
        table = self._get_table()
        self._num_rows = getattr(table, "num_rows", None)
        self._schema = list(getattr(table, "schema", []) or [])
        if not self._schema:
            raise StoreConnectionError("BigQuery table reports no schema.")
        self._partitioning, self._clustering = _partition_cluster(table)
        self._vector_col = self._detect_vector_column()
        self._id_col = self._detect_named(_ID_NAME_HINTS)
        self._content_col = self._detect_named(_CONTENT_NAME_HINTS)

    def _detect_vector_column(self) -> str:
        candidates = [
            f for f in self._schema
            if getattr(f, "field_type", None) in _FLOAT_TYPES and getattr(f, "mode", None) == "REPEATED"
        ]
        if not candidates:
            candidates = [f for f in self._schema if getattr(f, "field_type", None) == "VECTOR"]
        if not candidates:
            raise StoreConnectionError("No ARRAY<FLOAT64> embedding column found in the table schema.")
        for field in candidates:
            if any(hint in field.name.lower() for hint in _VECTOR_NAME_HINTS):
                return field.name
        return candidates[0].name

    def _detect_named(self, hints: tuple[str, ...]) -> Optional[str]:
        for field in self._schema:
            if field.name.lower() in hints and field.name != self._vector_col:
                return field.name
        return None

    def _fetch_dimension(self) -> Optional[int]:
        sql = (
            f"SELECT ARRAY_LENGTH(`{self._vector_col}`) AS dim FROM {self._quoted_table()} "
            f"WHERE `{self._vector_col}` IS NOT NULL LIMIT 1"
        )
        try:
            rows = list(self._run(sql))
        except StoreConnectionError:
            return None
        if not rows:
            return None
        value = dict(rows[0]).get("dim")
        return int(value) if value is not None else None

    def _get_index_info(self) -> tuple[Optional[str], Optional[str], Optional[dict]]:
        """Best-effort read of vector-index type and distance from INFORMATION_SCHEMA.

        Returns (index_type, distance_type, parameters). Any piece the view does
        not expose comes back as None rather than assumed.
        """
        if self._index_info is not None:
            return self._index_info
        sql = (
            f"SELECT * FROM `{self._project}.{self._dataset}`.INFORMATION_SCHEMA.VECTOR_INDEXES "
            f"WHERE table_name = '{self._table}'"
        )
        index_type: Optional[str] = None
        distance_type: Optional[str] = None
        parameters: Optional[dict] = None
        try:
            rows = list(self._run(sql))
            if rows:
                row = dict(rows[0])
                raw_type = row.get("index_type")
                index_type = raw_type.lower() if isinstance(raw_type, str) else raw_type
                distance_type = row.get("distance_type")
                parameters = {
                    key: row[key]
                    for key in ("index_name", "index_status", "coverage_percentage")
                    if key in row and row[key] is not None
                }
                parameters = parameters or None
        except StoreConnectionError:
            logger.info("Vector index metadata not available for %s; reporting as unknown.", self._table)
        self._index_info = (index_type, distance_type, parameters)
        return self._index_info

    def _row_to_vector(self, mapping: dict, index: int) -> VectorRecord:
        embedding = mapping.get(self._vector_col) or []
        metadata = {k: v for k, v in mapping.items() if k != self._vector_col}
        return VectorRecord(
            id=self._extract_id(mapping, index),
            embedding=[float(x) for x in embedding],
            metadata=metadata,
            content=self._extract_content(mapping),
        )

    def _extract_id(self, mapping: dict, index: int) -> str:
        if self._id_col and mapping.get(self._id_col) is not None:
            return str(mapping[self._id_col])
        return f"row-{index}"

    def _extract_content(self, mapping: dict) -> Optional[str]:
        if self._content_col:
            value = mapping.get(self._content_col)
            if isinstance(value, str) and value.strip():
                return value
        return None


def _normalize_metric(metric: Any) -> Optional[str]:
    if not isinstance(metric, str):
        return None
    return _METRIC_MAP.get(metric.lower(), metric.lower())


def _partition_cluster(table: Any) -> tuple[Optional[dict], Optional[list]]:
    partitioning: Optional[dict] = None
    time_partitioning = getattr(table, "time_partitioning", None)
    range_partitioning = getattr(table, "range_partitioning", None)
    if time_partitioning is not None:
        partitioning = {"kind": "time", "field": getattr(time_partitioning, "field", None),
                        "type": getattr(time_partitioning, "type_", None)}
    elif range_partitioning is not None:
        partitioning = {"kind": "range", "field": getattr(range_partitioning, "field", None)}
    clustering = list(getattr(table, "clustering_fields", None) or []) or None
    return partitioning, clustering


def _sample_percent(sample_size: int, num_rows: Optional[int]) -> float:
    """Choose a TABLESAMPLE percentage that oversamples a little, then LIMIT caps.

    Block sampling returns whole storage blocks, so requesting roughly twice the
    target share improves the odds of clearing the LIMIT. Falls back to 100 when
    the row count is unknown.
    """
    if not num_rows or num_rows <= 0:
        return 100.0
    percent = 100.0 * sample_size / num_rows * 2.0
    return round(min(100.0, max(0.01, percent)), 4)
