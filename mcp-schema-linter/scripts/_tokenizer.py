"""Tokenizer wrappers and tool serialization for the MCP schema linter.

Two orthogonal switches affect how a tool's token cost is measured:

- ``tokenizer``: ``cl100k_base`` (default, offline via ``tiktoken``) or
  ``claude`` (calls Anthropic's ``/v1/messages/count_tokens`` endpoint;
  requires ``ANTHROPIC_API_KEY`` in the environment).
- ``serialization``: ``raw`` (default; tool object as compact JSON,
  including the MCP ``annotations`` block) or ``anthropic`` (approximates
  the Anthropic Tools API wire format: ``input_schema`` instead of
  ``inputSchema``, and the MCP-only ``annotations`` block is dropped).

Public functions:

- ``count_tokens(text, tokenizer)``: count tokens in an arbitrary string.
- ``serialize_tool(tool, serialization)``: render one tool to the string
  whose tokens model the on-prompt cost.
- ``count_tool(tool, tokenizer, serialization)``: return a per-section
  token breakdown matching the ``tokens`` block in the per-tool report
  shape. ``total`` is the sum of the four sections, per ``SKILL.md`` step 2.

Approximation note (``serialization=anthropic``): the exact wire format
Anthropic uses to render a tool block into a system prompt is not part
of the public API. This mode produces the same JSON the Anthropic Tools
API accepts as input, which is a closer proxy than raw MCP but still an
approximation. Use ``tokenizer=claude`` alongside it to get counts that
are as close to ground truth as a public endpoint allows.

Framing note (``tokenizer=claude``): each ``count_tokens`` call includes
roughly 3-5 framing tokens from the surrounding ``messages`` envelope.
Absolute counts therefore run slightly high in claude mode; relative
ranking across tools is unaffected.
"""

from __future__ import annotations

import functools
import json
import os
from typing import Any

DEFAULT_TOKENIZER = "cl100k_base"
DEFAULT_SERIALIZATION = "raw"
SUPPORTED_TOKENIZERS = ("cl100k_base", "claude")
SUPPORTED_SERIALIZATIONS = ("raw", "anthropic")

_ANTHROPIC_COUNT_URL = "https://api.anthropic.com/v1/messages/count_tokens"
_ANTHROPIC_VERSION = "2023-06-01"
# The count_tokens endpoint requires a model parameter for routing only;
# the actual tokenization is shared across the family.
_DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"


# ----- public API -----------------------------------------------------------


def count_tokens(text: str, tokenizer: str = DEFAULT_TOKENIZER) -> int:
    """Count tokens in a string under the chosen tokenizer.

    Empty strings return 0 without invoking the underlying encoder. The
    claude path is cached per-text for the lifetime of the process.
    """
    if not text:
        return 0
    if tokenizer == "cl100k_base":
        return len(_get_tiktoken_encoder("cl100k_base").encode(text))
    if tokenizer == "claude":
        return _claude_count_cached(text)
    raise ValueError(
        f"Unsupported tokenizer: {tokenizer!r}. Use one of {SUPPORTED_TOKENIZERS}."
    )


def serialize_tool(tool: dict[str, Any], serialization: str = DEFAULT_SERIALIZATION) -> str:
    """Render one tool to the string whose tokens model its on-prompt cost.

    ``raw``: the canonical MCP tool object as compact JSON, including the
    ``annotations`` block.

    ``anthropic``: a compact JSON object using the Anthropic Tools API
    field names (``name``, ``description``, ``input_schema``) and omitting
    ``annotations`` (the API does not render the MCP annotations block).
    See the module docstring for caveats on this being an approximation.
    """
    if serialization == "raw":
        return _compact_json(tool)
    if serialization == "anthropic":
        return _compact_json(_to_anthropic_shape(tool))
    raise ValueError(
        f"Unsupported serialization: {serialization!r}. Use one of {SUPPORTED_SERIALIZATIONS}."
    )


def count_tool(
    tool: dict[str, Any],
    tokenizer: str = DEFAULT_TOKENIZER,
    serialization: str = DEFAULT_SERIALIZATION,
) -> dict[str, int]:
    """Return the per-section token breakdown for one tool.

    Shape matches the ``tokens`` block in the per-tool report:

        {"name": N, "description": N, "schema": N, "annotations": N, "total": N}

    ``total`` is the sum of the four sections per ``SKILL.md`` step 2.
    In ``serialization=anthropic`` the ``annotations`` section is 0 since
    the Anthropic Tools API does not render the MCP annotations block.
    """
    if serialization not in SUPPORTED_SERIALIZATIONS:
        raise ValueError(
            f"Unsupported serialization: {serialization!r}. Use one of {SUPPORTED_SERIALIZATIONS}."
        )

    name_str = tool.get("name") or ""
    description_str = tool.get("description") or ""
    schema_obj = tool.get("inputSchema") or {}
    annotations_obj = tool.get("annotations") or {}

    schema_str = _compact_json(schema_obj) if schema_obj else ""
    if serialization == "anthropic":
        annotations_str = ""  # dropped by the Anthropic Tools API
    else:
        annotations_str = _compact_json(annotations_obj) if annotations_obj else ""

    name_tokens = count_tokens(name_str, tokenizer)
    description_tokens = count_tokens(description_str, tokenizer)
    schema_tokens = count_tokens(schema_str, tokenizer)
    annotations_tokens = count_tokens(annotations_str, tokenizer)

    return {
        "name": name_tokens,
        "description": description_tokens,
        "schema": schema_tokens,
        "annotations": annotations_tokens,
        "total": name_tokens + description_tokens + schema_tokens + annotations_tokens,
    }


# ----- cl100k_base via tiktoken --------------------------------------------


@functools.lru_cache(maxsize=4)
def _get_tiktoken_encoder(encoding_name: str) -> Any:
    """Lazy-import tiktoken and return a cached encoder.

    Tiktoken's first import builds a regex automaton (~1 s on cold disk);
    we cache the encoder so repeated calls in a single CLI run are free.
    """
    try:
        import tiktoken  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "The cl100k_base tokenizer requires `tiktoken`. Install it, or pass --tokenizer claude."
        ) from exc
    return tiktoken.get_encoding(encoding_name)


# ----- claude via /v1/messages/count_tokens --------------------------------


@functools.lru_cache(maxsize=4096)
def _claude_count_cached(text: str) -> int:
    """Cached wrapper around the network call."""
    return _claude_count_uncached(text)


def _claude_count_uncached(text: str) -> int:
    """One HTTP call to Anthropic's count_tokens endpoint.

    Returns ``input_tokens`` from the response. See the framing note in
    the module docstring: this includes the small per-call overhead from
    the surrounding ``messages`` envelope.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "--tokenizer claude requires the ANTHROPIC_API_KEY environment variable. "
            "Set it, or pass --tokenizer cl100k_base (the default)."
        )
    try:
        import requests  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "--tokenizer claude requires the `requests` package."
        ) from exc

    body = {
        "model": _DEFAULT_CLAUDE_MODEL,
        "messages": [{"role": "user", "content": text}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    resp = requests.post(_ANTHROPIC_COUNT_URL, json=body, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    tokens = data.get("input_tokens")
    if not isinstance(tokens, int):
        raise RuntimeError(f"count_tokens response missing `input_tokens`: {data!r}")
    return tokens


# ----- shape helpers --------------------------------------------------------


def _compact_json(obj: Any) -> str:
    """Compact JSON with no extraneous whitespace; UTF-8 lossless."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _to_anthropic_shape(tool: dict[str, Any]) -> dict[str, Any]:
    """Translate the canonical MCP tool to the Anthropic Tools API shape.

    The Anthropic API accepts ``{name, description, input_schema}`` and
    ignores any other top-level fields. We strip ``annotations`` here so
    the count reflects what the API would actually serialize.
    """
    out: dict[str, Any] = {}
    if tool.get("name"):
        out["name"] = tool["name"]
    if tool.get("description"):
        out["description"] = tool["description"]
    schema = tool.get("inputSchema") or {}
    if schema:
        out["input_schema"] = schema
    return out
