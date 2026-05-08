"""Heuristic metadata-quality rubric.

Scores a table's authored metadata against the data-steward rubric:

- 8 table-level criteria (max 16 pts), each scored 0/1/2.
- 6 column-level criteria (max varies per column — only criteria that apply
  to the column's name pattern are counted).

Combined score: ``weights.table * (table_pts / 16) + weights.column * column_mean_normalized``,
scaled to 0-100. Letter grade A/B/C/D/F.

Pure Python: no third-party dependencies. Same logic powers the local
script (Path B) and is referenced by SKILL.md so the agent's semantic
grading (Path A) produces the same JSON shape.

The rubric *data* (keyword lists, regex triggers, weights, grade cutoffs,
thresholds) lives in a ``RubricConfig`` and can be replaced at runtime via
``load_rubric_config()`` + ``--rubric-config`` on the CLI. Check *logic*
(what counts as full credit vs. partial vs. fail) stays in code. The
built-in ``DEFAULT_CONFIG`` reproduces the historical scoring exactly.

Inputs are the normalized dict shape:

    {
        "table_id": "project.dataset.table",
        "description": str | None,
        "labels": {str: str},
        "columns": [
            {"name": str, "type": str, "mode": str,
             "description": str | None, "policy_tags": [str]}
        ],
    }
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import Any

RUBRIC_VERSION = "1.0"


# ---------------------------------------------------------------------------
# RubricConfig
#
# A frozen-by-convention container holding every piece of data the checks
# consult. Loadable from JSON via ``load_rubric_config``. Construct via
# ``DEFAULT_CONFIG`` for the built-in rubric, or pass a custom instance to
# ``score_table`` to override.
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class RubricConfig:
    """Configurable surface of the rubric.

    ``weights`` must contain ``table`` and ``column`` keys summing to ~1.0.
    ``grade_cutoffs`` must contain ``A``, ``B``, ``C``, ``D`` keys with
    monotonically decreasing integer cutoffs in [0, 100].
    ``thresholds`` must contain ``table_desc_min``, ``column_desc_min_partial``,
    ``column_desc_min_full``, ``evidence_max_chars`` (positive ints).
    ``column_triggers`` must contain ``coded``, ``measure``, ``sensitive``
    keys mapping to compiled ``re.Pattern`` objects.
    ``keywords`` is a dict keyed by criterion name; each value is a dict
    with optional ``strong`` / ``weak`` / ``label`` tuples of substrings.
    """
    name: str
    version: str
    weights: dict[str, float]
    grade_cutoffs: dict[str, int]
    thresholds: dict[str, int]
    column_triggers: dict[str, re.Pattern[str]]
    keywords: dict[str, dict[str, tuple[str, ...]]]
    type_echo_patterns: tuple[str, ...]
    generic_table_desc_re: re.Pattern[str]


# Built-in rubric data. These constants reproduce the historical scoring
# exactly so existing reports and tests are unaffected when no custom
# config is provided.

_DEFAULT_KEYWORDS: dict[str, dict[str, tuple[str, ...]]] = {
    # Table-level
    "grain_statement": {
        "strong": ("grain", "one row per", "1 row per", "row represents", "one record per", "one row for each"),
    },
    "primary_keys": {
        "strong": ("primary key", "unique key", "composite key", "business key", "surrogate key", "uniquely identif"),
        "weak":   (" key ", "key:", " keys ", "keys:"),
    },
    "join_guidance": {
        "strong": ("join to ", "joins to ", "joined to ", "foreign key", " fk ", "join on ", "join using ", "join via "),
        "weak":   ("join", "joining"),
    },
    "ownership": {
        "label":  ("owner", "steward", "team", "domain", "contact"),
        "strong": ("owner:", "steward:", "team:", "contact:", "owned by", "stewarded by"),
        "weak":   ("owner", "steward"),
    },
    "sensitivity": {
        "label":  ("pii", "phi", "sensitive", "sensitivity", "classification", "data_classification"),
        "strong": ("contains phi", "contains pii", "phi data", "pii data", "restricted", "confidential",
                   "sensitivity:", "classification:"),
        "weak":   ("phi", "pii", "sensitive", "non-sensitive", "public data"),
    },
    "history_rule": {
        "strong": ("current state", "current-state", "as of", "as_of", "snapshot", "scd", "type 2",
                   "type-2", "point in time", "point-in-time", "latest_record", "history", "version",
                   "current versus history", "versioned"),
    },
    "lineage": {
        "label":  ("source", "upstream", "pipeline", "system_of_record"),
        "strong": ("source system", "derived from", "upstream", "produced by", "ingested from",
                   "loaded from", "etl from", "system of record", "pipeline:"),
        "weak":   ("source",),
    },
    # Column-level
    "coded_field_explained": {
        "strong": ("value", "values", "code", "codes", "code system", "enum", "category", "categories",
                   "y/n", "yes/no", "0/1", "true/false", "valid values", "allowed values", "code set",
                   "icd", "snomed", "loinc", "cpt", "hcpcs", "rxnorm", "ndc"),
    },
    "units_or_format": {
        "strong": (
            "utc", "iso 8601", "iso8601", "in dollars", "usd", "eur", "gbp",
            "celsius", "fahrenheit", "kelvin",
            "milliseconds", "seconds", "minutes", "hours", "days", "ms", "ns",
            "kg", " lb", "pounds", "kilograms", "grams", "meters", "cm",
            "percent", "%", "per ", "rate per", "fraction",
            "format:", "format ", "formatted as", "yyyy", "mm-dd", "epoch", "unix time",
            "timezone",
        ),
    },
    "sensitivity_flagged": {
        "strong": ("phi", "pii", "sensitive", "restricted", "confidential",
                   "personally identifiable", "protected health", "do not export",
                   "policy:", "classification:"),
    },
    "caveats_present": {
        "strong": (
            "deprecated", "legacy", "do not use", "do not rely", "not enforced", "by design",
            "be aware", "note:", "caution:", "warning:", "warn:", "gotcha", "trap:",
            "null when", "null if", "may be null", "may contain", "can be null",
            "overloaded", "multiple meanings", "ambiguous", "reserved",
            "to be removed", "will be removed", "duplicates exist", "duplicates may",
            "not unique", "non-unique", "not guaranteed", "should not be",
        ),
    },
    "derived_or_source_status": {
        "strong": (
            "derived from", "derived as", "calculated as", "calculated from", "computed from",
            "computed as", "transformed from", "parsed from", "extracted from", "aggregated from",
            "raw from", "from the source", "source system", "source-native", "source field",
            "system of record", "source of truth", "ingested from", "loaded from", "etl from",
            "from upstream", "lineage:", "provenance:", "source:",
            "foreign key to", "fk to ", " fk ", "reference to", "references ", "lookup against",
            "join key to", "join to ",
            "generated by", "auto-generated", "auto generated", "uuid", "surrogate key",
            "natural key from",
        ),
    },
}

_DEFAULT_TYPE_ECHO_PATTERNS: tuple[str, ...] = (
    r"^(string|str|int|integer|int64|float|float64|number|numeric|date|datetime|timestamp|"
    r"boolean|bool|bytes|json|geography)\s*(field|column|value|type)?$",
    r"^the\s+\w+(\s+(field|column))?$",
    r"^\w+\s+(field|column)$",
)


DEFAULT_CONFIG: RubricConfig = RubricConfig(
    name="data-steward-default",
    version="1.0",
    weights={"table": 0.4, "column": 0.6},
    grade_cutoffs={"A": 90, "B": 80, "C": 70, "D": 60},
    thresholds={
        "table_desc_min": 30,
        "column_desc_min_partial": 8,
        "column_desc_min_full": 15,
        "evidence_max_chars": 90,
    },
    column_triggers={
        "coded":     re.compile(r"_(code|status|flag|type|cd|ind|category)$", re.IGNORECASE),
        "measure":   re.compile(
            r"(^|_)(amount|count|rate|pct|percent|temp|dose|qty|quantity|weight|height|"
            r"length|date|datetime|timestamp|duration|elapsed|seconds|minutes|hours|days|price|cost)(_|$)",
            re.IGNORECASE,
        ),
        "sensitive": re.compile(
            r"(^|_)(ssn|email|dob|date_of_birth|phone|mrn|patient_id|address|zip|postal|"
            r"first_name|last_name|full_name|account|credit_card|card_number|tax_id)(_|$)",
            re.IGNORECASE,
        ),
    },
    keywords=_DEFAULT_KEYWORDS,
    type_echo_patterns=_DEFAULT_TYPE_ECHO_PATTERNS,
    generic_table_desc_re=re.compile(
        r"^(table|view|dataset|extract|export)?\s*(from|in|for)?\s*[A-Za-z0-9_\-\s]{0,40}\.?$",
        re.IGNORECASE,
    ),
)


# ---------------------------------------------------------------------------
# Config loading and validation
# ---------------------------------------------------------------------------

_REQUIRED_TRIGGERS = ("coded", "measure", "sensitive")
_REQUIRED_GRADES = ("A", "B", "C", "D")
_REQUIRED_THRESHOLDS = (
    "table_desc_min", "column_desc_min_partial", "column_desc_min_full", "evidence_max_chars",
)
_ALLOWED_TOP_KEYS = {
    "name", "version", "weights", "grade_cutoffs", "thresholds",
    "column_triggers", "keywords", "type_echo_patterns", "generic_table_desc_re",
}
_ALLOWED_KEYWORD_BUCKETS = {"strong", "weak", "label"}


def _tuple_of_str(value: Any, ctx: str) -> tuple[str, ...]:
    """Coerce a JSON list of strings to a tuple, with a useful error context."""
    if not isinstance(value, list):
        raise ValueError(f"{ctx}: expected a list of strings, got {type(value).__name__}")
    out: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{ctx}[{i}]: expected string, got {type(item).__name__}")
        out.append(item)
    return tuple(out)


def _compile_re(pattern: str, ctx: str) -> re.Pattern[str]:
    """Compile a user-supplied regex; surface a friendly error on failure."""
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"{ctx}: invalid regex {pattern!r}: {exc}") from exc


def _merge_keywords(
    user: dict[str, Any] | None,
    default: dict[str, dict[str, tuple[str, ...]]],
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Merge a partial user keywords block with the defaults.

    Per criterion, user-provided buckets (``strong``/``weak``/``label``)
    replace the defaults; unspecified buckets fall through to the default.
    Unknown criterion names or buckets raise to surface typos.
    """
    if user is None:
        return {k: dict(v) for k, v in default.items()}
    if not isinstance(user, dict):
        raise ValueError(f"keywords: expected an object, got {type(user).__name__}")

    merged: dict[str, dict[str, tuple[str, ...]]] = {k: dict(v) for k, v in default.items()}
    for crit_name, buckets in user.items():
        if crit_name not in default:
            raise ValueError(
                f"keywords.{crit_name}: unknown criterion; "
                f"valid names: {sorted(default)}"
            )
        if not isinstance(buckets, dict):
            raise ValueError(f"keywords.{crit_name}: expected an object")
        for bucket_name, items in buckets.items():
            if bucket_name not in _ALLOWED_KEYWORD_BUCKETS:
                raise ValueError(
                    f"keywords.{crit_name}.{bucket_name}: unknown bucket; "
                    f"valid names: {sorted(_ALLOWED_KEYWORD_BUCKETS)}"
                )
            merged[crit_name][bucket_name] = _tuple_of_str(
                items, f"keywords.{crit_name}.{bucket_name}"
            )
    return merged


def load_rubric_config(path: str | Path) -> RubricConfig:
    """Load a rubric config from a JSON file.

    Any section not present in the file falls back to ``DEFAULT_CONFIG``,
    so users can ship a partial override (e.g., only custom weights).
    Unknown top-level keys, unknown criterion names, malformed regexes,
    and out-of-range numeric values raise ``ValueError``.
    """
    raw_path = Path(path)
    raw_text = raw_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{raw_path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{raw_path}: top-level value must be an object")

    extra = set(data) - _ALLOWED_TOP_KEYS
    if extra:
        raise ValueError(
            f"{raw_path}: unknown top-level keys {sorted(extra)}; "
            f"allowed: {sorted(_ALLOWED_TOP_KEYS)}"
        )

    name = data.get("name", DEFAULT_CONFIG.name)
    version = data.get("version", DEFAULT_CONFIG.version)
    if not isinstance(name, str) or not isinstance(version, str):
        raise ValueError(f"{raw_path}: name/version must be strings")

    weights_in = data.get("weights")
    if weights_in is None:
        weights = dict(DEFAULT_CONFIG.weights)
    else:
        if not isinstance(weights_in, dict):
            raise ValueError("weights: expected an object")
        weights = {**DEFAULT_CONFIG.weights, **weights_in}
        for key in ("table", "column"):
            if not isinstance(weights.get(key), (int, float)):
                raise ValueError(f"weights.{key}: expected a number")
            if weights[key] < 0:
                raise ValueError(f"weights.{key}: must be non-negative")
        total = weights["table"] + weights["column"]
        if not (0.99 <= total <= 1.01):
            raise ValueError(f"weights.table + weights.column must sum to ~1.0 (got {total})")

    grade_in = data.get("grade_cutoffs")
    if grade_in is None:
        grade_cutoffs = dict(DEFAULT_CONFIG.grade_cutoffs)
    else:
        if not isinstance(grade_in, dict):
            raise ValueError("grade_cutoffs: expected an object")
        grade_cutoffs = {**DEFAULT_CONFIG.grade_cutoffs, **grade_in}
        last = 101
        for letter in _REQUIRED_GRADES:
            cutoff = grade_cutoffs.get(letter)
            if not isinstance(cutoff, int):
                raise ValueError(f"grade_cutoffs.{letter}: expected an integer")
            if not 0 <= cutoff <= 100:
                raise ValueError(f"grade_cutoffs.{letter}: must be in [0, 100]")
            if cutoff >= last:
                raise ValueError(
                    f"grade_cutoffs.{letter} ({cutoff}) must be strictly less than the previous cutoff"
                )
            last = cutoff

    thresholds_in = data.get("thresholds")
    if thresholds_in is None:
        thresholds = dict(DEFAULT_CONFIG.thresholds)
    else:
        if not isinstance(thresholds_in, dict):
            raise ValueError("thresholds: expected an object")
        thresholds = {**DEFAULT_CONFIG.thresholds, **thresholds_in}
        for key in _REQUIRED_THRESHOLDS:
            val = thresholds.get(key)
            if not isinstance(val, int) or val < 0:
                raise ValueError(f"thresholds.{key}: expected a non-negative integer")

    triggers_in = data.get("column_triggers")
    if triggers_in is None:
        column_triggers = dict(DEFAULT_CONFIG.column_triggers)
    else:
        if not isinstance(triggers_in, dict):
            raise ValueError("column_triggers: expected an object")
        column_triggers = dict(DEFAULT_CONFIG.column_triggers)
        for key in triggers_in:
            if key not in _REQUIRED_TRIGGERS:
                raise ValueError(
                    f"column_triggers.{key}: unknown trigger; "
                    f"valid: {sorted(_REQUIRED_TRIGGERS)}"
                )
        for key in _REQUIRED_TRIGGERS:
            if key in triggers_in:
                pat = triggers_in[key]
                if not isinstance(pat, str):
                    raise ValueError(f"column_triggers.{key}: expected a regex string")
                column_triggers[key] = _compile_re(pat, f"column_triggers.{key}")

    keywords = _merge_keywords(data.get("keywords"), DEFAULT_CONFIG.keywords)

    if "type_echo_patterns" in data:
        type_echo_patterns = _tuple_of_str(data["type_echo_patterns"], "type_echo_patterns")
        for i, pat in enumerate(type_echo_patterns):
            _compile_re(pat, f"type_echo_patterns[{i}]")  # validate compiles
    else:
        type_echo_patterns = DEFAULT_CONFIG.type_echo_patterns

    if "generic_table_desc_re" in data:
        gen_pat = data["generic_table_desc_re"]
        if not isinstance(gen_pat, str):
            raise ValueError("generic_table_desc_re: expected a regex string")
        generic_table_desc_re = _compile_re(gen_pat, "generic_table_desc_re")
    else:
        generic_table_desc_re = DEFAULT_CONFIG.generic_table_desc_re

    return RubricConfig(
        name=name,
        version=version,
        weights=weights,
        grade_cutoffs=grade_cutoffs,
        thresholds=thresholds,
        column_triggers=column_triggers,
        keywords=keywords,
        type_echo_patterns=type_echo_patterns,
        generic_table_desc_re=generic_table_desc_re,
    )


def rubric_config_metadata(config: RubricConfig, source_path: str | Path | None) -> dict[str, Any]:
    """Build the ``rubric_config`` metadata block stamped into report output.

    ``source`` is ``"builtin"`` when no path was supplied, otherwise the
    absolute file path. ``sha256`` is over the raw file bytes when loaded
    from disk, or empty for the built-in.
    """
    if source_path is None:
        return {
            "source": "builtin",
            "name": config.name,
            "version": config.version,
            "sha256": "",
        }
    p = Path(source_path)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    return {
        "source": str(p),
        "name": config.name,
        "version": config.version,
        "sha256": digest,
    }


# ---------------------------------------------------------------------------
# Name-pattern detectors (thin wrappers over the config's column triggers).
# Kept so existing callers / tests can ask "is this a coded column name?"
# without constructing a full check.
# ---------------------------------------------------------------------------

def _is_coded_name(name: str, cfg: RubricConfig | None = None) -> bool:
    """True if the column name matches the config's ``coded`` trigger."""
    return bool((cfg or DEFAULT_CONFIG).column_triggers["coded"].search(name))


def _is_measure_name(name: str, cfg: RubricConfig | None = None) -> bool:
    """True if the column name matches the config's ``measure`` trigger."""
    return bool((cfg or DEFAULT_CONFIG).column_triggers["measure"].search(name))


def _is_sensitive_name(name: str, cfg: RubricConfig | None = None) -> bool:
    """True if the column name matches the config's ``sensitive`` trigger."""
    return bool((cfg or DEFAULT_CONFIG).column_triggers["sensitive"].search(name))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(text: str | None) -> str:
    """Trim whitespace; treat None as empty string."""
    return (text or "").strip()


def _lower(text: str | None) -> str:
    """Lowercased + trimmed view of a description, for keyword matching."""
    return _norm(text).lower()


def _evidence(text: str | None, max_chars: int) -> str:
    """Compact single-line preview of a description for a criterion's ``evidence`` field.

    Collapses internal whitespace and truncates with an ellipsis. The result
    is meant to fit in a UI cell — the full description is carried separately
    on the table/column dict for the renderer.
    """
    norm = _norm(text)
    if not norm:
        return ""
    norm = " ".join(norm.split())
    if len(norm) <= max_chars:
        return norm
    return norm[: max_chars - 1] + "…"


def _criterion(name: str, points: int, max_pts: int, evidence: str = "") -> dict[str, Any]:
    """Build a single criterion result dict in the canonical shape.

    ``passed`` is derived: a criterion only "passes" when it earns full
    points. Partial credit shows ``passed: false`` with non-zero points.
    """
    return {
        "name": name,
        "points": points,
        "max": max_pts,
        "passed": points >= max_pts,
        "evidence": evidence,
    }


def _any_in(needles: tuple[str, ...], haystack: str) -> bool:
    """True if any needle is a substring of haystack. Caller lower-cases first."""
    return any(n in haystack for n in needles)


def _kw(cfg: RubricConfig, criterion: str, bucket: str) -> tuple[str, ...]:
    """Look up a keyword bucket on the config; return empty tuple if absent."""
    return cfg.keywords.get(criterion, {}).get(bucket, ())


# ---------------------------------------------------------------------------
# Table-level criteria
#
# Each ``_check_*`` returns a criterion dict scored 0/1/2 (fail/partial/pass).
# Most checks use a "strong" keyword list for full credit and a "weak" list
# for partial credit, so a description that gestures toward the concept gets
# some recognition while one that nails it gets full credit.
# ---------------------------------------------------------------------------

def _check_business_description(description: str | None, cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether the table has a business-meaningful description.

    Pass: ≥``thresholds.table_desc_min`` chars and not a generic system label.
    Partial: present but short or matches the generic-label pattern.
    Fail: missing or empty.
    """
    desc = _norm(description)
    ev_max = cfg.thresholds["evidence_max_chars"]
    if not desc:
        return _criterion("business_description", 0, 2, "")
    if len(desc) < cfg.thresholds["table_desc_min"] or cfg.generic_table_desc_re.match(desc):
        return _criterion("business_description", 1, 2, _evidence(desc, ev_max))
    return _criterion("business_description", 2, 2, _evidence(desc, ev_max))


def _check_grain_statement(description: str | None, cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether the description states what one row represents."""
    desc_l = _lower(description)
    ev_max = cfg.thresholds["evidence_max_chars"]
    if not desc_l:
        return _criterion("grain_statement", 0, 2, "")
    pts = 2 if _any_in(_kw(cfg, "grain_statement", "strong"), desc_l) else 0
    return _criterion("grain_statement", pts, 2, _evidence(description, ev_max))


def _check_primary_keys(description: str | None, cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether the description names the primary or composite key."""
    desc_l = _lower(description)
    ev_max = cfg.thresholds["evidence_max_chars"]
    if not desc_l:
        return _criterion("primary_keys", 0, 2, "")
    if _any_in(_kw(cfg, "primary_keys", "strong"), desc_l):
        return _criterion("primary_keys", 2, 2, _evidence(description, ev_max))
    if _any_in(_kw(cfg, "primary_keys", "weak"), desc_l):
        return _criterion("primary_keys", 1, 2, _evidence(description, ev_max))
    return _criterion("primary_keys", 0, 2, "")


def _check_join_guidance(description: str | None, cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether the description tells users how to join to related tables."""
    desc_l = _lower(description)
    ev_max = cfg.thresholds["evidence_max_chars"]
    if not desc_l:
        return _criterion("join_guidance", 0, 2, "")
    if _any_in(_kw(cfg, "join_guidance", "strong"), desc_l):
        return _criterion("join_guidance", 2, 2, _evidence(description, ev_max))
    if _any_in(_kw(cfg, "join_guidance", "weak"), desc_l):
        return _criterion("join_guidance", 1, 2, _evidence(description, ev_max))
    return _criterion("join_guidance", 0, 2, "")


def _check_ownership(description: str | None, labels: dict[str, str], cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether ownership / stewardship is recorded.

    Labels (e.g., ``owner=clinical-data-team``) win full credit because
    they're machine-queryable; description-only ownership is also accepted
    for full credit when stated explicitly.
    """
    label_keys = {k.lower() for k in labels.keys()}
    desc_l = _lower(description)
    ev_max = cfg.thresholds["evidence_max_chars"]
    label_set = set(_kw(cfg, "ownership", "label"))
    matched_labels = label_keys & label_set
    if matched_labels:
        matched = sorted(matched_labels)[0]
        return _criterion("ownership", 2, 2, f"label `{matched}={labels.get(matched, '')}`"[:ev_max])
    if _any_in(_kw(cfg, "ownership", "strong"), desc_l):
        return _criterion("ownership", 2, 2, _evidence(description, ev_max))
    if _any_in(_kw(cfg, "ownership", "weak"), desc_l):
        return _criterion("ownership", 1, 2, _evidence(description, ev_max))
    return _criterion("ownership", 0, 2, "")


def _check_sensitivity(description: str | None, labels: dict[str, str], cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether sensitivity / data classification is stated."""
    label_keys = {k.lower() for k in labels.keys()}
    desc_l = _lower(description)
    ev_max = cfg.thresholds["evidence_max_chars"]
    label_set = set(_kw(cfg, "sensitivity", "label"))
    matched_labels = label_keys & label_set
    if matched_labels:
        matched = sorted(matched_labels)[0]
        return _criterion("sensitivity", 2, 2, f"label `{matched}={labels.get(matched, '')}`"[:ev_max])
    if _any_in(_kw(cfg, "sensitivity", "strong"), desc_l):
        return _criterion("sensitivity", 2, 2, _evidence(description, ev_max))
    if _any_in(_kw(cfg, "sensitivity", "weak"), desc_l):
        return _criterion("sensitivity", 1, 2, _evidence(description, ev_max))
    return _criterion("sensitivity", 0, 2, "")


def _check_history_rule(description: str | None, cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether the description explains current-state vs. historical-versions semantics."""
    desc_l = _lower(description)
    ev_max = cfg.thresholds["evidence_max_chars"]
    if not desc_l:
        return _criterion("history_rule", 0, 2, "")
    pts = 2 if _any_in(_kw(cfg, "history_rule", "strong"), desc_l) else 0
    return _criterion("history_rule", pts, 2, _evidence(description, ev_max))


def _check_lineage(description: str | None, labels: dict[str, str], cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether the source system / upstream pipeline is identified."""
    label_keys = {k.lower() for k in labels.keys()}
    desc_l = _lower(description)
    ev_max = cfg.thresholds["evidence_max_chars"]
    label_set = set(_kw(cfg, "lineage", "label"))
    matched_labels = label_keys & label_set
    if matched_labels:
        matched = sorted(matched_labels)[0]
        return _criterion("lineage", 2, 2, f"label `{matched}={labels.get(matched, '')}`"[:ev_max])
    if _any_in(_kw(cfg, "lineage", "strong"), desc_l):
        return _criterion("lineage", 2, 2, _evidence(description, ev_max))
    if _any_in(_kw(cfg, "lineage", "weak"), desc_l):
        return _criterion("lineage", 1, 2, _evidence(description, ev_max))
    return _criterion("lineage", 0, 2, "")


def score_table_metadata(
    description: str | None,
    labels: dict[str, str],
    *,
    config: RubricConfig | None = None,
) -> dict[str, Any]:
    """Run all 8 table-level criteria, return ``{points, max, criteria}``."""
    cfg = config or DEFAULT_CONFIG
    labels = labels or {}
    criteria = [
        _check_business_description(description, cfg),
        _check_grain_statement(description, cfg),
        _check_primary_keys(description, cfg),
        _check_join_guidance(description, cfg),
        _check_ownership(description, labels, cfg),
        _check_sensitivity(description, labels, cfg),
        _check_history_rule(description, cfg),
        _check_lineage(description, labels, cfg),
    ]
    return {
        "points": sum(c["points"] for c in criteria),
        "max": sum(c["max"] for c in criteria),
        "criteria": criteria,
    }


# ---------------------------------------------------------------------------
# Column-level criteria
# ---------------------------------------------------------------------------

def _check_has_description(col: dict[str, Any], cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether the column has a non-empty description.

    Pass: ≥10 chars. Partial: present but very short (<10 chars).
    Fail: missing or empty.
    """
    desc = _norm(col.get("description"))
    ev_max = cfg.thresholds["evidence_max_chars"]
    if not desc:
        return _criterion("has_description", 0, 2, "")
    if len(desc) < 10:
        return _criterion("has_description", 1, 2, _evidence(desc, ev_max))
    return _criterion("has_description", 2, 2, _evidence(desc, ev_max))


def _check_not_type_echo(col: dict[str, Any], cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether the description says something beyond the type/name.

    Pass: ≥``thresholds.column_desc_min_full`` chars and doesn't match an echo pattern.
    Partial: between ``column_desc_min_partial`` and ``column_desc_min_full`` chars (real but minimal).
    Fail: matches an echo pattern, equals the column name, or shorter than ``column_desc_min_partial``.
    """
    desc = _norm(col.get("description"))
    name = col.get("name", "")
    ev_max = cfg.thresholds["evidence_max_chars"]
    if not desc:
        return _criterion("not_type_echo", 0, 2, "")
    desc_l = desc.lower()
    if desc_l == name.lower() or desc_l == f"{name.lower()} field" or desc_l == f"{name.lower()} column":
        return _criterion("not_type_echo", 0, 2, _evidence(desc, ev_max))
    for pat in cfg.type_echo_patterns:
        if re.match(pat, desc_l):
            return _criterion("not_type_echo", 0, 2, _evidence(desc, ev_max))
    if len(desc) < cfg.thresholds["column_desc_min_partial"]:
        return _criterion("not_type_echo", 0, 2, _evidence(desc, ev_max))
    if len(desc) < cfg.thresholds["column_desc_min_full"]:
        return _criterion("not_type_echo", 1, 2, _evidence(desc, ev_max))
    return _criterion("not_type_echo", 2, 2, _evidence(desc, ev_max))


def _check_coded_explained(col: dict[str, Any], cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether a coded column's description explains the values or code system.

    Only applied when the column name matches the ``coded`` trigger.
    """
    desc_l = _lower(col.get("description"))
    ev_max = cfg.thresholds["evidence_max_chars"]
    if not desc_l:
        return _criterion("coded_field_explained", 0, 2, "")
    if any(k in desc_l for k in _kw(cfg, "coded_field_explained", "strong")):
        return _criterion("coded_field_explained", 2, 2, _evidence(col.get("description"), ev_max))
    return _criterion("coded_field_explained", 0, 2, _evidence(col.get("description"), ev_max))


def _check_units_or_format(col: dict[str, Any], cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether a measure / temporal column states units, timezone, or format.

    Only applied when the column name matches the ``measure`` trigger.
    """
    desc_l = _lower(col.get("description"))
    ev_max = cfg.thresholds["evidence_max_chars"]
    if not desc_l:
        return _criterion("units_or_format", 0, 2, "")
    if any(k in desc_l for k in _kw(cfg, "units_or_format", "strong")):
        return _criterion("units_or_format", 2, 2, _evidence(col.get("description"), ev_max))
    return _criterion("units_or_format", 0, 2, _evidence(col.get("description"), ev_max))


def _check_sensitivity_flagged(col: dict[str, Any], cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether a sensitive-named column is flagged as sensitive.

    Full credit if BigQuery policy tags are present (machine-queryable),
    or if the description uses sensitivity language. Only applied when the
    column name matches the ``sensitive`` trigger.
    """
    if col.get("policy_tags"):
        return _criterion("sensitivity_flagged", 2, 2, "policy_tags present")
    desc_l = _lower(col.get("description"))
    ev_max = cfg.thresholds["evidence_max_chars"]
    if not desc_l:
        return _criterion("sensitivity_flagged", 0, 2, "")
    if any(k in desc_l for k in _kw(cfg, "sensitivity_flagged", "strong")):
        return _criterion("sensitivity_flagged", 2, 2, _evidence(col.get("description"), ev_max))
    return _criterion("sensitivity_flagged", 0, 2, _evidence(col.get("description"), ev_max))


def _check_caveats(col: dict[str, Any], cfg: RubricConfig) -> dict[str, Any]:
    """Bonus criterion for explicit caveats / known traps.

    Only contributes to a column's ``max`` when a caveat phrase is present.
    """
    desc_l = _lower(col.get("description"))
    ev_max = cfg.thresholds["evidence_max_chars"]
    if any(k in desc_l for k in _kw(cfg, "caveats_present", "strong")):
        return _criterion("caveats_present", 2, 2, _evidence(col.get("description"), ev_max))
    return _criterion("caveats_present", 0, 0, "")


def _check_derived_or_source_status(col: dict[str, Any], cfg: RubricConfig) -> dict[str, Any]:
    """Scores whether the description states the value's provenance.

    Always applies. Binary: pass if any provenance keyword is present
    (derived/calculated/source/FK/auto-generated framings), fail otherwise.
    """
    desc_l = _lower(col.get("description"))
    ev_max = cfg.thresholds["evidence_max_chars"]
    if not desc_l:
        return _criterion("derived_or_source_status", 0, 2, "")
    if any(k in desc_l for k in _kw(cfg, "derived_or_source_status", "strong")):
        return _criterion("derived_or_source_status", 2, 2, _evidence(col.get("description"), ev_max))
    return _criterion("derived_or_source_status", 0, 2, "")


def score_column_metadata(col: dict[str, Any], *, config: RubricConfig | None = None) -> dict[str, Any]:
    """Run column-level criteria for a single column.

    Returns ``{name, points, max, criteria}``. ``max`` varies because the
    coded/measure/sensitivity criteria only count when the column name
    matches the relevant trigger, and ``caveats_present`` only counts if the
    description actually contains a caveat phrase.
    """
    cfg = config or DEFAULT_CONFIG
    name = col.get("name", "")
    criteria: list[dict[str, Any]] = [
        _check_has_description(col, cfg),
        _check_not_type_echo(col, cfg),
        _check_derived_or_source_status(col, cfg),
    ]
    if cfg.column_triggers["coded"].search(name):
        criteria.append(_check_coded_explained(col, cfg))
    if cfg.column_triggers["measure"].search(name):
        criteria.append(_check_units_or_format(col, cfg))
    if cfg.column_triggers["sensitive"].search(name):
        criteria.append(_check_sensitivity_flagged(col, cfg))
    criteria.append(_check_caveats(col, cfg))
    return {
        "name": name,
        "type": col.get("type"),
        "mode": col.get("mode"),
        "description": col.get("description"),
        "points": sum(c["points"] for c in criteria),
        "max": sum(c["max"] for c in criteria),
        "criteria": criteria,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _grade(score: int, cfg: RubricConfig | None = None) -> str:
    """Map a 0–100 integer score to a letter grade per the config's cutoffs."""
    cutoffs = (cfg or DEFAULT_CONFIG).grade_cutoffs
    if score >= cutoffs["A"]:
        return "A"
    if score >= cutoffs["B"]:
        return "B"
    if score >= cutoffs["C"]:
        return "C"
    if score >= cutoffs["D"]:
        return "D"
    return "F"


# ---------------------------------------------------------------------------
# Suggested-fix heuristics
#
# For each criterion that can fail, produce a concrete suggested-fix string.
# These are deterministic templates — they look at the schema where possible
# (likely PK columns, FK candidates, sensitive-named columns, SCD signals)
# but never fabricate facts about the table. Path A agents grading
# semantically should produce *better* suggestions by reading the description
# and schema directly; SKILL.md instructs them to.
# ---------------------------------------------------------------------------

def _likely_pk_columns(columns: list[dict[str, Any]]) -> list[str]:
    """Columns that look like primary-ish identifiers (`id`, `*_id`, `*_pk`)."""
    out: list[str] = []
    for col in columns or []:
        name = (col.get("name") or "")
        lname = name.lower()
        if lname == "id" or lname.endswith("_id") or lname.endswith("_pk"):
            out.append(name)
    return out


def _likely_fk_columns(columns: list[dict[str, Any]]) -> list[str]:
    """Columns that look like foreign keys: `*_id` but not the table's own id."""
    out: list[str] = []
    for col in columns or []:
        name = (col.get("name") or "")
        lname = name.lower()
        if lname == "id":
            continue
        if lname.endswith("_id") or lname.endswith("_fk"):
            out.append(name)
    return out


def _suggest_business_description(table_input: dict[str, Any]) -> str:
    return (
        "Describe what this table represents in business terms — what entity or process it tracks, "
        "and why it exists. Aim for 2–3 sentences."
    )


def _suggest_grain_statement(columns: list[dict[str, Any]]) -> str:
    pks = _likely_pk_columns(columns)
    if pks:
        return f"Add: 'Grain: one row per `{pks[0]}`.'"
    return "Add: 'Grain: one row per <entity>.' Replace <entity> with what each row represents."


def _suggest_primary_keys(columns: list[dict[str, Any]]) -> str:
    pks = _likely_pk_columns(columns)
    if len(pks) >= 2:
        keys = ", ".join(f"`{k}`" for k in pks[:3])
        return f"Add: 'Primary key: {keys}' (or composite: ({keys}))."
    if pks:
        return f"Add: 'Primary key: `{pks[0]}`.'"
    return "Add: 'Primary key: <column>.' Replace <column> with the uniquely-identifying column(s)."


def _suggest_join_guidance(columns: list[dict[str, Any]]) -> str:
    fks = _likely_fk_columns(columns)
    if fks:
        sample = fks[0]
        return f"Add: 'Join to <related-table> on `{sample}`.' Repeat for each FK."
    return "Add: 'Join to <related table> on <fk_column>.' List each FK and the table it joins to."


def _suggest_ownership(table_input: dict[str, Any]) -> str:
    return (
        "Add an `owner=<team>` BigQuery label, or include 'Owner: <team>' in the description. "
        "Labels are preferred (machine-queryable for governance tooling)."
    )


def _suggest_sensitivity(columns: list[dict[str, Any]], cfg: RubricConfig) -> str:
    sensitive_cols: list[str] = []
    for col in columns or []:
        name = col.get("name") or ""
        if cfg.column_triggers["sensitive"].search(name):
            sensitive_cols.append(name)
    if sensitive_cols:
        listed = ", ".join(f"`{c}`" for c in sensitive_cols[:5])
        more = "" if len(sensitive_cols) <= 5 else f" (and {len(sensitive_cols) - 5} more)"
        return (
            f"Looks like this table contains PHI/PII (columns: {listed}{more}). "
            f"Add: 'Contains PHI/PII.' or attach a `phi=true` / `pii=true` label."
        )
    return "If this table has no sensitive data, add 'Public — no PII/PHI.' Otherwise classify it explicitly."


def _suggest_history_rule(columns: list[dict[str, Any]]) -> str:
    names_l = {(col.get("name") or "").lower() for col in (columns or [])}
    has_scd = any(
        "valid_from" in n or "valid_to" in n or "_eff_dt" in n or n.endswith("_record_ind")
        or "version" in n or "effective" in n
        for n in names_l
    )
    if has_scd:
        return (
            "Looks like a type-2 SCD. Add: 'Type-2 SCD; filter `latest_record_ind=1` "
            "(or `valid_to IS NULL`) for current state.'"
        )
    return "State whether this is a snapshot (with `as_of_date`), a type-2 SCD, or current-state-only."


def _suggest_lineage(table_input: dict[str, Any]) -> str:
    return (
        "Add 'Loaded from <source-system>' to the description, or attach a `source=<system>` label. "
        "Labels are preferred (machine-queryable)."
    )


def _suggest_has_description(col: dict[str, Any]) -> str:
    return (
        f"Add a description for `{col.get('name')}` explaining what the value represents in business terms."
    )


def _suggest_not_type_echo(col: dict[str, Any]) -> str:
    return (
        f"Replace the description with one that explains what `{col.get('name')}` *means* — "
        f"not just its type or column name. Include a code system, units, or business semantics."
    )


def _suggest_derived_or_source_status(col: dict[str, Any]) -> str:
    return (
        f"State whether `{col.get('name')}` is raw from the source system or derived/calculated downstream "
        f"(e.g., 'Source-native from <system>.' or 'Calculated as <formula>.')."
    )


def _suggest_coded_explained(col: dict[str, Any]) -> str:
    return (
        f"List the allowed values or code system for `{col.get('name')}` "
        f"(e.g., 'Values: home, transfer, expired, hospice.' or 'ICD-10 diagnosis code.')."
    )


def _suggest_units_or_format(col: dict[str, Any]) -> str:
    name_l = (col.get("name") or "").lower()
    if "timestamp" in name_l or name_l.endswith("_at") or name_l.endswith("_ts") or name_l.endswith("_dt"):
        return f"State the timezone/format for `{col.get('name')}` (e.g., 'in UTC, ISO 8601.')."
    if name_l.endswith("_amount") or name_l.endswith("_price") or name_l.endswith("_cost") or "amount" in name_l:
        return f"State the currency for `{col.get('name')}` (e.g., 'in USD.')."
    if name_l.endswith("_pct") or "percent" in name_l or "rate" in name_l:
        return f"State the units for `{col.get('name')}` (e.g., 'percentage 0–100' or 'fraction 0–1.')."
    if "duration" in name_l or "elapsed" in name_l:
        return f"State the unit of time for `{col.get('name')}` (e.g., 'in seconds' or 'in milliseconds.')."
    return f"State units, timezone, or format for `{col.get('name')}` (e.g., 'in UTC' or 'in dollars')."


def _suggest_sensitivity_flagged(col: dict[str, Any]) -> str:
    return (
        f"Flag `{col.get('name')}` as PHI/PII in the description (e.g., 'Contains PHI; "
        f"do not export.'), or attach a BigQuery policy tag for machine-queryable governance."
    )


# ---------------------------------------------------------------------------
# Issues list
# ---------------------------------------------------------------------------

_TABLE_MSGS: dict[str, str] = {
    "business_description": "Table description is missing or generic; explain what the table represents in business terms.",
    "grain_statement": "No grain statement — say what one row represents (e.g., 'one row per ...').",
    "primary_keys": "Description does not identify the primary or composite key.",
    "join_guidance": "Description does not include join guidance to related tables.",
    "ownership": "No owner or steward recorded in labels or description.",
    "sensitivity": "Sensitivity / data-classification not stated; tag PHI / PII / restricted explicitly.",
    "history_rule": "No current-state vs. history rule; clarify versioning or how to filter to the latest record.",
    "lineage": "Source system / lineage not mentioned.",
}

_COLUMN_MSGS: dict[str, str] = {
    "has_description":          "Column `{name}` has no description.",
    "not_type_echo":            "Column `{name}` description just echoes the type/name; explain what the value means.",
    "derived_or_source_status": "Column `{name}` description doesn't say whether the value is raw from a source system or derived/calculated downstream.",
    "coded_field_explained":    "Column `{name}` looks coded but description doesn't list values or code system.",
    "units_or_format":          "Column `{name}` is a measure/timestamp but description doesn't state units, timezone, or format.",
    "sensitivity_flagged":      "Column `{name}` looks sensitive but neither description nor policy tags flag it.",
}


def _table_issues(
    table_input: dict[str, Any],
    table_block: dict[str, Any],
    column_block: dict[str, Any],
    cfg: RubricConfig,
) -> list[dict[str, Any]]:
    """Build a prioritized list of actionable issues, each with a suggested fix.

    Each entry is a dict with ``criterion``, ``message``, ``suggestion``, and
    (for column-level issues) ``column``. Returns up to 8 table-level issues
    followed by up to 5 column-level issues. Column issues for
    ``has_description`` / ``not_type_echo`` are treated as higher severity
    (sorted first) since they're foundational failures that block any other
    column-level signal from being trusted.

    Suggestions are heuristic templates derived from the schema where
    possible. Path A agents should produce richer, table-specific suggestions
    by reading the description directly — the heuristic is the floor.
    """
    columns_input = table_input.get("columns") or []
    issues: list[dict[str, Any]] = []

    table_suggesters = {
        "business_description": lambda: _suggest_business_description(table_input),
        "grain_statement":      lambda: _suggest_grain_statement(columns_input),
        "primary_keys":         lambda: _suggest_primary_keys(columns_input),
        "join_guidance":        lambda: _suggest_join_guidance(columns_input),
        "ownership":            lambda: _suggest_ownership(table_input),
        "sensitivity":          lambda: _suggest_sensitivity(columns_input, cfg),
        "history_rule":         lambda: _suggest_history_rule(columns_input),
        "lineage":              lambda: _suggest_lineage(table_input),
    }
    for c in table_block["criteria"]:
        if not c["passed"] and c["name"] in _TABLE_MSGS:
            issues.append({
                "criterion": c["name"],
                "message": _TABLE_MSGS[c["name"]],
                "suggestion": table_suggesters[c["name"]](),
            })

    column_suggesters = {
        "has_description":          _suggest_has_description,
        "not_type_echo":            _suggest_not_type_echo,
        "derived_or_source_status": _suggest_derived_or_source_status,
        "coded_field_explained":    _suggest_coded_explained,
        "units_or_format":          _suggest_units_or_format,
        "sensitivity_flagged":      _suggest_sensitivity_flagged,
    }
    col_failures: list[tuple[int, dict[str, Any]]] = []
    for col in column_block["columns"]:
        for c in col["criteria"]:
            if c["passed"] or c["max"] == 0:
                continue
            cname = c["name"]
            if cname not in _COLUMN_MSGS:
                continue
            severity = 2 if cname in ("has_description", "not_type_echo") else 1
            col_failures.append((severity, {
                "criterion": cname,
                "column": col.get("name", ""),
                "message": _COLUMN_MSGS[cname].format(name=col.get("name", "")),
                "suggestion": column_suggesters[cname](col),
            }))

    col_failures.sort(key=lambda t: -t[0])
    for _, issue in col_failures[:5]:
        issues.append(issue)
    return issues


def score_table(table_input: dict[str, Any], *, config: RubricConfig | None = None) -> dict[str, Any]:
    """Score a single table against the rubric.

    ``table_input`` is the normalized shape:
        {"table_id", "description", "labels", "columns": [...]}

    Returns the per-table result dict consumed by ``_scorecard_render`` and
    documented in ``SKILL.md``::

        {
            "table_id": str,
            "score": int,           # 0-100
            "grade": "A" | "B" | "C" | "D" | "F",
            "table_metadata": {
                "description": str | None,    # full text, surfaced in renderer
                "labels": dict[str, str],
                "points": int, "max": int,
                "criteria": [criterion_dict, ...],
            },
            "column_metadata": {
                "mean_normalized": float,     # 0.0-1.0 across columns
                "column_count": int,
                "columns": [column_result, ...],
            },
            "issues": [str, ...],             # actionable; up to ~13 entries
        }
    """
    cfg = config or DEFAULT_CONFIG
    table_block = score_table_metadata(
        table_input.get("description"), table_input.get("labels") or {}, config=cfg,
    )
    table_block["description"] = table_input.get("description")
    table_block["labels"] = dict(table_input.get("labels") or {})
    columns_input = table_input.get("columns") or []
    column_results = [score_column_metadata(c, config=cfg) for c in columns_input]
    if column_results:
        ratios = [
            (cr["points"] / cr["max"]) if cr["max"] > 0 else 1.0
            for cr in column_results
        ]
        col_mean_ratio = sum(ratios) / len(ratios)
    else:
        col_mean_ratio = 0.0

    table_ratio = (table_block["points"] / table_block["max"]) if table_block["max"] > 0 else 0.0
    combined = cfg.weights["table"] * table_ratio + cfg.weights["column"] * col_mean_ratio
    score = int(round(combined * 100))

    column_block = {
        "mean_normalized": round(col_mean_ratio, 4),
        "column_count": len(column_results),
        "columns": column_results,
    }

    result = {
        "table_id": table_input.get("table_id"),
        "score": score,
        "grade": _grade(score, cfg),
        "table_metadata": table_block,
        "column_metadata": column_block,
    }
    result["issues"] = _table_issues(table_input, table_block, column_block, cfg)
    return result
