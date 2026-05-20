#!/usr/bin/env python3
"""Lint and score the tool definitions exposed by an MCP server (Path B).

This is the bundled CLI entry point. It fetches tool definitions via one
of three input modes (URL, file, inline JSON), applies the deterministic
rubric in ``_rules``, and writes a scorecard (JSON, plus optional
Markdown / HTML). Path A is documented in ``SKILL.md`` and emits the same
JSON shape so ``render_scorecard.py`` renders it without a fetcher
dependency.

Code organization:

- ``_fetchers.py``      input normalization (URL / file / inline JSON).
- ``_tokenizer.py``     tokenizer wrappers and tool serialization.
- ``_rules/``           one module per criterion (15 rules).
- ``_scoring.py``       per-tool scoring + catalog roll-up.
- ``_suggest.py``       template-based rewrite suggestions.
- ``_validation.py``    rubric and CLI argument validation.
- ``render_scorecard.py``  Markdown + HTML renderer (Path A and Path B share).

This module owns the CLI, the rubric loader, expectation gating, and ``main()``.

Exit codes (per ``SKILL.md``):

- ``0``  clean (all tools scored, expectations passed).
- ``1``  MCP fetch error.
- ``2``  input or validation error.
- ``3``  expectation failure (``--expect-max-tokens`` or ``--expect-min-score``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# Make sibling modules importable when invoked as a script (per the other skills' convention).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _fetchers import load_from_file, load_from_json, load_from_url  # noqa: E402
from _scoring import score_catalog  # noqa: E402
from _tokenizer import SUPPORTED_SERIALIZATIONS, SUPPORTED_TOKENIZERS  # noqa: E402
from _validation import (  # noqa: E402
    validate_expect_max_tokens,
    validate_expect_min_score,
    validate_rubric,
    validate_server_url,
)

EXIT_OK = 0
EXIT_FETCH = 1
EXIT_INPUT = 2
EXIT_EXPECTATION = 3


# ----- argument parsing -----------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Path B CLI arguments. Mirrors the flag set in ``SKILL.md``."""
    p = argparse.ArgumentParser(
        prog="lint_mcp_schema.py",
        description="Lint and score the tool definitions exposed by an MCP server.",
    )

    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--server-url", default=None,
                        help="MCP server URL. The linter calls tools/list and scores the response.")
    source.add_argument("--tools-file", default=None,
                        help="Path to a JSON file with a tools/list response, server export, or bare tool array.")
    source.add_argument("--tools-json", default=None,
                        help="Inline JSON string with the same flexibility as --tools-file.")

    p.add_argument("--output-json", default="scorecard.json", help="JSON report path")
    p.add_argument("--output-md", default="scorecard.md", help="Markdown report path")
    p.add_argument("--output-html", default=None, help="Optional self-contained HTML scorecard path")
    p.add_argument("--theme", choices=("auto", "light", "dark"), default="auto", help="HTML theme")

    p.add_argument("--tokenizer", choices=SUPPORTED_TOKENIZERS, default=None,
                   help="Override the rubric's tokenizer choice (default: rubric, falling back to cl100k_base).")
    p.add_argument("--serialization", choices=SUPPORTED_SERIALIZATIONS, default=None,
                   help="Override the rubric's serialization choice (default: rubric, falling back to raw).")

    p.add_argument("--rubric-config", default=None,
                   help="Path to a JSON rubric override. Omitted sections fall back to the builtin defaults.")

    p.add_argument("--expect-max-tokens", type=int, default=None,
                   help="CI gate: exit 3 if catalog total_tokens exceeds N.")
    p.add_argument("--expect-min-score", type=int, default=None,
                   help="CI gate: exit 3 if any tool scores below N.")

    p.add_argument("--timeout", type=float, default=30.0,
                   help="Network timeout in seconds for --server-url mode.")

    return p.parse_args(argv)


# ----- rubric loading -------------------------------------------------------


def load_rubric(config_path: str | None) -> dict[str, Any]:
    """Load the builtin rubric and deep-merge a user override if provided.

    The user override wins per-key; builtin keys fill in any sections the
    user omits, recursively into nested dicts. The merged rubric carries
    a ``_meta`` block with the source path and the SHA-256 of the user
    file (``"builtin"`` / empty when no override was passed); the scoring
    layer surfaces this in the report's ``rubric_config`` block.
    """
    builtin_path = _HERE.parent / "rubric.json"
    builtin = json.loads(builtin_path.read_text(encoding="utf-8"))
    builtin["_meta"] = {"source": "builtin", "sha256": ""}

    if not config_path:
        return builtin

    user_path = Path(config_path).expanduser().resolve()
    if not user_path.is_file():
        raise FileNotFoundError(f"--rubric-config path not found: {user_path}")
    raw = user_path.read_bytes()
    user = json.loads(raw)
    if not isinstance(user, dict):
        raise ValueError("Rubric config file must contain a JSON object at the top level.")
    merged = _deep_merge(builtin, user)
    merged["_meta"] = {
        "source": str(user_path),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    return merged


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge: override's keys win, builtin keys fill in."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ----- catalog fetch dispatch ----------------------------------------------


def fetch_catalog(args: argparse.Namespace) -> dict[str, Any]:
    """Dispatch to the right fetcher based on the --source flag chosen."""
    if args.server_url:
        url = validate_server_url(args.server_url)
        return load_from_url(url, timeout=args.timeout)
    if args.tools_file:
        return load_from_file(args.tools_file)
    return load_from_json(args.tools_json or "")


# ----- expectations ---------------------------------------------------------


def evaluate_expectations(report: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    """Compute expectation results for the catalog.

    Returns an array of ``{name, limit, actual, passed, message[, offenders]}``
    dicts that the CLI writes into ``report["expectations"]``. The CLI then
    exits 3 if any entry's ``passed`` is False.
    """
    out: list[dict[str, Any]] = []

    if args.expect_max_tokens is not None:
        limit = validate_expect_max_tokens(args.expect_max_tokens)
        actual = int(report["catalog"]["total_tokens"])
        passed = actual <= limit
        out.append({
            "name": "expect_max_tokens",
            "limit": limit,
            "actual": actual,
            "passed": passed,
            "message": (
                f"Catalog total {actual} tokens within budget {limit}."
                if passed else
                f"Catalog total {actual} tokens exceeds budget {limit} (over by {actual - limit})."
            ),
        })

    if args.expect_min_score is not None:
        limit = validate_expect_min_score(args.expect_min_score)
        offenders = [t["name"] for t in report["tools"] if int(t["score"]) < limit]
        worst = min((int(t["score"]) for t in report["tools"]), default=100)
        passed = not offenders
        out.append({
            "name": "expect_min_score",
            "limit": limit,
            "actual": worst,
            "passed": passed,
            "offenders": offenders,
            "message": (
                f"All tools meet or exceed {limit}."
                if passed else
                f"{len(offenders)} tool(s) below {limit}: {', '.join(offenders[:5])}"
                + ("..." if len(offenders) > 5 else "")
            ),
        })

    return out


# ----- output writers (renderer hookup is lazy) ----------------------------


def _write_json(path: str, report: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_md(path: str, report: dict[str, Any], theme: str) -> None:
    """Write Markdown via the renderer if it has landed; otherwise stub."""
    try:
        from render_scorecard import make_markdown  # type: ignore[import-not-found]
    except ImportError:
        print(
            f"Note: Markdown renderer not yet wired up; writing placeholder to {path}.",
            file=sys.stderr,
        )
        Path(path).write_text(
            "# MCP Schema Linter (placeholder)\n\nMarkdown renderer is implemented in Step 7.\n",
            encoding="utf-8",
        )
        return
    Path(path).write_text(make_markdown(report, theme=theme), encoding="utf-8")


def _write_html(path: str, report: dict[str, Any], theme: str) -> None:
    """Write HTML via the renderer if it has landed; otherwise stub."""
    try:
        from render_scorecard import make_html  # type: ignore[import-not-found]
    except ImportError:
        print(
            f"Note: HTML renderer not yet wired up; writing placeholder to {path}.",
            file=sys.stderr,
        )
        Path(path).write_text(
            "<!doctype html><meta charset=\"utf-8\"><title>Scorecard placeholder</title>"
            "<body><h1>HTML renderer is implemented in Step 7.</h1></body>",
            encoding="utf-8",
        )
        return
    Path(path).write_text(make_html(report, theme=theme), encoding="utf-8")


# ----- main -----------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Rubric load + validate.
    try:
        rubric = load_rubric(args.rubric_config)
        validate_rubric(rubric)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Error in rubric config: {exc}", file=sys.stderr)
        return EXIT_INPUT

    tokenizer = args.tokenizer or rubric.get("tokenizer") or "cl100k_base"
    serialization = args.serialization or rubric.get("serialization") or "raw"

    # Fetch tools.
    try:
        catalog = fetch_catalog(args)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Error parsing tool list: {exc}", file=sys.stderr)
        return EXIT_INPUT
    except RuntimeError as exc:
        print(f"Fetch failed: {exc}", file=sys.stderr)
        return EXIT_FETCH
    except Exception as exc:  # noqa: BLE001
        print(f"Unexpected fetch error: {exc}", file=sys.stderr)
        return EXIT_FETCH

    if not catalog.get("tools"):
        print("Error: no tools found in the supplied input.", file=sys.stderr)
        return EXIT_INPUT

    # Score + expectations.
    report = score_catalog(catalog, rubric, tokenizer, serialization)
    report["expectations"] = evaluate_expectations(report, args)

    # Write outputs.
    _write_json(args.output_json, report)
    _write_md(args.output_md, report, args.theme)
    if args.output_html:
        _write_html(args.output_html, report, args.theme)

    # One-line stdout summary so CI logs are useful at a glance.
    cs = report["catalog"]
    print(
        f"Scored {cs['tool_count']} tool(s): catalog={cs['catalog_score']}/{cs['catalog_grade']}, "
        f"total_tokens={cs['total_tokens']}, "
        f"measured_savings={cs['addressable_savings_measured']}, "
        f"estimated_savings={cs['addressable_savings_estimated']}"
    )

    # Expectation gate.
    failed = [e for e in report["expectations"] if not e["passed"]]
    if failed:
        for e in failed:
            print(f"FAIL ({e['name']}): {e['message']}", file=sys.stderr)
        return EXIT_EXPECTATION

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
