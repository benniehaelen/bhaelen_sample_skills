"""Test setup: stub google.cloud.bigquery and expose per-module fixtures.

The renderer modules (`_render`, `_serialize`, `_validation`, `_expectations`)
have no BigQuery dependency. Stubs are still installed so tests that load the
main `evaluate_bigquery_table` script (for its CLI integration) don't fail
when the real client library is missing.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _install_google_stubs() -> None:
    if "google.cloud.bigquery" in sys.modules:
        return
    google = sys.modules.setdefault("google", types.ModuleType("google"))
    cloud = sys.modules.setdefault("google.cloud", types.ModuleType("google.cloud"))
    bigquery = types.ModuleType("google.cloud.bigquery")

    class _Placeholder:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("BigQuery client not available in tests; this is a stub.")

    bigquery.Client = _Placeholder
    bigquery.SchemaField = _Placeholder
    bigquery.Table = _Placeholder
    bigquery.QueryJobConfig = _Placeholder
    cloud.bigquery = bigquery
    google.cloud = cloud

    api_core = sys.modules.setdefault("google.api_core", types.ModuleType("google.api_core"))
    exceptions = types.ModuleType("google.api_core.exceptions")

    class GoogleAPIError(Exception):
        pass

    exceptions.GoogleAPIError = GoogleAPIError
    api_core.exceptions = exceptions
    sys.modules["google.cloud.bigquery"] = bigquery
    sys.modules["google.api_core.exceptions"] = exceptions


_install_google_stubs()


_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


@pytest.fixture(scope="session")
def serialize_module():
    return importlib.import_module("_serialize")


@pytest.fixture(scope="session")
def validation_module():
    return importlib.import_module("_validation")


@pytest.fixture(scope="session")
def expectations_module():
    return importlib.import_module("_expectations")


@pytest.fixture(scope="session")
def render_module():
    return importlib.import_module("_render")


@pytest.fixture(scope="session")
def script_module():
    """The CLI module — kept for tests that exercise the full integration path."""
    spec = importlib.util.spec_from_file_location(
        "evaluate_bigquery_table", _SCRIPTS / "evaluate_bigquery_table.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def render_report_module():
    spec = importlib.util.spec_from_file_location(
        "render_report", _SCRIPTS / "render_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
