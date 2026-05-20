"""Shared pytest fixtures for the MCP schema linter.

Unlike the other skills in this repo, the linter has no heavy third-party
dependency to stub (no google.cloud.bigquery), so this conftest only puts
``scripts/`` on the import path and exposes the built-in rubric as a fixture.
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
