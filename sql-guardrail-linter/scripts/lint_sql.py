#!/usr/bin/env python3
"""Lint generated SQL against the guardrail control catalog (Path B).

Bundled CLI entry point. Collects statements from one of three sources
(``--sql`` / ``--sql-file`` / ``--usage-log``), parses each structurally,
resolves the conditional controls (partition / tenant / PII / scan ceiling)
from inputs and, in cost_aware / usage_log modes, BigQuery metadata plus a
dry-run, scores each statement, and writes a report (JSON, plus optional
Markdown / HTML). Path A is documented in ``SKILL.md`` and emits the same JSON
shape so ``render_report.py`` renders it without any BigQuery dependency.

Code organization:

- ``_validation.py``  identifier hygiene, byte/duration parsing, rubric checks.
- ``_parser.py``      sqlglot-based structural analysis.
- ``_controls/``      one module per control (12 controls across 3 tiers).
- ``_scoring.py``     per-statement scoring + batch roll-up + top-fixes.
- ``_metadata.py``    BigQuery resolver (lazy import; cost_aware / usage_log).
- ``render_report.py``  Markdown + HTML renderer (Path A and Path B share).

This module owns the CLI, the rubric loader, statement collection, the gate,
and ``main()``.

Exit codes (per SKILL.md):

- ``0``  clean (all statements linted, expectations passed).
- ``1``  BigQuery API error.
- ``2``  input or validation error (including a statement that fails to parse).
- ``3``  expectation failure (a gate flag was set and not met).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _controls import LintContext  # noqa: E402
from _metadata import BigQueryMetadataResolver, NullResolver, build_client  # noqa: E402
from _parser import parse_statement, split_statements  # noqa: E402
from _scoring import build_report, score_statement  # noqa: E402
from _validation import (  # noqa: E402
    parse_bytes,
    parse_duration,
    quote_column,
    quote_table,
    split_table_id,
    validate_column,
    validate_expect_min_score,
    validate_rubric,
    validate_severity,
    severity_rank,
)

EXIT_OK = 0
EXIT_BIGQUERY = 1
EXIT_INPUT = 2
EXIT_EXPECTATION = 3

_DEFAULT_CEILING = 53687091200  # 50 GB


# ----- argument parsing -----------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="lint_sql.py",
        description="Lint generated SQL against a guardrail control catalog.",
    )

    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--sql", default=None, help="A single SQL statement as a string.")
    source.add_argument("--sql-file", default=None, help="Path to a file of statements separated by ';'.")
    source.add_argument("--usage-log", default=None, help="project.dataset.table of logged statements to audit.")

    p.add_argument("--mode", choices=("static", "cost_aware", "usage_log"), default=None,
                   help="Default: usage_log for --usage-log, otherwise static.")
    p.add_argument("--dialect", default="bigquery", help="SQL dialect for the parser (default bigquery).")

    p.add_argument("--max-bytes-ceiling", default=None,
                   help="Scan ceiling for SQ-008 (byte count or human value like 50GB). Default from rubric.")
    p.add_argument("--tenant-registry", default=None, help="Path to a JSON map of table -> tenant column.")
    p.add_argument("--pii-policy", default=None,
                   help="'policy_tags', 'labels', or a path to a JSON map of table -> [restricted columns].")
    p.add_argument("--pii-allowlist", default=None,
                   help="Comma-separated columns/callers permitted to read restricted columns.")

    p.add_argument("--usage-log-sql-column", default=None, help="Column in --usage-log holding the statement text.")
    p.add_argument("--usage-log-time-column", default=None, help="Timestamp column used to bound the window.")
    p.add_argument("--since", default=None, help="Lower time bound for usage_log mode, e.g. 7d, 24h.")
    p.add_argument("--until", default=None, help="Optional upper time bound for usage_log mode, e.g. 1d.")

    p.add_argument("--rubric-config", default=None, help="Path to a JSON rubric override.")

    p.add_argument("--billing-project", default=None, help="Project used by the BigQuery client.")
    p.add_argument("--location", default=None, help="BigQuery location, e.g. US or EU.")

    p.add_argument("--output-json", default="report.json", help="JSON report path.")
    p.add_argument("--output-md", default="report.md", help="Markdown report path.")
    p.add_argument("--output-html", default=None, help="Optional self-contained HTML report path.")
    p.add_argument("--theme", choices=("auto", "light", "dark"), default="auto", help="HTML theme.")

    p.add_argument("--expect-min-score", type=int, default=None,
                   help="CI gate: exit 3 if any statement scores below N.")
    p.add_argument("--expect-no-violations-at", choices=("critical", "high", "medium", "low"), default=None,
                   help="CI gate: exit 3 if any statement has a failure at or above this severity.")
    p.add_argument("--expect-max-bytes", default=None,
                   help="CI gate: exit 3 if any statement's dry-run estimate exceeds this (cost_aware/usage_log only).")

    return p.parse_args(argv)


# ----- rubric loading -------------------------------------------------------


def load_rubric(config_path: Optional[str]) -> dict[str, Any]:
    """Load the builtin rubric and deep-merge a user override if provided."""
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
    merged["_meta"] = {"source": str(user_path), "sha256": hashlib.sha256(raw).hexdigest()}
    return merged


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def rubric_provenance(rubric: dict[str, Any]) -> dict[str, Any]:
    meta = rubric.get("_meta", {}) or {}
    return {
        "source": meta.get("source", "builtin"),
        "name": rubric.get("name", "nl2sql-guardrail-default"),
        "version": rubric.get("version", "1.0"),
        "sha256": meta.get("sha256", ""),
    }


# ----- input loading --------------------------------------------------------


def load_tenant_registry(path: Optional[str]) -> dict[str, str]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("--tenant-registry must be a JSON object of table -> column.")
    out: dict[str, str] = {}
    for table_id, column in data.items():
        split_table_id(table_id)  # validates the key
        out[table_id] = validate_column(str(column))
    return out


def load_pii_policy(value: Optional[str]) -> Any:
    if value is None:
        return None
    if value in ("policy_tags", "labels"):
        return value
    path = Path(value).expanduser()
    if not path.is_file():
        raise ValueError(
            "--pii-policy must be 'policy_tags', 'labels', or a path to a JSON map file."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("--pii-policy JSON map must be an object of table -> [columns].")
    out: dict[str, list[str]] = {}
    for table_id, cols in data.items():
        split_table_id(table_id)
        if not isinstance(cols, list):
            raise ValueError(f"--pii-policy entry for {table_id!r} must be a list of columns.")
        out[table_id] = [validate_column(str(c)) for c in cols]
    return out


def parse_allowlist(value: Optional[str]) -> set[str]:
    if not value:
        return set()
    return {item.strip().lower() for item in value.split(",") if item.strip()}


# ----- statement collection -------------------------------------------------


def collect_statements(args: argparse.Namespace, resolver: Any, ceiling: int) -> list[tuple[str, str]]:
    """Return ``[(raw_sql, source_label), ...]`` for the chosen source."""
    if args.sql is not None:
        return [(args.sql, "inline sql")]
    if args.sql_file is not None:
        path = Path(args.sql_file)
        if not path.is_file():
            raise FileNotFoundError(f"--sql-file not found: {path}")
        segments = split_statements(path.read_text(encoding="utf-8"))
        if not segments:
            raise ValueError("--sql-file contained no statements.")
        base = path.name
        return [(seg, f"{base} #{i + 1}") for i, seg in enumerate(segments)]
    # usage_log
    return fetch_usage_log(args, resolver, ceiling)


def fetch_usage_log(args: argparse.Namespace, resolver: Any, ceiling: int) -> list[tuple[str, str]]:
    if not args.usage_log_sql_column or not args.usage_log_time_column:
        raise ValueError("usage_log mode requires --usage-log-sql-column and --usage-log-time-column.")
    if not args.since:
        raise ValueError("usage_log mode requires --since (e.g. 7d).")

    table_id = args.usage_log
    split_table_id(table_id)
    sql_col = validate_column(args.usage_log_sql_column)
    time_col = validate_column(args.usage_log_time_column)
    n, unit, _ = parse_duration(args.since)

    clauses = [f"{quote_column(time_col)} >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {n} {unit})"]
    if args.until:
        m, unit2, _ = parse_duration(args.until)
        clauses.append(f"{quote_column(time_col)} <= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {m} {unit2})")
    where = " AND ".join(clauses)
    sql = (
        f"SELECT {quote_column(sql_col)} AS sql_text, {quote_column(time_col)} AS logged_at "
        f"FROM {quote_table(table_id)} WHERE {where}"
    )

    from google.cloud import bigquery  # lazy import

    client = resolver._client
    dry = client.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False))
    est = int(dry.total_bytes_processed or 0)
    if est > ceiling:
        raise ValueError(
            f"usage-log fetch would scan {est} bytes, over the ceiling {ceiling}. "
            "Narrow --since or raise --max-bytes-ceiling."
        )
    job = client.query(sql, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=ceiling))
    rows = list(job.result())
    out: list[tuple[str, str]] = []
    for row in rows:
        text = row["sql_text"]
        if text and str(text).strip():
            out.append((str(text), f"{table_id} @ {row['logged_at']}"))
    return out


# ----- per-statement linting ------------------------------------------------


def lint_statements(
    raw_statements: list[tuple[str, str]],
    *,
    rubric: dict[str, Any],
    mode: str,
    ceiling: int,
    tenant_registry: dict[str, str],
    pii_policy: Any,
    pii_allowlist: set[str],
    resolver: Any,
    dialect: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Parse, contextualize, and score each statement. Returns (cards, any_parse_error)."""
    cards: list[dict[str, Any]] = []
    parse_failed = False
    cost_aware = mode in ("cost_aware", "usage_log")

    for index, (raw, source) in enumerate(raw_statements, start=1):
        stmt = parse_statement(raw, dialect=dialect)
        if not stmt.ok:
            parse_failed = True

        table_metadata: dict[str, dict[str, Any]] = {}
        estimated_bytes: Optional[int] = None
        if cost_aware:
            for tbl in stmt.tables:
                table_metadata[tbl.written.lower()] = resolver.table_meta(tbl.written)
            if stmt.ok and stmt.is_read_only_select:
                estimated_bytes = resolver.dry_run(raw)

        ctx = LintContext(
            mode=mode,
            max_bytes_ceiling=ceiling,
            tenant_registry=tenant_registry,
            pii_policy=pii_policy,
            pii_allowlist=pii_allowlist,
            table_metadata=table_metadata,
            estimated_bytes=estimated_bytes,
            bytes_available=cost_aware and estimated_bytes is not None,
        )
        statement_id = f"stmt_{index:04d}"
        cards.append(score_statement(stmt, ctx, rubric, statement_id=statement_id, source=source))

    return cards, parse_failed


# ----- expectations ---------------------------------------------------------


def evaluate_expectations(report: dict[str, Any], args: argparse.Namespace, mode: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    statements = report.get("statements", [])

    if args.expect_min_score is not None:
        limit = validate_expect_min_score(args.expect_min_score)
        offenders = [s["statement_id"] for s in statements if int(s["score"]) < limit]
        worst = min((int(s["score"]) for s in statements), default=100)
        passed = not offenders
        out.append({
            "name": "expect_min_score",
            "limit": limit,
            "actual": worst,
            "passed": passed,
            "offenders": offenders,
            "message": (
                f"All statements meet or exceed {limit}."
                if passed else
                f"{len(offenders)} statement(s) below {limit}: {', '.join(offenders[:5])}"
                + ("..." if len(offenders) > 5 else "")
            ),
        })

    if args.expect_no_violations_at is not None:
        sev = validate_severity(args.expect_no_violations_at)
        threshold = severity_rank(sev)
        offenders = [s["statement_id"] for s in statements if _max_violation_rank(s) >= threshold]
        passed = not offenders
        out.append({
            "name": "expect_no_violations_at",
            "limit": sev,
            "actual": len(offenders),
            "passed": passed,
            "offenders": offenders,
            "message": (
                f"No statement has a violation at or above {sev}."
                if passed else
                f"{len(offenders)} statement(s) violate at or above {sev}: {', '.join(offenders[:5])}"
                + ("..." if len(offenders) > 5 else "")
            ),
        })

    if args.expect_max_bytes is not None:
        limit = parse_bytes(args.expect_max_bytes)
        offenders = [
            s["statement_id"] for s in statements
            if s.get("estimated_bytes") is not None and int(s["estimated_bytes"]) > limit
        ]
        worst = max((int(s["estimated_bytes"]) for s in statements if s.get("estimated_bytes") is not None), default=0)
        passed = not offenders
        out.append({
            "name": "expect_max_bytes",
            "limit": limit,
            "actual": worst,
            "passed": passed,
            "offenders": offenders,
            "message": (
                f"All statements scan within {limit} bytes."
                if passed else
                f"{len(offenders)} statement(s) exceed {limit} bytes: {', '.join(offenders[:5])}"
                + ("..." if len(offenders) > 5 else "")
            ),
        })

    return out


def _max_violation_rank(statement: dict[str, Any]) -> int:
    rank = 0
    for sev, count in (statement.get("violations") or {}).items():
        if count:
            rank = max(rank, severity_rank(sev))
    return rank


# ----- output writers -------------------------------------------------------


def _write_json(path: str, report: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_md(path: str, report: dict[str, Any], theme: str) -> None:
    from render_report import make_markdown
    Path(path).write_text(make_markdown(report, theme=theme), encoding="utf-8")


def _write_html(path: str, report: dict[str, Any], theme: str) -> None:
    from render_report import make_html
    Path(path).write_text(make_html(report, theme=theme), encoding="utf-8")


# ----- main -----------------------------------------------------------------


def _resolve_mode(args: argparse.Namespace) -> str:
    if args.mode:
        return args.mode
    if args.usage_log is not None:
        return "usage_log"
    return "static"


def main(argv: Optional[list[str]] = None) -> int:
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

    mode = _resolve_mode(args)
    if args.usage_log is not None and mode != "usage_log":
        print("Error: --usage-log requires --mode usage_log.", file=sys.stderr)
        return EXIT_INPUT

    # Resolve inputs.
    try:
        ceiling = parse_bytes(args.max_bytes_ceiling) if args.max_bytes_ceiling is not None \
            else int(rubric.get("max_bytes_ceiling", _DEFAULT_CEILING))
        tenant_registry = load_tenant_registry(args.tenant_registry)
        pii_policy = load_pii_policy(args.pii_policy)
        pii_allowlist = parse_allowlist(args.pii_allowlist)
        if args.expect_max_bytes is not None:
            parse_bytes(args.expect_max_bytes)  # validate early
            if mode == "static":
                print("Error: --expect-max-bytes requires cost_aware or usage_log mode.", file=sys.stderr)
                return EXIT_INPUT
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INPUT

    # Build the metadata resolver.
    if mode in ("cost_aware", "usage_log"):
        try:
            client = build_client(args.billing_project, args.location)
        except ImportError:
            print("Error: cost_aware / usage_log modes need google-cloud-bigquery installed.", file=sys.stderr)
            return EXIT_INPUT
        except Exception as exc:  # noqa: BLE001  auth / project errors
            print(f"BigQuery error: {exc}", file=sys.stderr)
            return EXIT_BIGQUERY
        resolver: Any = BigQueryMetadataResolver(
            client, pii_policy=pii_policy, pii_patterns=rubric.get("pii_column_patterns"),
        )
    else:
        resolver = NullResolver()

    # Collect statements.
    try:
        raw_statements = collect_statements(args, resolver, ceiling)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    except Exception as exc:  # noqa: BLE001  BigQuery fetch failure in usage_log mode
        print(f"BigQuery error while reading usage log: {exc}", file=sys.stderr)
        return EXIT_BIGQUERY

    if not raw_statements:
        print("Error: no statements to lint.", file=sys.stderr)
        return EXIT_INPUT

    # Lint + score.
    cards, parse_failed = lint_statements(
        raw_statements,
        rubric=rubric,
        mode=mode,
        ceiling=ceiling,
        tenant_registry=tenant_registry,
        pii_policy=pii_policy,
        pii_allowlist=pii_allowlist,
        resolver=resolver,
        dialect=args.dialect,
    )

    gate_severity = args.expect_no_violations_at or "high"
    scope = _build_scope(args, mode, raw_statements)
    warnings = list(getattr(resolver, "warnings", []) or [])

    report = build_report(
        statements=cards,
        rubric=rubric,
        mode=mode,
        scope=scope,
        gate_severity=gate_severity,
        linted_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        rubric_config=rubric_provenance(rubric),
        warnings=warnings,
    )
    report["expectations"] = evaluate_expectations(report, args, mode)

    # Write outputs.
    _write_json(args.output_json, report)
    _write_md(args.output_md, report, args.theme)
    if args.output_html:
        _write_html(args.output_html, report, args.theme)

    # One-line stdout summary.
    summary = report["summary"]
    print(
        f"Linted {summary['statement_count']} statement(s): "
        f"worst_score={summary['worst_score']}, "
        f"with_violations_at_{summary['gate_severity']}={summary['statements_with_violations']}, "
        f"total_bytes={summary['total_estimated_bytes'] if summary['total_estimated_bytes'] is not None else 'n/a'}"
    )

    # Exit-code precedence: parse/input error (2) over expectation failure (3).
    if parse_failed:
        print("FAIL: at least one statement failed to parse (see report).", file=sys.stderr)
        return EXIT_INPUT

    failed = [e for e in report["expectations"] if not e["passed"]]
    if failed:
        for e in failed:
            print(f"FAIL ({e['name']}): {e['message']}", file=sys.stderr)
        return EXIT_EXPECTATION

    return EXIT_OK


def _build_scope(args: argparse.Namespace, mode: str, raw_statements: list[tuple[str, str]]) -> dict[str, Any]:
    if args.sql is not None:
        source = "sql"
        table = None
    elif args.sql_file is not None:
        source = "sql_file"
        table = args.sql_file
    else:
        source = "usage_log"
        table = args.usage_log
    scope: dict[str, Any] = {"source": source, "statement_count": len(raw_statements)}
    if table:
        scope["table"] = table
    if mode == "usage_log" and args.since:
        scope["window"] = args.since
    return scope


if __name__ == "__main__":
    sys.exit(main())
