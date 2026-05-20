"""Template-based rewrite suggestions for the Path B linter.

Each failed (non-N/A) criterion gets a concrete suggestion that references
the tool's actual name. Path A agents produce richer semantic suggestions;
these templates are the floor.

Public function:

- ``make_issues(tool, results, rubric)`` returns a list of issue dicts
  matching ``SKILL.md``'s per-tool ``issues`` shape:
  ``{"criterion", "tier", "message", "suggestion"}``.
"""

from __future__ import annotations

from typing import Any, Iterable

from _rules import CriterionResult


def make_issues(
    tool: dict[str, Any],
    results: Iterable[CriterionResult],
    rubric: dict[str, Any],
) -> list[dict[str, Any]]:
    """Convert non-N/A failed CriterionResults into issue dicts."""
    out: list[dict[str, Any]] = []
    for r in results:
        if r.n_a or r.passed:
            continue
        out.append({
            "criterion": r.name,
            "tier": r.tier,
            "message": r.evidence or "",
            "suggestion": _suggestion_for(r, tool, rubric),
        })
    return out


def _suggestion_for(r: CriterionResult, tool: dict[str, Any], rubric: dict[str, Any]) -> str:
    name = tool.get("name", "<tool>")
    t = rubric.get("thresholds", {})

    if r.name == "description_length_appropriate":
        good_max = int(t.get("description_tokens_good_max", 150))
        target_low = good_max // 2
        return (
            f"Trim `{name}`'s description to roughly {target_low}-{good_max} tokens. "
            "Move inline examples to a separate `examples` field or a README, and cut background paragraphs."
        )
    if r.name == "no_boilerplate_opener":
        return (
            f"Rewrite the opening sentence of `{name}`'s description. "
            "Start with the verb describing what the tool does, not 'This tool allows you to...' framing."
        )
    if r.name == "schema_compact":
        good_max = int(t.get("schema_tokens_good_max", 200))
        return (
            f"Reduce `{name}`'s input schema below {good_max} tokens. "
            "Flatten nested objects, drop optional fields the model rarely needs, and collapse oneOf/anyOf unions where one branch dominates."
        )
    if r.name == "no_redundant_examples":
        return (
            f"Pick one place for `{name}`'s examples (description OR schema). "
            "Duplicated example content pays tokens on every turn."
        )
    if r.name == "concise_parameter_names":
        return (
            f"Rename the over-long parameters of `{name}` to the shortest form that still reads "
            "(e.g. `customer_id`, not `the_customer_email_address`)."
        )
    if r.name == "has_when_to_use":
        return (
            f"Add a 'Use this when ...' sentence to `{name}`'s description that positions it against any sibling tools."
        )
    if r.name == "has_when_not_to_use":
        return (
            f"Add a 'Do not use this for X; use Y instead.' sentence to `{name}`. "
            "Cite the sibling tool by name."
        )
    if r.name == "no_overlap_with_siblings":
        return (
            f"Add explicit distinguishing language to `{name}`'s description, referencing the overlapping sibling by name. "
            "Or consolidate the tools if they are not meaningfully different."
        )
    if r.name == "param_format_specified":
        return (
            f"For `{name}`'s date/timestamp/ID/URL parameters, state the format (ISO 8601, RFC 3339, UUID v4, fully-qualified URL) "
            "in the parameter's description or via the JSON Schema `format` keyword."
        )
    if r.name == "destructive_action_annotated":
        return (
            f"`{name}` appears to mutate state. Set `annotations.destructiveHint: true` (and `idempotentHint` if applicable), "
            "AND open the description with the mutation verb (e.g. 'Creates ...', 'Deletes ...')."
        )
    if r.name == "name_is_verb_oriented":
        return f"Rename `{name}` to begin with a verb (e.g. `create_invoice`, `search_users`)."
    if r.name == "name_is_unique_in_catalog":
        return (
            f"Either rename or merge `{name}` so it is not near-synonymous with a sibling tool. "
            "The model picks one and ignores the other; pick which deliberately."
        )
    if r.name == "every_param_has_description":
        return f"Add a one-sentence description to every parameter of `{name}`. Empty descriptions cause argument fabrication."
    if r.name == "enum_values_documented":
        return (
            f"For each enum parameter in `{name}`, list the values with a brief semantic note: "
            "`status: pending (not yet submitted), shipped (in transit), delivered (received)`."
        )
    if r.name == "units_specified":
        return f"State the units in the description for `{name}`'s numeric parameters: USD cents, milliseconds, bytes, etc."

    return "Refactor per the criterion description in the rubric."
