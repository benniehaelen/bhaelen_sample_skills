from __future__ import annotations

import argparse
import datetime as dt


def _ns(**overrides) -> argparse.Namespace:
    base = dict(
        expect_min_rows=None,
        expect_zero_duplicates=False,
        expect_freshness_within=None,
        expect_max_null_rate=None,
        expect_no_schema_drift=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestSchemaDrift:
    def test_no_drift(self, expectations_module):
        schema = [{"name": "a", "type": "STRING", "mode": "NULLABLE"}]
        drift = expectations_module.schema_drift(schema, schema)
        assert drift == {"added": [], "removed": [], "changed": []}

    def test_added_and_removed(self, expectations_module):
        baseline = [{"name": "a", "type": "STRING", "mode": "NULLABLE"}]
        current = [{"name": "b", "type": "INT64", "mode": "NULLABLE"}]
        drift = expectations_module.schema_drift(current, baseline)
        assert drift["added"] == ["b"]
        assert drift["removed"] == ["a"]
        assert drift["changed"] == []

    def test_type_change(self, expectations_module):
        baseline = [{"name": "a", "type": "INT64", "mode": "NULLABLE"}]
        current = [{"name": "a", "type": "STRING", "mode": "NULLABLE"}]
        drift = expectations_module.schema_drift(current, baseline)
        assert drift["changed"] == [
            {"name": "a", "type": {"baseline": "INT64", "current": "STRING"}}
        ]

    def test_mode_change(self, expectations_module):
        baseline = [{"name": "a", "type": "STRING", "mode": "NULLABLE"}]
        current = [{"name": "a", "type": "STRING", "mode": "REQUIRED"}]
        drift = expectations_module.schema_drift(current, baseline)
        assert drift["changed"] == [
            {"name": "a", "mode": {"baseline": "NULLABLE", "current": "REQUIRED"}}
        ]


class TestEvaluateExpectations:
    def _report(self):
        return {
            "metadata": {"num_rows": 100, "schema": []},
            "checks": {
                "duplicate_keys": {
                    "status": "complete",
                    "rows": [{"duplicate_excess_rows": 5, "duplicate_key_groups": 2}],
                },
                "freshness": {
                    "status": "complete",
                    "rows": [{"max_value": "2026-05-03T12:00:00+00:00"}],
                },
                "column_profile": {
                    "status": "complete",
                    "rows": [
                        {
                            "scanned_rows": 1000,
                            "user_id__null_count": 0,
                            "referrer__null_count": 200,
                        }
                    ],
                },
            },
        }

    def test_min_rows_pass_and_fail(self, expectations_module):
        out = expectations_module.evaluate_expectations(self._report(), _ns(expect_min_rows=50))
        assert out[0]["status"] == "passed"
        out = expectations_module.evaluate_expectations(self._report(), _ns(expect_min_rows=500))
        assert out[0]["status"] == "failed"

    def test_min_rows_skipped_when_metadata_missing(self, expectations_module):
        report = self._report()
        report["metadata"]["num_rows"] = None
        out = expectations_module.evaluate_expectations(report, _ns(expect_min_rows=10))
        assert out[0]["status"] == "skipped_no_data"

    def test_zero_duplicates_fail(self, expectations_module):
        out = expectations_module.evaluate_expectations(self._report(), _ns(expect_zero_duplicates=True))
        assert out[0]["status"] == "failed"
        assert out[0]["duplicate_excess_rows"] == 5

    def test_zero_duplicates_skipped_when_check_missing(self, expectations_module):
        report = self._report()
        del report["checks"]["duplicate_keys"]
        out = expectations_module.evaluate_expectations(report, _ns(expect_zero_duplicates=True))
        assert out[0]["status"] == "skipped_no_data"

    def test_freshness_pass_with_huge_window(self, expectations_module):
        out = expectations_module.evaluate_expectations(
            self._report(), _ns(expect_freshness_within="100000h")
        )
        assert out[0]["status"] == "passed"

    def test_freshness_fail_with_tiny_window(self, expectations_module):
        out = expectations_module.evaluate_expectations(
            self._report(), _ns(expect_freshness_within="1s")
        )
        assert out[0]["status"] == "failed"

    def test_freshness_invalid_duration_is_error(self, expectations_module):
        out = expectations_module.evaluate_expectations(
            self._report(), _ns(expect_freshness_within="bogus")
        )
        assert out[0]["status"] == "error"

    def test_freshness_check_not_run_is_skipped(self, expectations_module):
        report = self._report()
        del report["checks"]["freshness"]
        out = expectations_module.evaluate_expectations(report, _ns(expect_freshness_within="24h"))
        assert out[0]["status"] == "skipped_no_data"
        assert "did not complete" in out[0]["reason"]

    def test_freshness_null_max_value_fails(self, expectations_module):
        report = self._report()
        report["checks"]["freshness"]["rows"] = [{"max_value": None}]
        out = expectations_module.evaluate_expectations(report, _ns(expect_freshness_within="24h"))
        assert out[0]["status"] == "failed"
        assert "NULL" in out[0]["reason"]
        assert out[0]["max_value"] is None

    def test_freshness_unparseable_max_value_is_skipped(self, expectations_module):
        report = self._report()
        report["checks"]["freshness"]["rows"] = [{"max_value": "not-a-date"}]
        out = expectations_module.evaluate_expectations(report, _ns(expect_freshness_within="24h"))
        assert out[0]["status"] == "skipped_no_data"
        assert out[0]["max_value"] == "not-a-date"

    def test_freshness_naive_max_value_uses_utc_now(self, expectations_module):
        # naive datetime path: must not raise a deprecation error and must succeed for a recent value
        recent = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(minutes=5)).isoformat(timespec="seconds")
        report = self._report()
        report["checks"]["freshness"]["rows"] = [{"max_value": recent}]
        out = expectations_module.evaluate_expectations(report, _ns(expect_freshness_within="1h"))
        assert out[0]["status"] == "passed"
        assert 0 <= out[0]["age_seconds"] <= 3600

    def test_max_null_rate_mixed(self, expectations_module):
        args = _ns(expect_max_null_rate=["user_id=0", "referrer=0.05", "missing_col=0.5"])
        out = expectations_module.evaluate_expectations(self._report(), args)
        statuses = {(e.get("column"), e["status"]) for e in out}
        assert ("user_id", "passed") in statuses
        assert ("referrer", "failed") in statuses
        assert ("missing_col", "skipped_no_data") in statuses

    def test_no_schema_drift_passes_when_clean(self, expectations_module):
        report = self._report()
        report["schema_drift"] = {"added": [], "removed": [], "changed": []}
        out = expectations_module.evaluate_expectations(report, _ns(expect_no_schema_drift=True))
        assert out[0]["status"] == "passed"

    def test_no_schema_drift_fails_when_dirty(self, expectations_module):
        report = self._report()
        report["schema_drift"] = {"added": ["new_col"], "removed": [], "changed": []}
        out = expectations_module.evaluate_expectations(report, _ns(expect_no_schema_drift=True))
        assert out[0]["status"] == "failed"
        assert out[0]["added"] == 1

    def test_no_schema_drift_skipped_without_baseline(self, expectations_module):
        out = expectations_module.evaluate_expectations(self._report(), _ns(expect_no_schema_drift=True))
        assert out[0]["status"] == "skipped_no_data"
