"""Input fetchers and normalization for the MCP schema linter.

Three input modes produce one canonical internal representation:

    {
      "server": {"name": str | None, "version": str | None, "source": str},
      "tools": [
        {"name": str, "description": str, "inputSchema": dict, "annotations": dict},
        ...
      ],
      "extras": {"resource_count": int, "prompt_count": int}
    }

Public functions:

- ``load_from_url(url)`` calls a live MCP server. Uses a stateless JSON-RPC
  POST against the URL, accepting both ``application/json`` and
  ``text/event-stream`` responses (the two reply modes of the Streamable
  HTTP transport). The official ``mcp`` Python SDK is async-only and is
  intentionally not required at v1; callers who prefer it can do their
  own fetch and pass the result through ``parse_payload``.
- ``load_from_file(path)`` reads a JSON file from disk.
- ``load_from_json(text)`` parses an inline JSON string.

The file and JSON modes accept three input shapes:

1. A JSON-RPC envelope: ``{"jsonrpc": "2.0", "result": {"tools": [...]}}``.
2. A server export: ``{"server": {...}, "tools": [...], "resources": [...], "prompts": [...]}``.
3. A bare list of tool objects.

No tokenization, no scoring. ``requests`` is imported lazily inside
``load_from_url`` so file and JSON modes work without it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# ----- public API -----------------------------------------------------------


def load_from_url(url: str, timeout: float = 30.0) -> dict[str, Any]:
    """Fetch ``tools/list`` from a live MCP server and normalize.

    Performs a best-effort ``initialize`` handshake to capture ``serverInfo``,
    then ``tools/list``, then optional ``resources/list`` and ``prompts/list``
    to populate the extras counters. All three list calls follow MCP cursor
    pagination (``nextCursor``) to completion, so large catalogs are fetched
    in full rather than truncated to the first page. Raises ``RuntimeError``
    if the server cannot be reached or the response is not parseable.
    """
    return _load_via_jsonrpc(url, timeout)


def load_from_file(path: str | Path) -> dict[str, Any]:
    """Read a JSON file from disk and normalize its content."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return parse_payload(json.loads(text), source=str(p.resolve()))


def load_from_json(text: str, source: str = "<inline>") -> dict[str, Any]:
    """Parse a JSON string and normalize it.

    ``source`` is the value placed in ``server.source`` when the payload
    does not carry one of its own.
    """
    return parse_payload(json.loads(text), source=source)


def parse_payload(payload: Any, source: str) -> dict[str, Any]:
    """Normalize an already-parsed payload into the canonical shape.

    Accepts the three shapes documented at the top of this module. Raises
    ``ValueError`` for anything that does not match.
    """
    if isinstance(payload, list):
        return _build(server_info=None, tools=payload, resources=[], prompts=[], source=source)

    if not isinstance(payload, dict):
        raise ValueError("Top-level JSON must be an object or array.")

    # JSON-RPC envelope: tools live under result.tools.
    if "result" in payload and isinstance(payload["result"], dict) and "tools" in payload["result"]:
        body = payload["result"]
    elif "tools" in payload:
        body = payload
    else:
        raise ValueError("Payload does not contain a `tools` key (looked at top level and under `result`).")

    tools = body.get("tools", [])
    if not isinstance(tools, list):
        raise ValueError("`tools` must be a list.")

    return _build(
        server_info=body.get("server") or body.get("serverInfo") or payload.get("serverInfo"),
        tools=tools,
        resources=body.get("resources") or payload.get("resources") or [],
        prompts=body.get("prompts") or payload.get("prompts") or [],
        source=source,
    )


# ----- normalization helpers -----------------------------------------------


_TOOL_SCHEMA_ALIASES = ("inputSchema", "input_schema", "schema", "parameters")
_TOOL_ANNOTATION_ALIASES = ("annotations", "annotation")


def _build(
    *,
    server_info: dict[str, Any] | None,
    tools: list[Any],
    resources: list[Any],
    prompts: list[Any],
    source: str,
) -> dict[str, Any]:
    """Assemble the canonical dict from normalized parts."""
    server: dict[str, Any] = {"name": None, "version": None, "source": source}
    if isinstance(server_info, dict):
        if isinstance(server_info.get("name"), str):
            server["name"] = server_info["name"]
        if isinstance(server_info.get("version"), str):
            server["version"] = server_info["version"]

    norm_tools = [_normalize_tool(t) for t in tools if isinstance(t, dict)]

    return {
        "server": server,
        "tools": norm_tools,
        "extras": {
            "resource_count": len(resources) if isinstance(resources, list) else 0,
            "prompt_count": len(prompts) if isinstance(prompts, list) else 0,
        },
    }


def _normalize_tool(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce one tool dict to the canonical four-key shape.

    Accepts common field aliases (``input_schema``, ``schema``, ``parameters``,
    ``annotation``). Missing optional fields default to empty containers
    rather than ``None`` so downstream rules can treat them uniformly.
    """
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"Tool entry has no `name`: {raw!r}")

    description = raw.get("description") or ""
    if not isinstance(description, str):
        raise ValueError(f"Tool {name!r} description is not a string.")

    schema: dict[str, Any] = {}
    for key in _TOOL_SCHEMA_ALIASES:
        candidate = raw.get(key)
        if isinstance(candidate, dict):
            schema = candidate
            break

    annotations: dict[str, Any] = {}
    for key in _TOOL_ANNOTATION_ALIASES:
        candidate = raw.get(key)
        if isinstance(candidate, dict):
            annotations = candidate
            break

    return {
        "name": name,
        "description": description,
        "inputSchema": schema,
        "annotations": annotations,
    }


# ----- URL transport (Streamable HTTP, JSON-RPC) ---------------------------


def _require_requests() -> Any:
    """Lazy-import ``requests`` so file and JSON modes work without it."""
    try:
        import requests  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "URL mode requires the `requests` package. Install it, or pass --tools-file / --tools-json instead."
        ) from exc
    return requests


def _load_via_jsonrpc(url: str, timeout: float) -> dict[str, Any]:
    """Stateless JSON-RPC POST against a Streamable HTTP MCP endpoint.

    Initialize is best-effort: a server that does not require it will
    still answer ``tools/list`` directly. Resources and prompts listings
    are also best-effort and populate only the counter, not the rubric.
    """
    requests = _require_requests()

    server_info: dict[str, Any] | None = None
    try:
        init_resp = _post_jsonrpc(requests, url, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "mcp-schema-linter", "version": "1.0"},
        }, timeout=timeout, request_id=1)
        candidate = init_resp.get("result", {}).get("serverInfo")
        if isinstance(candidate, dict):
            server_info = candidate
    except Exception:
        # Some servers skip initialize for stateless clients. Continue.
        pass

    tools = _list_all_pages(requests, url, "tools/list", "tools", timeout=timeout, request_id=2)

    resources: list[Any] = []
    prompts: list[Any] = []
    for method, key, sink in (("resources/list", "resources", resources), ("prompts/list", "prompts", prompts)):
        try:
            sink.extend(_list_all_pages(requests, url, method, key, timeout=timeout, request_id=3))
        except Exception:
            # Optional capability; ignore failures (only feeds the count warning).
            pass

    return _build(server_info=server_info, tools=tools, resources=resources, prompts=prompts, source=url)


# Safety cap so a server returning a non-advancing cursor cannot loop forever.
_MAX_LIST_PAGES = 1000


def _list_all_pages(
    requests_mod: Any,
    url: str,
    method: str,
    key: str,
    *,
    timeout: float,
    request_id: int,
) -> list[Any]:
    """Call a paginated MCP list method, following ``nextCursor`` to the end.

    MCP list methods (``tools/list``, ``resources/list``, ``prompts/list``)
    return at most one page plus an optional ``nextCursor``. To get the full
    set the client re-issues the call with ``params.cursor = nextCursor``
    until the server stops returning a cursor. Without this loop a live
    audit of a large catalog would silently score only the first page.

    Raises ``RuntimeError`` if a page's result is not a list, or if the
    server returns more than ``_MAX_LIST_PAGES`` pages (a non-advancing
    cursor would otherwise spin forever).
    """
    items: list[Any] = []
    cursor: Any = None
    for _ in range(_MAX_LIST_PAGES):
        params = {"cursor": cursor} if cursor else {}
        resp = _post_jsonrpc(requests_mod, url, method, params, timeout=timeout, request_id=request_id)
        result = resp.get("result", {}) if isinstance(resp, dict) else {}
        page = result.get(key, [])
        if not isinstance(page, list):
            raise RuntimeError(f"{method} result is not a list: {resp!r}")
        items.extend(page)
        cursor = result.get("nextCursor")
        if not cursor:
            return items
    raise RuntimeError(
        f"{method} returned more than {_MAX_LIST_PAGES} pages; aborting to avoid an unbounded loop "
        "(the server may be returning a non-advancing cursor)."
    )


_SSE_DATA_LINE = re.compile(r"^data:\s*(.*)$")


def _post_jsonrpc(
    requests_mod: Any,
    url: str,
    method: str,
    params: dict[str, Any],
    *,
    timeout: float,
    request_id: int,
) -> dict[str, Any]:
    """POST a single JSON-RPC request and return the parsed response.

    Accepts both ``application/json`` and ``text/event-stream`` responses.
    For SSE, reads the stream until the first event whose ``data:`` payload
    is a JSON-RPC response matching ``request_id``.
    """
    body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    resp = requests_mod.post(url, json=body, headers=headers, timeout=timeout, stream=True)
    resp.raise_for_status()
    ctype = resp.headers.get("Content-Type", "").lower()

    if "text/event-stream" in ctype:
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            match = _SSE_DATA_LINE.match(raw_line)
            if not match:
                continue
            payload_str = match.group(1).strip()
            if not payload_str:
                continue
            try:
                parsed = json.loads(payload_str)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("id") == request_id:
                return parsed
        raise RuntimeError(f"SSE stream ended without a response for {method}.")

    return resp.json()
