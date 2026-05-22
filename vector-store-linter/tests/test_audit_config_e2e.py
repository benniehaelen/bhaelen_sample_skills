"""End-to-end tests for the audit-config subcommand.

Drives lint.main with a fake adapter injected through _build_adapter, so the
full path (parse, load rubric, describe, run Tier 1, score, render, gate) runs
without a live store. Verifies the success path, the CI gates, the exit codes
for connection and rubric errors, and stdout output.
"""

from __future__ import annotations

import json

import pytest

import lint
from adapters.base import StoreConfig
from adapters.errors import StoreConnectionError


def _config(**overrides) -> StoreConfig:
    base = dict(
        store_type="pinecone",
        vector_count=50000,
        dimension=768,
        distance_metric="cosine",
        index_type="hnsw",
        index_parameters={"M": 32, "ef_search": 100},
        replica_count=2,
        shard_count=1,
        refresh_cadence="daily",
        metadata_schema={"model": "str", "source": "str"},
        namespaces=["tenant-a", "tenant-b"],
        raw={},
    )
    base.update(overrides)
    return StoreConfig(**base)


class FakeAdapter:
    """Context-manager adapter stand-in for the CLI path."""

    def __init__(self, config: StoreConfig | None = None, error: Exception | None = None):
        self._config = config
        self._error = error

    def __enter__(self):
        if self._error is not None:
            raise self._error
        return self

    def __exit__(self, *exc):
        return False

    def describe(self) -> StoreConfig:
        return self._config


def _patch_adapter(monkeypatch, adapter: FakeAdapter) -> None:
    monkeypatch.setattr(lint, "_build_adapter", lambda args: adapter)


# ----- success path --------------------------------------------------------


def test_audit_config_writes_valid_json(monkeypatch, tmp_path):
    _patch_adapter(monkeypatch, FakeAdapter(_config()))
    out = tmp_path / "report.json"
    code = lint.main(
        ["audit-config", "--store", "pinecone", "--index", "kb",
         "--output-format", "json", "--output", str(out)]
    )
    assert code == lint.EXIT_SUCCESS
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["grade"] == "A"
    assert data["overall_score"] >= 90
    assert "tier_1_config" in data["tier_scores"]
    assert data["metadata"]["store"] == {"store_type": "pinecone", "index": "kb"}
    assert data["metadata"]["modes"] == ["audit-config"]


def test_audit_config_to_stdout(monkeypatch, capsys):
    _patch_adapter(monkeypatch, FakeAdapter(_config()))
    code = lint.main(["audit-config", "--store", "pinecone", "--index", "kb"])
    assert code == lint.EXIT_SUCCESS
    data = json.loads(capsys.readouterr().out)
    assert data["schema_version"] == "1.0"


def test_connection_string_not_in_output(monkeypatch, capsys):
    # pgvector path would carry a secret; ensure it never reaches the metadata.
    _patch_adapter(monkeypatch, FakeAdapter(_config(store_type="pgvector")))
    lint.main(
        ["audit-config", "--store", "pgvector",
         "--connection-string", "postgresql://user:secret@host/db", "--table", "embeddings"]
    )
    out = capsys.readouterr().out
    assert "secret" not in out
    assert "connection_string" not in out


# ----- CI gates ------------------------------------------------------------


def test_expect_min_score_pass(monkeypatch):
    _patch_adapter(monkeypatch, FakeAdapter(_config()))
    code = lint.main(
        ["audit-config", "--store", "pinecone", "--index", "kb", "--expect-min-score", "80"]
    )
    assert code == lint.EXIT_SUCCESS


def test_expect_min_score_fail(monkeypatch):
    # A barely-configured store scores low.
    poor = _config(
        dimension=None, distance_metric=None, index_type=None, index_parameters=None,
        vector_count=None, replica_count=None, shard_count=None, refresh_cadence=None,
        metadata_schema=None, namespaces=None,
    )
    _patch_adapter(monkeypatch, FakeAdapter(poor))
    code = lint.main(
        ["audit-config", "--store", "pinecone", "--index", "kb", "--expect-min-score", "90"]
    )
    assert code == lint.EXIT_EXPECTATION_NOT_MET


def test_expect_zero_dimension_mismatches_fail(monkeypatch):
    _patch_adapter(monkeypatch, FakeAdapter(_config(dimension=None)))
    code = lint.main(
        ["audit-config", "--store", "pinecone", "--index", "kb", "--expect-zero-dimension-mismatches"]
    )
    assert code == lint.EXIT_EXPECTATION_NOT_MET


def test_tier2_gate_rejected_on_audit_config(monkeypatch):
    _patch_adapter(monkeypatch, FakeAdapter(_config()))
    with pytest.raises(SystemExit) as exc:
        lint.main(["audit-config", "--store", "pinecone", "--index", "kb", "--expect-zero-orphans"])
    assert exc.value.code == lint.EXIT_USAGE_ERROR


# ----- error paths ---------------------------------------------------------


def test_connection_error_exit_code(monkeypatch):
    _patch_adapter(monkeypatch, FakeAdapter(error=StoreConnectionError("index not found")))
    code = lint.main(["audit-config", "--store", "pinecone", "--index", "missing"])
    assert code == lint.EXIT_STORE_CONNECTION_ERROR


def test_bad_rubric_path_exit_code(monkeypatch, tmp_path):
    _patch_adapter(monkeypatch, FakeAdapter(_config()))
    code = lint.main(
        ["audit-config", "--store", "pinecone", "--index", "kb",
         "--rubric-config", str(tmp_path / "no_such.yaml")]
    )
    assert code == lint.EXIT_USAGE_ERROR


def test_unregistered_adapter_exit_code(monkeypatch):
    # An adapter the registry cannot supply surfaces as a ValueError, which the
    # CLI maps to a usage error. Simulated directly so the test does not depend
    # on which stores happen to be registered.
    def _raise(args):
        raise ValueError("No adapter registered for store type 'mystery'.")

    monkeypatch.setattr(lint, "_build_adapter", _raise)
    code = lint.main(["audit-config", "--store", "pinecone", "--index", "kb"])
    assert code == lint.EXIT_USAGE_ERROR
