"""End-to-end output-format tests.

Runs audit-config through the CLI with a fake adapter and each output format,
confirming a file of the right kind is written, that the JSON conforms to the
required keys of output_schema.json, and that markdown and html no longer fall
back to JSON now that their renderers are registered.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

import pytest

import lint
from adapters.base import StoreConfig

_SCHEMA = Path(__file__).resolve().parent.parent / "scripts" / "output_schema.json"


def _config() -> StoreConfig:
    return StoreConfig(
        store_type="pinecone", vector_count=50000, dimension=768, distance_metric="cosine",
        index_type="hnsw", index_parameters={"M": 32, "ef_search": 100}, replica_count=2,
        shard_count=1, refresh_cadence="daily", metadata_schema={"model": "str", "source": "str"},
        namespaces=["a", "b"], raw={},
    )


class FakeAdapter:
    def __init__(self, config):
        self._config = config

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def describe(self):
        return self._config


@pytest.fixture(autouse=True)
def _patch_adapter(monkeypatch):
    monkeypatch.setattr(lint, "_build_adapter", lambda args: FakeAdapter(_config()))


def _run(fmt, out_path):
    return lint.main(
        ["audit-config", "--store", "pinecone", "--index", "kb",
         "--output-format", fmt, "--output", str(out_path)]
    )


def test_json_format_is_schema_conformant(tmp_path):
    out = tmp_path / "report.json"
    assert _run("json", out) == lint.EXIT_SUCCESS
    data = json.loads(out.read_text(encoding="utf-8"))
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    for key in schema["required"]:
        assert key in data, f"missing required key {key!r}"


def test_markdown_format_writes_markdown(tmp_path):
    out = tmp_path / "report.md"
    assert _run("markdown", out) == lint.EXIT_SUCCESS
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Vector Store Scorecard")
    assert "## Summary" in text


def test_html_format_writes_html(tmp_path):
    out = tmp_path / "report.html"
    assert _run("html", out) == lint.EXIT_SUCCESS
    text = out.read_text(encoding="utf-8")
    assert text.startswith("<!DOCTYPE html>")
    HTMLParser().feed(text)  # parses
    assert "<script" not in text  # self-contained, no JS


def test_each_format_is_distinct(tmp_path):
    paths = {}
    for fmt, name in (("json", "r.json"), ("markdown", "r.md"), ("html", "r.html")):
        p = tmp_path / name
        _run(fmt, p)
        paths[fmt] = p.read_text(encoding="utf-8")
    assert paths["json"] != paths["markdown"] != paths["html"]
    assert paths["json"].lstrip().startswith("{")


def test_default_output_format_is_json(monkeypatch, capsys):
    # No --output-format and no --output: JSON to stdout.
    monkeypatch.setattr(lint, "_build_adapter", lambda args: FakeAdapter(_config()))
    assert lint.main(["audit-config", "--store", "pinecone", "--index", "kb"]) == lint.EXIT_SUCCESS
    data = json.loads(capsys.readouterr().out)
    assert data["schema_version"] == "1.0"
