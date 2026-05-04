"""CI-style expectation evaluation and schema-drift detection.

Pure post-hoc analysis: reads from a populated report dict and produces a
list of pass/fail/skip entries. Never triggers new queries.
"""

from __future__ import annotations

import argparse
import datetime as dt
from typing import Any

from _validation import parse_duration, parse_null_rate_arg


def schema_drift(current: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> dict[str, Any]:
    cur = {f.get("name"): f for f in current if f.get("name")}
    base = {f.get("name"): f for f in baseline if f.get("name")}
    added = sorted(set(cur) - set(base))
    removed = sorted(set(base) - set(cur))
    changed: list[dict[str, Any]] = []
    for name in sorted(set(cur) & set(base)):
        c, b = cur[name], base[name]
        diffs: dict[str, Any] = {}
        if c.get("type") != b.get("type"):
            diffs["type"] = {"baseline": b.get("type"), "current": c.get("type")}
        if c.get("mode") != b.get("mode"):
            diffs["mode"] = {"baseline": b.get("mode"), "current": c.get("mode")}
        if diffs:
            changed.append({"name": name, **diffs})
    return {"added": added, "removed": removed, "changed": changed}


def _completed_check_first_row(checks: dict[str, Any], name: str) -> dict[str, Any] | None:
    check = checks.get(name)
    if not check or check.get("status") != "complete":
        return None
    rows = check.get("rows") or []
    return rows[0] if rows else None


def _parse_max_value(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    text = str(value)
    for candidate in (text, text.replace("Z", "+00:00"), f"{text}T00:00:00"):
        try:
            return dt.datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def evaluate_expectations(report: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    """Compare a populated report against the user's expectation flags.

    Returns one entry per expectation with ``status`` in {passed, failed,
    skipped_no_data, error}. ``skipped_no_data`` does not fail the run; only
    ``failed`` does.
    """
    results: list[dict[str, Any]] = []
    metadata = report.get("metadata", {})
    checks = report.get("checks", {})

    if args.expect_min_rows is not None:
        num_rows = metadata.get("num_rows")
        entry = {"name": "min_rows", "expected_min": args.expect_min_rows, "actual": num_rows}
        if num_rows is None:
            entry["status"] = "skipped_no_data"
            entry["reason"] = "metadata num_rows unavailable (external table?)"
        else:
            entry["status"] = "passed" if int(num_rows) >= args.expect_min_rows else "failed"
        results.append(entry)

    if args.expect_zero_duplicates:
        first = _completed_check_first_row(checks, "duplicate_keys")
        entry: dict[str, Any] = {"name": "zero_duplicates"}
        if first is None:
            entry["status"] = "skipped_no_data"
            entry["reason"] = "duplicate_keys check did not complete"
        else:
            excess = int(first.get("duplicate_excess_rows") or 0)
            entry["duplicate_excess_rows"] = excess
            entry["status"] = "passed" if excess == 0 else "failed"
        results.append(entry)

    if args.expect_freshness_within:
        entry = {"name": "freshness_within", "max_age": args.expect_freshness_within}
        try:
            max_age = parse_duration(args.expect_freshness_within)
        except ValueError as exc:
            entry["status"] = "error"
            entry["error"] = str(exc)
            results.append(entry)
        else:
            entry["max_age_seconds"] = int(max_age.total_seconds())
            first = _completed_check_first_row(checks, "freshness")
            if first is None:
                entry["status"] = "skipped_no_data"
                entry["reason"] = "freshness check did not complete"
            else:
                max_value = first.get("max_value")
                entry["max_value"] = None if max_value is None else str(max_value)
                if max_value is None:
                    entry["status"] = "failed"
                    entry["reason"] = "MAX(freshness_col) is NULL (table empty or column entirely NULL)"
                else:
                    parsed = _parse_max_value(max_value)
                    if parsed is None:
                        entry["status"] = "skipped_no_data"
                        entry["reason"] = f"could not parse MAX value {max_value!r}"
                    else:
                        if parsed.tzinfo:
                            now = dt.datetime.now(parsed.tzinfo)
                        else:
                            now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                        age = now - parsed
                        entry["age_seconds"] = int(age.total_seconds())
                        entry["status"] = "passed" if age <= max_age else "failed"
            results.append(entry)

    if args.expect_no_schema_drift:
        entry = {"name": "no_schema_drift"}
        drift = report.get("schema_drift")
        if not drift or "error" in drift:
            entry["status"] = "skipped_no_data"
            entry["reason"] = drift.get("error") if drift else "--baseline not provided"
        else:
            added = drift.get("added") or []
            removed = drift.get("removed") or []
            changed = drift.get("changed") or []
            entry["added"] = len(added)
            entry["removed"] = len(removed)
            entry["changed"] = len(changed)
            entry["status"] = "passed" if not (added or removed or changed) else "failed"
        results.append(entry)

    for spec in args.expect_max_null_rate or []:
        entry = {"name": "max_null_rate", "spec": spec}
        try:
            col, max_rate = parse_null_rate_arg(spec)
        except ValueError as exc:
            entry["status"] = "error"
            entry["error"] = str(exc)
            results.append(entry)
            continue
        entry["column"] = col
        entry["max_null_rate"] = max_rate
        first = _completed_check_first_row(checks, "column_profile")
        if first is None:
            entry["status"] = "skipped_no_data"
            entry["reason"] = "column_profile check did not complete"
            results.append(entry)
            continue
        nulls = first.get(f"{col}__null_count")
        scanned = first.get("scanned_rows")
        if nulls is None or not scanned:
            entry["status"] = "skipped_no_data"
            entry["reason"] = f"column {col!r} not in profiled columns"
            results.append(entry)
            continue
        rate = int(nulls) / int(scanned)
        entry["actual_null_rate"] = rate
        entry["null_count"] = int(nulls)
        entry["scanned_rows"] = int(scanned)
        entry["status"] = "passed" if rate <= max_rate else "failed"
        results.append(entry)

    return results
