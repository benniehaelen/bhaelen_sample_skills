"""Rule modules for the MCP schema linter.

Each rule module exposes three module-level constants and one function:

- ``NAME``: criterion id, matches a key in ``rubric.json``'s ``criteria``.
- ``TIER``: 1 (direct cost), 2 (indirect cost), 3 (hygiene).
- ``MAX_POINTS``: points awarded for a full pass.
- ``check(tool, catalog, rubric) -> CriterionResult``.

The ``tool`` argument is the canonical four-key dict from ``_fetchers``
augmented by ``_scoring`` with a ``"tokens"`` block (see ``_tokenizer``).
The ``catalog`` argument is the full fetcher output so rules that look
across siblings can iterate ``catalog["tools"]``. The ``rubric`` argument
is the parsed rubric config, with all thresholds and keyword lists.

Conditional criteria (``has_when_not_to_use``, ``param_format_specified``,
``destructive_action_annotated``, ``enum_values_documented``,
``units_specified``) return ``CriterionResult(..., n_a=True, points=0,
max=0)`` when they do not apply. The scoring layer filters those out of
the score denominator.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CriterionResult:
    """One rule's verdict on one tool."""

    name: str
    tier: int
    points: int
    max: int
    passed: bool
    evidence: str = ""
    tokens_saved: int = 0
    estimated: bool = False
    n_a: bool = False


def all_rules() -> list[tuple[str, "callable"]]:
    """Return ``[(name, check_fn), ...]`` for every rule module.

    The list order matches the tier order in ``SKILL.md`` so the rendered
    scorecard groups criteria intuitively.
    """
    from . import (
        boilerplate,
        concise_parameter_names,
        description_length,
        destructive_action_annotated,
        enum_values_documented,
        every_param_has_description,
        has_when_not_to_use,
        has_when_to_use,
        name_is_unique_in_catalog,
        name_is_verb_oriented,
        no_overlap_with_siblings,
        param_format_specified,
        redundant_examples,
        schema_compact,
        units_specified,
    )

    modules = [
        # Tier 1.
        description_length,
        boilerplate,
        schema_compact,
        redundant_examples,
        concise_parameter_names,
        # Tier 2.
        has_when_to_use,
        has_when_not_to_use,
        no_overlap_with_siblings,
        param_format_specified,
        destructive_action_annotated,
        # Tier 3.
        name_is_verb_oriented,
        name_is_unique_in_catalog,
        every_param_has_description,
        enum_values_documented,
        units_specified,
    ]
    return [(m.NAME, m.check) for m in modules]


__all__ = ["CriterionResult", "all_rules"]
