"""Rubric loading and validation for vector-store-linter.

The rubric is a YAML document (the default lives at scripts/rubric.yaml, and a
custom rubric can be supplied via --rubric-config). This module parses it into
typed objects and validates the structure before any check runs against it. A
malformed rubric is a configuration error, so validation is strict and fails
loudly with a message that names the specific problem.

The validation logic is kept in a standalone function (validate_rubric) so it
can be reused if a rubric is ever loaded from a source other than a file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

VALID_TIERS: tuple[str, ...] = ("tier_1_config", "tier_2_content", "tier_3_retrieval")
VALID_SEVERITIES: tuple[str, ...] = ("info", "warn", "fail", "critical")

# tier_weights must sum to this, and each tier's criterion weights must sum to
# this independently. Both totals are part of the rubric contract.
EXPECTED_TIER_WEIGHT_TOTAL = 100
EXPECTED_CRITERION_WEIGHT_TOTAL = 100

_REQUIRED_CRITERION_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "tier",
    "weight",
    "severity",
    "description",
)
_REQUIRED_FAILURE_MODE_FIELDS: tuple[str, ...] = ("id", "name", "description")


class RubricError(ValueError):
    """Raised when a rubric is missing, unparseable, or structurally invalid.

    Subclasses ValueError so the CLI's usage-error handling continues to catch
    it, while code that wants to react to a rubric problem specifically can
    catch RubricError directly.
    """


@dataclass
class Criterion:
    """A single scored criterion in the rubric."""

    id: str
    name: str
    tier: str  # one of VALID_TIERS
    weight: int  # relative weight within its tier; the tier's weights sum to 100
    severity: str  # one of VALID_SEVERITIES
    description: str
    threshold: dict[str, Any]  # criterion-specific parameters, may be empty


@dataclass
class FailureMode:
    """A retrieval failure mode used in Tier 3 reporting."""

    id: str
    name: str
    description: str


@dataclass
class Rubric:
    """A parsed and validated rubric.

    Holds the tier weights, the flat list of criteria (in rubric order), and
    the failure-mode taxonomy. Use by_tier and by_id for lookups rather than
    iterating the list at call sites.
    """

    tier_weights: dict[str, int]
    criteria: list[Criterion]
    failure_modes: list[FailureMode]

    def by_tier(self, tier: str) -> list[Criterion]:
        """Return the criteria belonging to a tier, in rubric order."""
        return [c for c in self.criteria if c.tier == tier]

    def by_id(self, criterion_id: str) -> Criterion:
        """Return the criterion with the given id.

        Raises:
            KeyError: if no criterion has that id.
        """
        for criterion in self.criteria:
            if criterion.id == criterion_id:
                return criterion
        raise KeyError(f"No criterion with id {criterion_id!r} in rubric.")

    @classmethod
    def load(cls, path: str | Path) -> "Rubric":
        """Load and validate a rubric from a YAML file.

        Args:
            path: Path to the rubric YAML file.

        Returns:
            A validated Rubric.

        Raises:
            RubricError: if the file is missing, not parseable as YAML, or
                fails structural validation. The message names the problem.
        """
        file_path = Path(path)
        if not file_path.is_file():
            raise RubricError(f"Rubric file not found: {file_path}")
        try:
            raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise RubricError(f"Rubric YAML is not parseable ({file_path}): {exc}") from exc
        rubric = cls.from_dict(raw, source=str(file_path))
        logger.info(
            "Loaded rubric from %s: %d criteria across %d tiers.",
            file_path,
            len(rubric.criteria),
            len(rubric.tier_weights),
        )
        return rubric

    @classmethod
    def from_dict(cls, data: Any, source: str = "<dict>") -> "Rubric":
        """Build and validate a Rubric from an already-parsed mapping.

        Args:
            data: The parsed rubric mapping.
            source: A label used in error messages to identify where the data
                came from (a file path, typically).

        Raises:
            RubricError: if validation fails.
        """
        validate_rubric(data, source=source)
        tier_weights = {str(tier): int(weight) for tier, weight in data["tier_weights"].items()}
        criteria = [
            Criterion(
                id=raw["id"],
                name=raw["name"],
                tier=raw["tier"],
                weight=int(raw["weight"]),
                severity=raw["severity"],
                description=raw["description"],
                threshold=dict(raw.get("threshold") or {}),
            )
            for raw in data["criteria"]
        ]
        failure_modes = [
            FailureMode(id=raw["id"], name=raw["name"], description=raw["description"])
            for raw in (data.get("failure_modes") or [])
        ]
        return cls(tier_weights=tier_weights, criteria=criteria, failure_modes=failure_modes)


def validate_rubric(data: Any, source: str = "<dict>") -> None:
    """Validate a parsed rubric mapping against the documented shape.

    Checks, in order: top-level shape; tier_weights present, of integer values
    over known tiers, summing to 100; each criterion carries the required
    fields with valid types; criterion ids are unique; tiers and severities are
    from the allowed sets; every criterion tier is weighted; each tier's
    criterion weights sum to 100; and failure modes, when present, each carry
    id, name, and description with unique ids.

    A clean return means the rubric is valid.

    Raises:
        RubricError: naming the first problem found.
    """
    where = f" (in {source})" if source else ""

    if not isinstance(data, dict):
        raise RubricError(f"Rubric must be a mapping at the top level{where}.")

    tier_weights = data.get("tier_weights")
    if not isinstance(tier_weights, dict) or not tier_weights:
        raise RubricError(f"Rubric is missing a non-empty 'tier_weights' mapping{where}.")
    for tier, weight in tier_weights.items():
        if tier not in VALID_TIERS:
            raise RubricError(
                f"Unknown tier {tier!r} in tier_weights{where}. Valid tiers: {', '.join(VALID_TIERS)}."
            )
        if not _is_int(weight):
            raise RubricError(f"tier_weights[{tier!r}] must be an integer{where}, got {weight!r}.")
    tier_weight_total = sum(tier_weights.values())
    if tier_weight_total != EXPECTED_TIER_WEIGHT_TOTAL:
        breakdown = ", ".join(f"{k}={v}" for k, v in tier_weights.items())
        raise RubricError(
            f"tier_weights must sum to {EXPECTED_TIER_WEIGHT_TOTAL}{where}, "
            f"got {tier_weight_total} ({breakdown})."
        )

    criteria = data.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise RubricError(f"Rubric is missing a non-empty 'criteria' list{where}.")

    seen_ids: set[str] = set()
    weight_by_tier: dict[str, int] = {}

    for index, criterion in enumerate(criteria):
        loc = f"criteria[{index}]"
        if not isinstance(criterion, dict):
            raise RubricError(f"{loc} must be a mapping{where}.")
        for field_name in _REQUIRED_CRITERION_FIELDS:
            if field_name not in criterion or criterion[field_name] is None:
                raise RubricError(f"{loc} is missing required field {field_name!r}{where}.")

        criterion_id = criterion["id"]
        if not isinstance(criterion_id, str) or not criterion_id:
            raise RubricError(f"{loc} has an invalid id {criterion_id!r}{where}.")
        if criterion_id in seen_ids:
            raise RubricError(f"Duplicate criterion id {criterion_id!r}{where}.")
        seen_ids.add(criterion_id)

        tier = criterion["tier"]
        if tier not in VALID_TIERS:
            raise RubricError(
                f"{loc} ({criterion_id}) has invalid tier {tier!r}{where}. "
                f"Valid tiers: {', '.join(VALID_TIERS)}."
            )
        severity = criterion["severity"]
        if severity not in VALID_SEVERITIES:
            raise RubricError(
                f"{loc} ({criterion_id}) has invalid severity {severity!r}{where}. "
                f"Valid severities: {', '.join(VALID_SEVERITIES)}."
            )
        weight = criterion["weight"]
        if not _is_int(weight):
            raise RubricError(f"{loc} ({criterion_id}) weight must be an integer{where}, got {weight!r}.")
        threshold = criterion.get("threshold")
        if threshold is not None and not isinstance(threshold, dict):
            raise RubricError(
                f"{loc} ({criterion_id}) threshold must be a mapping or omitted{where}, "
                f"got {type(threshold).__name__}."
            )
        weight_by_tier[tier] = weight_by_tier.get(tier, 0) + weight

    for tier in weight_by_tier:
        if tier not in tier_weights:
            raise RubricError(
                f"Criteria reference tier {tier!r} which is not present in tier_weights{where}."
            )
    for tier in tier_weights:
        total = weight_by_tier.get(tier, 0)
        if total != EXPECTED_CRITERION_WEIGHT_TOTAL:
            raise RubricError(
                f"Criterion weights for tier {tier!r} must sum to "
                f"{EXPECTED_CRITERION_WEIGHT_TOTAL}{where}, got {total}."
            )

    failure_modes = data.get("failure_modes")
    if failure_modes is not None:
        if not isinstance(failure_modes, list):
            raise RubricError(f"'failure_modes' must be a list when present{where}.")
        seen_failure_ids: set[str] = set()
        for index, failure_mode in enumerate(failure_modes):
            loc = f"failure_modes[{index}]"
            if not isinstance(failure_mode, dict):
                raise RubricError(f"{loc} must be a mapping{where}.")
            for field_name in _REQUIRED_FAILURE_MODE_FIELDS:
                if field_name not in failure_mode or failure_mode[field_name] is None:
                    raise RubricError(f"{loc} is missing required field {field_name!r}{where}.")
            failure_id = failure_mode["id"]
            if failure_id in seen_failure_ids:
                raise RubricError(f"Duplicate failure_mode id {failure_id!r}{where}.")
            seen_failure_ids.add(failure_id)


def _is_int(value: Any) -> bool:
    """Return True if value is an integer and not a bool.

    bool is a subclass of int in Python, so a bare isinstance check would
    accept True and False as weights. The rubric never wants that.
    """
    return isinstance(value, int) and not isinstance(value, bool)
