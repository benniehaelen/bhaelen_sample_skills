"""Tests for the tokenizer wrappers and tool serialization.

The cl100k_base path uses real ``tiktoken`` when available (it is in the
skill's dev requirements). The claude path is exercised against a stubbed
``requests`` module so no network or API key is needed.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import _tokenizer  # noqa: E402


# The cl100k_base path needs tiktoken; the claude path does not. Marker
# applies only to tests that exercise the default tokenizer, so the
# claude tests still run in a tiktoken-free environment.
_HAS_TIKTOKEN = importlib.util.find_spec("tiktoken") is not None
needs_tiktoken = pytest.mark.skipif(not _HAS_TIKTOKEN, reason="tiktoken not installed")


# A small tool used across the breakdown tests.
SAMPLE_TOOL = {
    "name": "search_orders",
    "description": "Search orders by status and customer.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "UUID v4 of the customer."},
            "status": {"type": "string", "enum": ["pending", "shipped", "delivered"]},
        },
        "required": ["customer_id"],
    },
    "annotations": {"readOnlyHint": True},
}


# ----- count_tokens (cl100k_base) -----------------------------------------


def test_count_tokens_empty_returns_zero():
    # Short-circuit before the encoder, so this works without tiktoken.
    assert _tokenizer.count_tokens("", "cl100k_base") == 0


@needs_tiktoken
def test_count_tokens_simple_string():
    # Exact number depends on tiktoken's BPE; assert it's small and positive.
    n = _tokenizer.count_tokens("Hello, world!", "cl100k_base")
    assert 2 <= n <= 6


@needs_tiktoken
def test_count_tokens_longer_string_strictly_larger():
    short = _tokenizer.count_tokens("Hello", "cl100k_base")
    longer = _tokenizer.count_tokens("Hello, this is a longer string.", "cl100k_base")
    assert longer > short


def test_count_tokens_unsupported_tokenizer_raises():
    with pytest.raises(ValueError, match="Unsupported tokenizer"):
        _tokenizer.count_tokens("x", "made_up")


# ----- serialize_tool ------------------------------------------------------


def test_serialize_tool_raw_is_compact_json():
    s = _tokenizer.serialize_tool(SAMPLE_TOOL, "raw")
    # Compact: no spaces after separators.
    assert ": " not in s and ", " not in s
    # Round-trips.
    assert json.loads(s)["name"] == "search_orders"
    # raw keeps annotations and uses inputSchema.
    assert "inputSchema" in s and "annotations" in s


def test_serialize_tool_anthropic_uses_input_schema_and_drops_annotations():
    s = _tokenizer.serialize_tool(SAMPLE_TOOL, "anthropic")
    parsed = json.loads(s)
    assert parsed["name"] == "search_orders"
    assert "input_schema" in parsed
    assert "inputSchema" not in parsed
    assert "annotations" not in parsed


def test_serialize_tool_unsupported_raises():
    with pytest.raises(ValueError, match="Unsupported serialization"):
        _tokenizer.serialize_tool(SAMPLE_TOOL, "made_up")


# ----- count_tool ----------------------------------------------------------


@needs_tiktoken
def test_count_tool_raw_breakdown_sums_to_total():
    out = _tokenizer.count_tool(SAMPLE_TOOL, "cl100k_base", "raw")
    assert out["total"] == out["name"] + out["description"] + out["schema"] + out["annotations"]
    # Each section is positive for this tool.
    assert out["name"] > 0
    assert out["description"] > 0
    assert out["schema"] > 0
    assert out["annotations"] > 0


@needs_tiktoken
def test_count_tool_anthropic_drops_annotations_count():
    raw = _tokenizer.count_tool(SAMPLE_TOOL, "cl100k_base", "raw")
    ant = _tokenizer.count_tool(SAMPLE_TOOL, "cl100k_base", "anthropic")
    assert ant["annotations"] == 0
    assert ant["total"] == ant["name"] + ant["description"] + ant["schema"]
    # raw counts annotations, anthropic does not, so raw total is higher.
    assert raw["total"] > ant["total"]


@needs_tiktoken
def test_count_tool_empty_sections_are_zero():
    bare = {"name": "x", "description": "", "inputSchema": {}, "annotations": {}}
    out = _tokenizer.count_tool(bare, "cl100k_base", "raw")
    assert out["description"] == 0
    assert out["schema"] == 0
    assert out["annotations"] == 0
    assert out["name"] > 0
    assert out["total"] == out["name"]


def test_count_tool_unsupported_serialization_raises():
    with pytest.raises(ValueError, match="Unsupported serialization"):
        _tokenizer.count_tool(SAMPLE_TOOL, "cl100k_base", "made_up")


# ----- claude tokenizer (stubbed) -----------------------------------------


class _StubResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


class _StubRequests:
    """Captures ``post()`` calls and returns a scripted token count."""

    def __init__(self, token_count: int = 7):
        self.calls: list[dict] = []
        self._token_count = token_count

    def post(self, url, json, headers, timeout):  # noqa: A002
        self.calls.append({"url": url, "body": json, "headers": headers})
        return _StubResponse({"input_tokens": self._token_count})


def _install_stub_requests(monkeypatch, stub: _StubRequests) -> None:
    mod = types.ModuleType("requests")
    mod.post = stub.post  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "requests", mod)


def _clear_claude_cache():
    _tokenizer._claude_count_cached.cache_clear()


def test_count_tokens_claude_requires_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _clear_claude_cache()
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        _tokenizer.count_tokens("anything", "claude")


def test_count_tokens_claude_makes_http_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _clear_claude_cache()
    stub = _StubRequests(token_count=42)
    _install_stub_requests(monkeypatch, stub)

    n = _tokenizer.count_tokens("Hello, world!", "claude")
    assert n == 42

    # Headers and body match the documented contract.
    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call["url"] == _tokenizer._ANTHROPIC_COUNT_URL
    assert call["headers"]["x-api-key"] == "test-key"
    assert call["headers"]["anthropic-version"] == _tokenizer._ANTHROPIC_VERSION
    assert call["body"]["messages"] == [{"role": "user", "content": "Hello, world!"}]
    assert call["body"]["model"]  # model is set, exact value documented in the module


def test_count_tokens_claude_caches_repeat_calls(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _clear_claude_cache()
    stub = _StubRequests(token_count=7)
    _install_stub_requests(monkeypatch, stub)

    _tokenizer.count_tokens("same text", "claude")
    _tokenizer.count_tokens("same text", "claude")
    _tokenizer.count_tokens("different text", "claude")

    assert len(stub.calls) == 2  # one per unique text, not one per call


def test_count_tokens_claude_empty_text_skips_network(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # would error if called
    _clear_claude_cache()
    # An empty string short-circuits to 0 before the network check.
    assert _tokenizer.count_tokens("", "claude") == 0
