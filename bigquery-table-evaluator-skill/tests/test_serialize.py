from __future__ import annotations

import datetime as dt

import pytest


class TestShortInt:
    """Compact integer formatting (``1234567 -> '1.2M'``) for chart labels."""

    @pytest.mark.parametrize(
        "n, expected",
        [
            (0, "0"),
            (5, "5"),
            (999, "999"),
            (1000, "1K"),
            (1500, "1.5K"),
            (10_000, "10K"),
            (1_000_000, "1M"),
            (1_234_567, "1.2M"),
            (1_000_000_000, "1B"),
        ],
    )
    def test_short_int(self, serialize_module, n, expected):
        assert serialize_module.short_int(n) == expected


class TestFormatDatetime:
    """Human-friendly timestamp rendering (``"May 3, 2026 11:30 UTC (3h ago)"``)."""

    def test_utc_iso_with_relative(self, serialize_module):
        # Pin a recent value so the relative bit is predictable
        recent = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) - dt.timedelta(hours=3, minutes=5)
        out = serialize_module.format_datetime(recent.isoformat())
        assert "UTC" in out
        assert "(3h ago)" in out
        # Should contain a month abbreviation and 4-digit year
        assert recent.strftime("%Y") in out
        assert recent.strftime("%b") in out

    def test_strips_leading_zero_from_day(self, serialize_module):
        out = serialize_module.format_datetime("2026-05-03T11:30:00+00:00", with_relative=False)
        assert "May 3, 2026 11:30 UTC" == out
        # but does not strip leading zero from the time
        out2 = serialize_module.format_datetime("2026-05-03T03:30:00+00:00", with_relative=False)
        assert out2.endswith("03:30 UTC")

    def test_double_digit_day_unaffected(self, serialize_module):
        out = serialize_module.format_datetime("2026-05-13T11:30:00+00:00", with_relative=False)
        assert "May 13, 2026 11:30 UTC" == out

    def test_non_utc_offset_formatted(self, serialize_module):
        out = serialize_module.format_datetime("2026-05-03T11:30:00+02:00", with_relative=False)
        assert out.endswith("+02:00")

    def test_naive_datetime_assumes_utc_for_relative(self, serialize_module):
        recent = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(minutes=10)).isoformat(timespec="seconds")
        out = serialize_module.format_datetime(recent)
        assert "10m ago" in out

    def test_date_only_value(self, serialize_module):
        out = serialize_module.format_datetime("2026-05-03", with_relative=False)
        assert "May 3, 2026 00:00" in out

    def test_unparseable_passes_through(self, serialize_module):
        assert serialize_module.format_datetime("not a date", with_relative=False) == "not a date"

    def test_none_or_empty_renders_dash(self, serialize_module):
        assert serialize_module.format_datetime(None) == "—"
        assert serialize_module.format_datetime("") == "—"

    def test_future_date_uses_in_prefix(self, serialize_module):
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=10)).isoformat()
        out = serialize_module.format_datetime(future)
        assert "in 5h" in out
