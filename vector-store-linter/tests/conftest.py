"""Pytest configuration for vector-store-linter.

Puts the scripts/ directory on sys.path so tests import modules the same way
the CLI entry point does. Running python scripts/lint.py places scripts/ on the
path, and the tests mirror that so imports like "import rubric" resolve in both
contexts. The unit tests require no external services.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def pytest_configure(config):
    """Register the integration marker.

    Integration tests are marked with it and skip themselves when the
    environment they need (a live store, credentials) is not configured, so a
    plain pytest run executes only the unit tests.
    """
    config.addinivalue_line(
        "markers",
        "integration: requires a live store and credentials; skipped without them",
    )
