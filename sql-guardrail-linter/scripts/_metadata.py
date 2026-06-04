"""BigQuery metadata resolution for cost_aware / usage_log modes.

Lazy-imports ``google.cloud.bigquery`` so static mode, the renderer, the
parser, and the control modules never require the client library. Two
resolvers implement a tiny protocol the CLI depends on:

- ``table_meta(table_id) -> {"partition_field": str, "pii_columns": set}``
- ``dry_run(sql) -> int | None`` (bytes; ``None`` if the estimate failed)

``NullResolver`` is the static-mode no-op. ``BigQueryMetadataResolver`` reads
``TimePartitioning.field`` for SQ-004 and resolves restricted columns for
SQ-010 from policy tags or column-name patterns. The statement under audit is
never executed: ``dry_run`` always sets ``dry_run=True``.

Cost-safety invariant: the only outbound calls are ``get_table`` (metadata,
no scan) and dry-run query jobs.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from _validation import split_table_id


class NullResolver:
    """Static-mode resolver: no metadata, no dry-run."""

    def table_meta(self, table_id: str) -> dict[str, Any]:
        return {"partition_field": "", "pii_columns": set()}

    def dry_run(self, sql: str) -> Optional[int]:
        return None


class BigQueryMetadataResolver:
    """Resolves partition fields, restricted columns, and dry-run bytes.

    ``pii_policy`` is one of ``"policy_tags"``, ``"labels"``, a JSON map, or
    ``None``. Only ``policy_tags`` and ``labels`` cause metadata-driven PII
    resolution here; a JSON map is applied by the control directly, and the
    rubric's ``pii_column_patterns`` provide the name-based fallback.
    """

    def __init__(self, client: Any, pii_policy: Any = None, pii_patterns: Optional[list[str]] = None) -> None:
        self._client = client
        self._pii_policy = pii_policy
        self._patterns = [re.compile(p, re.IGNORECASE) for p in _safe_patterns(pii_patterns)]
        self._cache: dict[str, dict[str, Any]] = {}
        self.warnings: list[str] = []

    # ----- metadata --------------------------------------------------------

    def table_meta(self, table_id: str) -> dict[str, Any]:
        key = table_id.lower()
        if key in self._cache:
            return self._cache[key]
        meta = {"partition_field": "", "pii_columns": set()}
        try:
            project, dataset, table = split_table_id(table_id)
        except ValueError:
            # Under-qualified or unsafe table name: nothing to resolve.
            self._cache[key] = meta
            return meta
        try:
            tbl = self._client.get_table(f"{project}.{dataset}.{table}")
            meta["partition_field"] = _partition_field(tbl)
            meta["pii_columns"] = self._restricted_columns(tbl)
        except Exception as exc:  # noqa: BLE001  one bad table should not abort the run
            self.warnings.append(f"Could not read metadata for {table_id}: {exc}")
        self._cache[key] = meta
        return meta

    def _restricted_columns(self, tbl: Any) -> set[str]:
        if self._pii_policy == "policy_tags":
            return _policy_tag_columns(tbl.schema)
        if self._pii_policy == "labels":
            # BigQuery labels are table-level, not column-level; fall back to
            # name-pattern detection across the schema's actual column names.
            cols = {f.name.lower() for f in tbl.schema if getattr(f, "name", None)}
            return {c for c in cols if any(p.search(c) for p in self._patterns)}
        return set()

    # ----- dry-run ---------------------------------------------------------

    def dry_run(self, sql: str) -> Optional[int]:
        """Return the dry-run byte estimate, or ``None`` if it could not run.

        Always sets ``dry_run=True`` and disables the query cache so the
        estimate reflects a cold scan. The statement is never executed.
        """
        from google.cloud import bigquery  # lazy import

        try:
            job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
            job = self._client.query(sql, job_config=job_config)
            return int(job.total_bytes_processed or 0)
        except Exception as exc:  # noqa: BLE001  downgrade per-statement failures to a warning
            self.warnings.append(f"Dry-run failed for a statement: {exc}")
            return None


def build_client(billing_project: Optional[str] = None, location: Optional[str] = None) -> Any:
    """Construct a BigQuery client. Raises if the library/credentials are missing."""
    from google.cloud import bigquery  # lazy import

    kwargs: dict[str, Any] = {}
    if billing_project:
        kwargs["project"] = billing_project
    if location:
        kwargs["location"] = location
    return bigquery.Client(**kwargs)


# ----- helpers --------------------------------------------------------------


def _partition_field(tbl: Any) -> str:
    tp = getattr(tbl, "time_partitioning", None)
    if tp is not None and getattr(tp, "field", None):
        return str(tp.field)
    rp = getattr(tbl, "range_partitioning", None)
    if rp is not None and getattr(rp, "field", None):
        return str(rp.field)
    return ""


def _policy_tag_columns(schema: Any) -> set[str]:
    out: set[str] = set()
    for field in schema or []:
        name = getattr(field, "name", None)
        if not name:
            continue
        tags = getattr(field, "policy_tags", None)
        names = getattr(tags, "names", None) if tags is not None else None
        if names:
            out.add(name.lower())
    return out


def _safe_patterns(patterns: Optional[list[str]]) -> list[str]:
    out: list[str] = []
    for p in patterns or []:
        try:
            re.compile(p)
            out.append(p)
        except re.error:
            continue
    return out
