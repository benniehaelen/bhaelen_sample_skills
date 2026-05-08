"""Heuristic metadata-quality rubric.

Scores a table's authored metadata against the data-steward rubric:

- 8 table-level criteria (max 16 pts), each scored 0/1/2.
- 6 column-level criteria (max varies per column — only criteria that apply
  to the column's name pattern are counted).

Combined score: ``0.4 * (table_pts / 16) + 0.6 * column_mean_normalized``,
scaled to 0-100. Letter grade A/B/C/D/F.

Pure Python: no third-party dependencies. Same logic powers the local
script (Path B) and is referenced by SKILL.md so the agent's semantic
grading (Path A) produces the same JSON shape.

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

import re
from typing import Any

RUBRIC_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Name-pattern detectors for conditional column criteria.
#
# Three rubric criteria only apply to columns whose names suggest they
# *should* address that aspect: a column called `discharge_disposition_code`
# really should explain its code system; a column called `event_timestamp`
# really should state its timezone/format; a column called `patient_email`
# really should be flagged as sensitive. For columns that don't match these
# patterns, the criteria simply don't fire — the column isn't penalized for
# not being something it isn't.
# ---------------------------------------------------------------------------

# Coded fields: enum-like values driven by an external code system or a
# small fixed vocabulary. Naming conventions across data warehouses vary
# (`_code`, `_cd`, `_ind`, `_flag`, `_status`, `_type`, `_category`); this
# pattern matches the common ones.
_CODED_RE = re.compile(r"_(code|status|flag|type|cd|ind|category)$", re.IGNORECASE)

# Measure-like fields where units, timezone, or format matter for correct
# interpretation. Includes monetary, count, rate, dose, dimension, and
# temporal columns.
_MEASURE_RE = re.compile(
    r"(^|_)(amount|count|rate|pct|percent|temp|dose|qty|quantity|weight|height|"
    r"length|date|datetime|timestamp|duration|elapsed|seconds|minutes|hours|days|price|cost)(_|$)",
    re.IGNORECASE,
)

# Names that strongly suggest PII / PHI: identifiers, contact info, and
# financial fields. A match here triggers the sensitivity_flagged criterion;
# the description (or a BigQuery policy tag) is then expected to acknowledge
# the sensitivity.
_SENSITIVE_RE = re.compile(
    r"(^|_)(ssn|email|dob|date_of_birth|phone|mrn|patient_id|address|zip|postal|"
    r"first_name|last_name|full_name|account|credit_card|card_number|tax_id)(_|$)",
    re.IGNORECASE,
)


def _is_coded_name(name: str) -> bool:
    """True if the column name suggests an enumerated / coded field."""
    return bool(_CODED_RE.search(name))


def _is_measure_name(name: str) -> bool:
    """True if the column name suggests a measured value where units/format matter."""
    return bool(_MEASURE_RE.search(name))


def _is_sensitive_name(name: str) -> bool:
    """True if the column name suggests PII / PHI / regulated data."""
    return bool(_SENSITIVE_RE.search(name))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(text: str | None) -> str:
    """Trim whitespace; treat None as empty string."""
    return (text or "").strip()


def _lower(text: str | None) -> str:
    """Lowercased + trimmed view of a description, for keyword matching."""
    return _norm(text).lower()


def _evidence(text: str | None, max_chars: int = 90) -> str:
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


# ---------------------------------------------------------------------------
# Table-level criteria
#
# Each ``_check_*`` returns a criterion dict scored 0/1/2 (fail/partial/pass).
# Most checks use a "strong" keyword list for full credit and a "weak" list
# for partial credit, so a description that gestures toward the concept gets
# some recognition while one that nails it gets full credit.
# ---------------------------------------------------------------------------

# Generic system-label patterns that should NOT earn full credit even if
# they're long enough. Matches things like "Table from CRM" or "events view".
_GENERIC_TABLE_DESC_RE = re.compile(
    r"^(table|view|dataset|extract|export)?\s*(from|in|for)?\s*[A-Za-z0-9_\-\s]{0,40}\.?$",
    re.IGNORECASE,
)


def _check_business_description(description: str | None) -> dict[str, Any]:
    """Scores whether the table has a business-meaningful description.

    Pass: ≥30 chars and not a generic system label.
    Partial: present but short (<30 chars) or matches the generic-label pattern.
    Fail: missing or empty.
    """
    desc = _norm(description)
    if not desc:
        return _criterion("business_description", 0, 2, "")
    if len(desc) < 30 or _GENERIC_TABLE_DESC_RE.match(desc):
        return _criterion("business_description", 1, 2, _evidence(desc))
    return _criterion("business_description", 2, 2, _evidence(desc))


# A grain statement is the single most actionable piece of metadata for
# preventing analysis errors. We accept several common phrasings.
_GRAIN_KEYS = ("grain", "one row per", "1 row per", "row represents", "one record per", "one row for each")


def _check_grain_statement(description: str | None) -> dict[str, Any]:
    """Scores whether the description states what one row represents.

    Binary: pass if any grain phrasing is present, fail otherwise.
    """
    desc_l = _lower(description)
    if not desc_l:
        return _criterion("grain_statement", 0, 2, "")
    return _criterion("grain_statement", 2 if _any_in(_GRAIN_KEYS, desc_l) else 0, 2,
                      _evidence(description))


# Strong: explicit "primary key" / "composite key" / "uniquely identifies".
# Weak: just the word "key" floating in the description (gestures at it).
_PRIMARY_KEY_STRONG = ("primary key", "unique key", "composite key", "business key", "surrogate key", "uniquely identif")
_PRIMARY_KEY_WEAK = (" key ", "key:", " keys ", "keys:")


def _check_primary_keys(description: str | None) -> dict[str, Any]:
    """Scores whether the description names the primary or composite key."""
    desc_l = _lower(description)
    if not desc_l:
        return _criterion("primary_keys", 0, 2, "")
    if _any_in(_PRIMARY_KEY_STRONG, desc_l):
        return _criterion("primary_keys", 2, 2, _evidence(description))
    if _any_in(_PRIMARY_KEY_WEAK, desc_l):
        return _criterion("primary_keys", 1, 2, _evidence(description))
    return _criterion("primary_keys", 0, 2, "")


# Strong: explicit "join to <table>" / "foreign key" / "join on …".
# Weak: just "join" / "joining" without specifics.
_JOIN_STRONG = ("join to ", "joins to ", "joined to ", "foreign key", " fk ", "join on ", "join using ", "join via ")
_JOIN_WEAK = ("join", "joining")


def _check_join_guidance(description: str | None) -> dict[str, Any]:
    """Scores whether the description tells users how to join to related tables."""
    desc_l = _lower(description)
    if not desc_l:
        return _criterion("join_guidance", 0, 2, "")
    if _any_in(_JOIN_STRONG, desc_l):
        return _criterion("join_guidance", 2, 2, _evidence(description))
    if _any_in(_JOIN_WEAK, desc_l):
        return _criterion("join_guidance", 1, 2, _evidence(description))
    return _criterion("join_guidance", 0, 2, "")


# Ownership can be stated structurally (BigQuery labels) OR in the
# description ("Owner: clinical-data-team"). Labels get full credit
# directly; description has strong (with colon, "owned by") and weak
# (just the word) tiers.
_OWNERSHIP_LABEL_KEYS = ("owner", "steward", "team", "domain", "contact")
_OWNERSHIP_DESC = ("owner:", "steward:", "team:", "contact:", "owned by", "stewarded by")
_OWNERSHIP_WEAK = ("owner", "steward")


def _check_ownership(description: str | None, labels: dict[str, str]) -> dict[str, Any]:
    """Scores whether ownership / stewardship is recorded.

    Labels (e.g., ``owner=clinical-data-team``) win full credit because
    they're machine-queryable; description-only ownership is also accepted
    for full credit when stated explicitly.
    """
    label_keys = {k.lower() for k in labels.keys()}
    desc_l = _lower(description)
    if label_keys & set(_OWNERSHIP_LABEL_KEYS):
        matched = sorted(label_keys & set(_OWNERSHIP_LABEL_KEYS))[0]
        return _criterion("ownership", 2, 2, f"label `{matched}={labels.get(matched, '')}`"[:90])
    if _any_in(_OWNERSHIP_DESC, desc_l):
        return _criterion("ownership", 2, 2, _evidence(description))
    if _any_in(_OWNERSHIP_WEAK, desc_l):
        return _criterion("ownership", 1, 2, _evidence(description))
    return _criterion("ownership", 0, 2, "")


# Sensitivity classification: a label is best (machine-queryable for DLP /
# governance tooling), explicit phrasing in the description is also full
# credit, just dropping "PHI" somewhere is partial.
_SENS_LABEL_KEYS = ("pii", "phi", "sensitive", "sensitivity", "classification", "data_classification")
_SENS_DESC_STRONG = ("contains phi", "contains pii", "phi data", "pii data", "restricted", "confidential",
                     "sensitivity:", "classification:")
_SENS_DESC_WEAK = ("phi", "pii", "sensitive", "non-sensitive", "public data")


def _check_sensitivity(description: str | None, labels: dict[str, str]) -> dict[str, Any]:
    """Scores whether sensitivity / data classification is stated."""
    label_keys = {k.lower() for k in labels.keys()}
    desc_l = _lower(description)
    if label_keys & set(_SENS_LABEL_KEYS):
        matched = sorted(label_keys & set(_SENS_LABEL_KEYS))[0]
        return _criterion("sensitivity", 2, 2, f"label `{matched}={labels.get(matched, '')}`"[:90])
    if _any_in(_SENS_DESC_STRONG, desc_l):
        return _criterion("sensitivity", 2, 2, _evidence(description))
    if _any_in(_SENS_DESC_WEAK, desc_l):
        return _criterion("sensitivity", 1, 2, _evidence(description))
    return _criterion("sensitivity", 0, 2, "")


# History / current-state distinguishes "this row is the current truth"
# from "this row is one historical version among many". Critical for
# correct filtering on type-2 SCD or snapshot tables.
_HISTORY_KEYS = ("current state", "current-state", "as of", "as_of", "snapshot", "scd", "type 2",
                 "type-2", "point in time", "point-in-time", "latest_record", "history", "version",
                 "current versus history", "versioned")


def _check_history_rule(description: str | None) -> dict[str, Any]:
    """Scores whether the description explains current-state vs. historical-versions semantics.

    Binary: pass if any history-related phrasing is present, fail otherwise.
    """
    desc_l = _lower(description)
    if not desc_l:
        return _criterion("history_rule", 0, 2, "")
    return _criterion("history_rule", 2 if _any_in(_HISTORY_KEYS, desc_l) else 0, 2,
                      _evidence(description))


# Lineage can be a label (`source=adt-feed`) or explicit description text.
# Just the word "source" without specifics is partial.
_LINEAGE_LABEL_KEYS = ("source", "upstream", "pipeline", "system_of_record")
_LINEAGE_STRONG = ("source system", "derived from", "upstream", "produced by", "ingested from",
                   "loaded from", "etl from", "system of record", "pipeline:")
_LINEAGE_WEAK = ("source",)


def _check_lineage(description: str | None, labels: dict[str, str]) -> dict[str, Any]:
    """Scores whether the source system / upstream pipeline is identified."""
    label_keys = {k.lower() for k in labels.keys()}
    desc_l = _lower(description)
    if label_keys & set(_LINEAGE_LABEL_KEYS):
        matched = sorted(label_keys & set(_LINEAGE_LABEL_KEYS))[0]
        return _criterion("lineage", 2, 2, f"label `{matched}={labels.get(matched, '')}`"[:90])
    if _any_in(_LINEAGE_STRONG, desc_l):
        return _criterion("lineage", 2, 2, _evidence(description))
    if _any_in(_LINEAGE_WEAK, desc_l):
        return _criterion("lineage", 1, 2, _evidence(description))
    return _criterion("lineage", 0, 2, "")


def score_table_metadata(description: str | None, labels: dict[str, str]) -> dict[str, Any]:
    """Run all 8 table-level criteria, return ``{points, max, criteria}``."""
    labels = labels or {}
    criteria = [
        _check_business_description(description),
        _check_grain_statement(description),
        _check_primary_keys(description),
        _check_join_guidance(description),
        _check_ownership(description, labels),
        _check_sensitivity(description, labels),
        _check_history_rule(description),
        _check_lineage(description, labels),
    ]
    return {
        "points": sum(c["points"] for c in criteria),
        "max": sum(c["max"] for c in criteria),
        "criteria": criteria,
    }


# ---------------------------------------------------------------------------
# Column-level criteria
#
# Three criteria always apply: has_description, not_type_echo, and
# derived_or_source_status. The remaining criteria fire only when the
# column's name matches the relevant pattern (see _is_*_name helpers
# above), so a column's ``max`` varies — fair across heterogeneous schemas.
# ---------------------------------------------------------------------------

def _check_has_description(col: dict[str, Any]) -> dict[str, Any]:
    """Scores whether the column has a non-empty description.

    Pass: ≥10 chars. Partial: present but very short (<10 chars).
    Fail: missing or empty.
    """
    desc = _norm(col.get("description"))
    if not desc:
        return _criterion("has_description", 0, 2, "")
    if len(desc) < 10:
        return _criterion("has_description", 1, 2, _evidence(desc))
    return _criterion("has_description", 2, 2, _evidence(desc))


# Anti-patterns: descriptions that just echo the type or column name and add
# no business meaning. We catch "string field", "the user_id field", etc.,
# because those are the most common low-effort placeholders stewards leave
# in BigQuery descriptions.
_TYPE_ECHO_PATTERNS = (
    r"^(string|str|int|integer|int64|float|float64|number|numeric|date|datetime|timestamp|"
    r"boolean|bool|bytes|json|geography)\s*(field|column|value|type)?$",
    r"^the\s+\w+(\s+(field|column))?$",
    r"^\w+\s+(field|column)$",
)


def _check_not_type_echo(col: dict[str, Any]) -> dict[str, Any]:
    """Scores whether the description says something beyond the type/name.

    Pass: ≥15 chars and doesn't match an echo pattern.
    Partial: 8–14 chars (real but minimal).
    Fail: matches an echo pattern, equals the column name, or <8 chars.
    """
    desc = _norm(col.get("description"))
    name = col.get("name", "")
    if not desc:
        return _criterion("not_type_echo", 0, 2, "")
    desc_l = desc.lower()
    if desc_l == name.lower() or desc_l == f"{name.lower()} field" or desc_l == f"{name.lower()} column":
        return _criterion("not_type_echo", 0, 2, _evidence(desc))
    for pat in _TYPE_ECHO_PATTERNS:
        if re.match(pat, desc_l):
            return _criterion("not_type_echo", 0, 2, _evidence(desc))
    if len(desc) < 8:
        return _criterion("not_type_echo", 0, 2, _evidence(desc))
    if len(desc) < 15:
        return _criterion("not_type_echo", 1, 2, _evidence(desc))
    return _criterion("not_type_echo", 2, 2, _evidence(desc))


# Healthcare code systems (ICD/SNOMED/LOINC/CPT/HCPCS/RxNorm/NDC) get
# special-cased in addition to the generic "values map to ..." phrasing,
# since those are the most common code-set references in clinical data.
_CODED_EXPLAINED_KEYS = ("value", "values", "code", "codes", "code system", "enum", "category", "categories",
                         "y/n", "yes/no", "0/1", "true/false", "valid values", "allowed values", "code set",
                         "icd", "snomed", "loinc", "cpt", "hcpcs", "rxnorm", "ndc")


def _check_coded_explained(col: dict[str, Any]) -> dict[str, Any]:
    """Scores whether a coded column's description explains the values or code system.

    Only applied when the column name matches the coded pattern; see
    ``_is_coded_name``.
    """
    desc_l = _lower(col.get("description"))
    if not desc_l:
        return _criterion("coded_field_explained", 0, 2, "")
    if any(k in desc_l for k in _CODED_EXPLAINED_KEYS):
        return _criterion("coded_field_explained", 2, 2, _evidence(col.get("description")))
    return _criterion("coded_field_explained", 0, 2, _evidence(col.get("description")))


# Unit / format / timezone tokens. Covers temporal (UTC, ISO 8601),
# monetary (USD, "in dollars"), physical (kg, celsius), and ratio/percent
# framings. Also accepts explicit "format: …" or "formatted as …".
_UNITS_KEYS = (
    "utc", "iso 8601", "iso8601", "in dollars", "usd", "eur", "gbp",
    "celsius", "fahrenheit", "kelvin",
    "milliseconds", "seconds", "minutes", "hours", "days", "ms", "ns",
    "kg", " lb", "pounds", "kilograms", "grams", "meters", "cm",
    "percent", "%", "per ", "rate per", "fraction",
    "format:", "format ", "formatted as", "yyyy", "mm-dd", "epoch", "unix time",
    "timezone",
)


def _check_units_or_format(col: dict[str, Any]) -> dict[str, Any]:
    """Scores whether a measure / temporal column states units, timezone, or format.

    Only applied when the column name matches the measure pattern; see
    ``_is_measure_name``.
    """
    desc_l = _lower(col.get("description"))
    if not desc_l:
        return _criterion("units_or_format", 0, 2, "")
    if any(k in desc_l for k in _UNITS_KEYS):
        return _criterion("units_or_format", 2, 2, _evidence(col.get("description")))
    return _criterion("units_or_format", 0, 2, _evidence(col.get("description")))


_SENSITIVITY_DESC_KEYS = ("phi", "pii", "sensitive", "restricted", "confidential",
                          "personally identifiable", "protected health", "do not export",
                          "policy:", "classification:")


def _check_sensitivity_flagged(col: dict[str, Any]) -> dict[str, Any]:
    """Scores whether a sensitive-named column is flagged as sensitive.

    Full credit if BigQuery policy tags are present (machine-queryable),
    or if the description uses sensitivity language. Only applied when the
    column name matches the sensitive pattern; see ``_is_sensitive_name``.
    """
    if col.get("policy_tags"):
        return _criterion("sensitivity_flagged", 2, 2, "policy_tags present")
    desc_l = _lower(col.get("description"))
    if not desc_l:
        return _criterion("sensitivity_flagged", 0, 2, "")
    if any(k in desc_l for k in _SENSITIVITY_DESC_KEYS):
        return _criterion("sensitivity_flagged", 2, 2, _evidence(col.get("description")))
    return _criterion("sensitivity_flagged", 0, 2, _evidence(col.get("description")))


# Caveat phrases real stewards use when warning users. Includes deprecation
# language, key-non-enforcement notes, null semantics, overloading hints,
# and explicit "note:/caution:/warning:" prefixes.
_CAVEAT_KEYS = (
    "deprecated", "legacy", "do not use", "do not rely", "not enforced", "by design",
    "be aware", "note:", "caution:", "warning:", "warn:", "gotcha", "trap:",
    "null when", "null if", "may be null", "may contain", "can be null",
    "overloaded", "multiple meanings", "ambiguous", "reserved",
    "to be removed", "will be removed", "duplicates exist", "duplicates may",
    "not unique", "non-unique", "not guaranteed", "should not be",
)


def _check_caveats(col: dict[str, Any]) -> dict[str, Any]:
    """Bonus criterion for explicit caveats / known traps.

    Only contributes to a column's ``max`` when a caveat phrase is present —
    columns with nothing to caveat aren't penalized for the absence. This
    matches the rubric's framing of caveats as a quality bonus, not a base
    requirement.
    """
    desc_l = _lower(col.get("description"))
    if any(k in desc_l for k in _CAVEAT_KEYS):
        return _criterion("caveats_present", 2, 2, _evidence(col.get("description")))
    return _criterion("caveats_present", 0, 0, "")


# Always-applicable: source/derived provenance.
# The rubric treats this as essential for every column ("indicates whether
# the value is raw from the source system or calculated downstream").
# Pass keywords cover three idioms: "derived/calculated" framing,
# "raw from source" framing, and FK / lookup framings (which implicitly
# state provenance via the foreign reference).
_DERIVED_OR_SOURCE_KEYS = (
    "derived from", "derived as", "calculated as", "calculated from", "computed from",
    "computed as", "transformed from", "parsed from", "extracted from", "aggregated from",
    "raw from", "from the source", "source system", "source-native", "source field",
    "system of record", "source of truth", "ingested from", "loaded from", "etl from",
    "from upstream", "lineage:", "provenance:", "source:",
    "foreign key to", "fk to ", " fk ", "reference to", "references ", "lookup against",
    "join key to", "join to ",
    "generated by", "auto-generated", "auto generated", "uuid", "surrogate key",
    "natural key from",
)


def _check_derived_or_source_status(col: dict[str, Any]) -> dict[str, Any]:
    """Scores whether the description states the value's provenance.

    Always applies. Binary: pass if any provenance keyword is present
    (derived/calculated/source/FK/auto-generated framings), fail otherwise.
    """
    desc_l = _lower(col.get("description"))
    if not desc_l:
        return _criterion("derived_or_source_status", 0, 2, "")
    if any(k in desc_l for k in _DERIVED_OR_SOURCE_KEYS):
        return _criterion("derived_or_source_status", 2, 2, _evidence(col.get("description")))
    return _criterion("derived_or_source_status", 0, 2, "")


def score_column_metadata(col: dict[str, Any]) -> dict[str, Any]:
    """Run column-level criteria for a single column.

    Returns ``{name, points, max, criteria}``. ``max`` varies because the
    coded/measure/sensitivity criteria only count when the column name
    matches the relevant pattern, and ``caveats_present`` only counts if the
    description actually contains a caveat phrase.
    """
    name = col.get("name", "")
    criteria: list[dict[str, Any]] = [
        _check_has_description(col),
        _check_not_type_echo(col),
        _check_derived_or_source_status(col),
    ]
    if _is_coded_name(name):
        criteria.append(_check_coded_explained(col))
    if _is_measure_name(name):
        criteria.append(_check_units_or_format(col))
    if _is_sensitive_name(name):
        criteria.append(_check_sensitivity_flagged(col))
    criteria.append(_check_caveats(col))
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
#
# Combined score weights table-level criteria at 40% and column-level at
# 60%. Columns are many but each column is one of several; table-level
# criteria are few but each one is heavier per item. Empirically this
# split gives the table description a meaningful ceiling while letting
# rich column metadata still drive a high overall grade.
# ---------------------------------------------------------------------------

TABLE_WEIGHT = 0.4
COLUMN_WEIGHT = 0.6


def _grade(score: int) -> str:
    """Map a 0–100 integer score to a US-style letter grade A/B/C/D/F."""
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _table_issues(table_block: dict[str, Any], column_block: dict[str, Any]) -> list[str]:
    """Build a prioritized list of human-readable, actionable issues.

    Returns up to 8 table-level issues followed by up to 5 column-level
    issues. Column issues for "has_description" / "not_type_echo" are
    treated as higher severity (sorted first) since they're foundational
    failures that block any other column-level signal from being trusted.
    """
    issues: list[str] = []
    table_msgs = {
        "business_description": "Table description is missing or generic; explain what the table represents in business terms.",
        "grain_statement": "No grain statement — say what one row represents (e.g., 'one row per ...').",
        "primary_keys": "Description does not identify the primary or composite key.",
        "join_guidance": "Description does not include join guidance to related tables.",
        "ownership": "No owner or steward recorded in labels or description.",
        "sensitivity": "Sensitivity / data-classification not stated; tag PHI / PII / restricted explicitly.",
        "history_rule": "No current-state vs. history rule; clarify versioning or how to filter to the latest record.",
        "lineage": "Source system / lineage not mentioned.",
    }
    for c in table_block["criteria"]:
        if not c["passed"] and c["name"] in table_msgs:
            issues.append(table_msgs[c["name"]])

    col_failures: list[tuple[int, str]] = []
    for col in column_block["columns"]:
        for c in col["criteria"]:
            if c["passed"] or c["max"] == 0:
                continue
            name = col["name"]
            cname = c["name"]
            severity = 2 if cname in ("has_description", "not_type_echo") else 1
            if cname == "has_description":
                col_failures.append((severity, f"Column `{name}` has no description."))
            elif cname == "not_type_echo":
                col_failures.append((severity, f"Column `{name}` description just echoes the type/name; explain what the value means."))
            elif cname == "derived_or_source_status":
                col_failures.append((severity, f"Column `{name}` description doesn't say whether the value is raw from a source system or derived/calculated downstream."))
            elif cname == "coded_field_explained":
                col_failures.append((severity, f"Column `{name}` looks coded but description doesn't list values or code system."))
            elif cname == "units_or_format":
                col_failures.append((severity, f"Column `{name}` is a measure/timestamp but description doesn't state units, timezone, or format."))
            elif cname == "sensitivity_flagged":
                col_failures.append((severity, f"Column `{name}` looks sensitive but neither description nor policy tags flag it."))

    col_failures.sort(key=lambda t: -t[0])
    for _, msg in col_failures[:5]:
        issues.append(msg)
    return issues


def score_table(table_input: dict[str, Any]) -> dict[str, Any]:
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
    table_block = score_table_metadata(table_input.get("description"), table_input.get("labels") or {})
    table_block["description"] = table_input.get("description")
    table_block["labels"] = dict(table_input.get("labels") or {})
    columns_input = table_input.get("columns") or []
    column_results = [score_column_metadata(c) for c in columns_input]
    if column_results:
        ratios = [
            (cr["points"] / cr["max"]) if cr["max"] > 0 else 1.0
            for cr in column_results
        ]
        col_mean_ratio = sum(ratios) / len(ratios)
    else:
        col_mean_ratio = 0.0

    table_ratio = (table_block["points"] / table_block["max"]) if table_block["max"] > 0 else 0.0
    combined = TABLE_WEIGHT * table_ratio + COLUMN_WEIGHT * col_mean_ratio
    score = int(round(combined * 100))

    column_block = {
        "mean_normalized": round(col_mean_ratio, 4),
        "column_count": len(column_results),
        "columns": column_results,
    }

    result = {
        "table_id": table_input.get("table_id"),
        "score": score,
        "grade": _grade(score),
        "table_metadata": table_block,
        "column_metadata": column_block,
    }
    result["issues"] = _table_issues(table_block, column_block)
    return result
