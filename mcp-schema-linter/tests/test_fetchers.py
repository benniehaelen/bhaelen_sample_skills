"""Tests for the input fetchers and the canonical normalizer.

The tests load ``_fetchers`` directly from the sibling ``scripts/`` folder
so the package layout in the other skills is mirrored. URL transport is
covered with a stubbed ``requests`` module (no network).
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import _fetchers  # noqa: E402


# A realistic captured tools/list response. Two tools so siblings/overlap
# work in later rule tests; one read-only, one mutating; one with an enum.
TOOLS_LIST_RESPONSE = {
    "jsonrpc": "2.0",
    "id": 2,
    "result": {
        "tools": [
            {
                "name": "search_orders",
                "description": "Search orders by status and customer. Use when you need to find existing orders, not when creating new ones.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "UUID v4 of the customer."},
                        "status": {"type": "string", "enum": ["pending", "shipped", "delivered"]},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "required": ["customer_id"],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "create_order",
                "description": "This tool allows you to create a new order in the system.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["items"],
                },
                "annotations": {"destructiveHint": False},
            },
        ]
    },
}


# ----- parse_payload --------------------------------------------------------


def test_parse_payload_accepts_jsonrpc_envelope():
    out = _fetchers.parse_payload(TOOLS_LIST_RESPONSE, source="test://envelope")
    assert out["server"]["source"] == "test://envelope"
    assert [t["name"] for t in out["tools"]] == ["search_orders", "create_order"]
    # Optional fields default to empty containers, not None.
    assert out["tools"][1]["annotations"] == {"destructiveHint": False}
    assert isinstance(out["tools"][0]["inputSchema"], dict)
    assert out["extras"] == {"resource_count": 0, "prompt_count": 0}


def test_parse_payload_accepts_server_export():
    export = {
        "server": {"name": "billing", "version": "2.4.1"},
        "tools": TOOLS_LIST_RESPONSE["result"]["tools"],
        "resources": [{"uri": "billing://customers"}],
        "prompts": [],
    }
    out = _fetchers.parse_payload(export, source="test://export")
    assert out["server"] == {"name": "billing", "version": "2.4.1", "source": "test://export"}
    assert out["extras"]["resource_count"] == 1
    assert out["extras"]["prompt_count"] == 0


def test_parse_payload_accepts_bare_array():
    arr = TOOLS_LIST_RESPONSE["result"]["tools"]
    out = _fetchers.parse_payload(arr, source="test://array")
    assert len(out["tools"]) == 2
    assert out["server"] == {"name": None, "version": None, "source": "test://array"}


def test_parse_payload_rejects_missing_tools_key():
    with pytest.raises(ValueError, match="tools"):
        _fetchers.parse_payload({"jsonrpc": "2.0", "result": {}}, source="x")


def test_parse_payload_rejects_non_object_payload():
    with pytest.raises(ValueError, match="object or array"):
        _fetchers.parse_payload("just a string", source="x")  # type: ignore[arg-type]


def test_parse_payload_coerces_schema_aliases():
    raw = [{
        "name": "do_thing",
        "description": "Does the thing.",
        "input_schema": {"type": "object", "properties": {"x": {"type": "integer"}}},
        "annotation": {"readOnlyHint": True},
    }]
    out = _fetchers.parse_payload(raw, source="x")
    tool = out["tools"][0]
    assert tool["inputSchema"]["properties"]["x"]["type"] == "integer"
    assert tool["annotations"] == {"readOnlyHint": True}


def test_parse_payload_rejects_tool_without_name():
    with pytest.raises(ValueError, match="name"):
        _fetchers.parse_payload([{"description": "no name"}], source="x")


def test_parse_payload_drops_non_dict_tool_entries():
    raw = [
        {"name": "ok", "description": "fine"},
        "garbage",  # silently dropped
        12345,      # silently dropped
    ]
    out = _fetchers.parse_payload(raw, source="x")
    assert [t["name"] for t in out["tools"]] == ["ok"]


# ----- load_from_file / load_from_json -------------------------------------


def test_load_from_file_reads_jsonrpc_envelope(tmp_path: Path):
    p = tmp_path / "tools.json"
    p.write_text(json.dumps(TOOLS_LIST_RESPONSE), encoding="utf-8")
    out = _fetchers.load_from_file(p)
    assert [t["name"] for t in out["tools"]] == ["search_orders", "create_order"]
    # source is the resolved absolute path so reports can cite it.
    assert out["server"]["source"] == str(p.resolve())


def test_load_from_json_inline():
    text = json.dumps(TOOLS_LIST_RESPONSE["result"]["tools"])
    out = _fetchers.load_from_json(text)
    assert len(out["tools"]) == 2
    assert out["server"]["source"] == "<inline>"


# ----- load_from_url with a stubbed requests module ------------------------


class _StubResponse:
    """Minimal stand-in for ``requests.Response`` used by ``_post_jsonrpc``."""

    def __init__(self, payload: dict | None = None, sse_lines: list[str] | None = None,
                 content_type: str = "application/json", status: int = 200):
        self._payload = payload
        self._sse_lines = sse_lines or []
        self.headers = {"Content-Type": content_type}
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        assert self._payload is not None
        return self._payload

    def iter_lines(self, decode_unicode: bool = False):
        for line in self._sse_lines:
            yield line


class _StubRequests:
    """Captures ``post()`` calls and replays scripted responses by JSON-RPC method."""

    def __init__(self, responses_by_method: dict[str, _StubResponse]):
        self._by_method = responses_by_method
        self.calls: list[dict] = []

    def post(self, url, json, headers, timeout, stream):  # noqa: A002 (shadowing the json module is fine here)
        self.calls.append({"url": url, "body": json, "headers": headers})
        method = json["method"]
        if method in self._by_method:
            return self._by_method[method]
        raise AssertionError(f"unexpected method: {method}")


def _install_stub_requests(monkeypatch, stub: _StubRequests) -> None:
    mod = types.ModuleType("requests")
    mod.post = stub.post  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "requests", mod)


def test_load_from_url_application_json(monkeypatch):
    stub = _StubRequests({
        "initialize": _StubResponse(payload={
            "jsonrpc": "2.0", "id": 1,
            "result": {"serverInfo": {"name": "billing", "version": "2.4.1"}},
        }),
        "tools/list": _StubResponse(payload=TOOLS_LIST_RESPONSE),
        "resources/list": _StubResponse(payload={"jsonrpc": "2.0", "id": 3, "result": {"resources": [{"uri": "x"}]}}),
        "prompts/list": _StubResponse(payload={"jsonrpc": "2.0", "id": 3, "result": {"prompts": []}}),
    })
    _install_stub_requests(monkeypatch, stub)

    out = _fetchers.load_from_url("https://billing.example.com/mcp")
    assert out["server"] == {"name": "billing", "version": "2.4.1", "source": "https://billing.example.com/mcp"}
    assert [t["name"] for t in out["tools"]] == ["search_orders", "create_order"]
    assert out["extras"] == {"resource_count": 1, "prompt_count": 0}

    # Three best-effort calls plus initialize.
    methods = [c["body"]["method"] for c in stub.calls]
    assert methods == ["initialize", "tools/list", "resources/list", "prompts/list"]


def test_load_from_url_sse_response(monkeypatch):
    # Server replies via Streamable HTTP's SSE mode. Each event carries a
    # JSON-RPC response on a `data:` line; non-matching ids are ignored.
    sse = [
        ": keepalive",
        "event: message",
        f"data: {json.dumps({'jsonrpc': '2.0', 'id': 99, 'result': {'serverInfo': {'name': 'noise'}}})}",
        "",
        "event: message",
        f"data: {json.dumps({'jsonrpc': '2.0', 'id': 2, 'result': TOOLS_LIST_RESPONSE['result']})}",
        "",
    ]
    stub = _StubRequests({
        "initialize": _StubResponse(status=500),  # initialize fails, fetcher should still continue
        "tools/list": _StubResponse(sse_lines=sse, content_type="text/event-stream"),
        "resources/list": _StubResponse(status=500),
        "prompts/list": _StubResponse(status=500),
    })
    _install_stub_requests(monkeypatch, stub)

    out = _fetchers.load_from_url("https://billing.example.com/mcp")
    assert [t["name"] for t in out["tools"]] == ["search_orders", "create_order"]
    # initialize failed, so serverInfo remains unknown.
    assert out["server"]["name"] is None
    assert out["server"]["source"] == "https://billing.example.com/mcp"


def test_load_from_url_raises_when_requests_missing(monkeypatch):
    # Hide `requests` even if installed in the environment.
    monkeypatch.setitem(sys.modules, "requests", None)
    with pytest.raises(RuntimeError, match="requests"):
        _fetchers.load_from_url("https://billing.example.com/mcp")


class _QueueStubRequests:
    """Returns queued responses per method (last entry repeats); records calls."""

    def __init__(self, queues: dict[str, list[_StubResponse]]):
        self._queues = {m: list(v) for m, v in queues.items()}
        self.calls: list[dict] = []

    def post(self, url, json, headers, timeout, stream):  # noqa: A002
        self.calls.append({"url": url, "body": json})
        method = json["method"]
        q = self._queues.get(method)
        if not q:
            raise AssertionError(f"unexpected method: {method}")
        return q.pop(0) if len(q) > 1 else q[0]


def test_load_from_url_paginates_tools(monkeypatch):
    # tools/list returns two pages; page 1 carries a nextCursor, page 2 does not.
    page1 = _StubResponse(payload={"jsonrpc": "2.0", "id": 2, "result": {
        "tools": [{"name": "tool_a", "description": "First."}],
        "nextCursor": "CURSOR_PAGE_2",
    }})
    page2 = _StubResponse(payload={"jsonrpc": "2.0", "id": 2, "result": {
        "tools": [{"name": "tool_b", "description": "Second."}],
    }})
    stub = _QueueStubRequests({
        "initialize": [_StubResponse(payload={"jsonrpc": "2.0", "id": 1, "result": {}})],
        "tools/list": [page1, page2],
        "resources/list": [_StubResponse(payload={"jsonrpc": "2.0", "id": 3, "result": {"resources": []}})],
        "prompts/list": [_StubResponse(payload={"jsonrpc": "2.0", "id": 3, "result": {"prompts": []}})],
    })
    _install_stub_requests(monkeypatch, stub)

    out = _fetchers.load_from_url("https://x/mcp")
    # Both pages are accumulated, in order.
    assert [t["name"] for t in out["tools"]] == ["tool_a", "tool_b"]

    # The first tools/list call sends no cursor; the second carries page 1's cursor.
    tools_calls = [c for c in stub.calls if c["body"]["method"] == "tools/list"]
    assert len(tools_calls) == 2
    assert tools_calls[0]["body"]["params"] == {}
    assert tools_calls[1]["body"]["params"] == {"cursor": "CURSOR_PAGE_2"}


def test_load_from_url_pagination_safety_cap(monkeypatch):
    # A server that returns a non-advancing cursor must not loop forever.
    monkeypatch.setattr(_fetchers, "_MAX_LIST_PAGES", 5)
    looping = _StubResponse(payload={"jsonrpc": "2.0", "id": 2, "result": {
        "tools": [{"name": "t", "description": "x"}],
        "nextCursor": "NEVER_ADVANCES",
    }})
    stub = _QueueStubRequests({
        "initialize": [_StubResponse(payload={"jsonrpc": "2.0", "id": 1, "result": {}})],
        "tools/list": [looping],
    })
    _install_stub_requests(monkeypatch, stub)

    with pytest.raises(RuntimeError, match="more than 5 pages"):
        _fetchers.load_from_url("https://x/mcp")
