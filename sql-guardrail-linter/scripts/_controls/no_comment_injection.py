"""SQ-003 no_comment_injection (Tier 1, high).

No comment block or query hint used to smuggle directives or disable a
control. This is the one control that deliberately scans the raw statement
text (inside comments) rather than the parsed tree, because the threat lives
precisely in the text the parser discards. The trigger list is rubric data
(``comment_injection_patterns``); the scan logic is fixed here.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from . import ControlResult, LintContext, control_meta
from ._helpers import compiled_patterns

if TYPE_CHECKING:
    from _parser import ParsedStatement

ID = "SQ-003"
NAME = "no_comment_injection"
TIER = 1
SEVERITY = "high"

# Comment spans only: a ``--`` line comment or a ``/* */`` block comment, plus
# optimizer hint syntax ``/*+ ... */``. Patterns are matched against the text
# inside these spans so a literal containing "drop table" in a WHERE does not
# trip the control.
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def _comment_text(raw: str) -> str:
    return " ".join(m.group(0) for m in _COMMENT_RE.finditer(raw or ""))


def check(stmt: "ParsedStatement", ctx: LintContext, rubric: dict[str, Any]) -> ControlResult:
    tier, name, severity = control_meta(ID, rubric, tier=TIER, name=NAME, severity=SEVERITY)
    comments = _comment_text(stmt.raw)
    if not comments.strip():
        return ControlResult(ID, name, tier, severity, "pass", evidence="no comments or hints")

    patterns = compiled_patterns(rubric, "comment_injection_patterns")
    for pat in patterns:
        m = pat.search(comments)
        if m:
            snippet = " ".join(m.group(0).split())[:60]
            return ControlResult(
                ID, name, tier, severity, "fail",
                evidence=f"comment matches injection pattern: {snippet!r}",
                suggestion="Remove the directive comment / hint. The control plane strips and rejects comments that look like instructions.",
            )
    return ControlResult(ID, name, tier, severity, "pass", evidence="comments present, no injection pattern matched")
