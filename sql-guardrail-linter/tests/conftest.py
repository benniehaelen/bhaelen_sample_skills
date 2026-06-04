"""Shared pytest fixtures for the SQL guardrail linter.

The linter's only third-party dependency is ``sqlglot`` (the parser), which is
a real test dependency, so there is nothing to stub the way the BigQuery skills
stub ``google.cloud.bigquery``. This conftest only puts ``scripts/`` on the
import path and exposes the built-in rubric as a fixture. cost_aware behavior is
tested by injecting a ``LintContext`` with metadata directly, so no BigQuery
client is ever constructed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_RUBRIC_PATH = Path(__file__).resolve().parent.parent / "rubric.json"


@pytest.fixture
def rubric() -> dict:
    """The built-in rubric, freshly parsed for each test that mutates it."""
    return json.loads(_RUBRIC_PATH.read_text(encoding="utf-8"))
