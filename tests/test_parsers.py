import datetime as dt

import pytest

from strawalarm.cli import parse_days
from strawalarm.core import (fmt_delta, next_occurrence, parse_duration,
                             parse_wake_time)

SAT_NOON = dt.datetime(2026, 7, 4, 12, 0)  # a Saturday


class TestParseDuration:
    @pytest.mark.parametrize("text,seconds", [
        ("1h30m", 5400), ("90m", 5400), ("45s", 45), ("2h", 7200),
        ("1h30m20s", 5420), ("01:30:00", 5400), ("1:30", 5400),
        ("90", 5400), ("0:05", 300), ("7h 30m", 27000), (" 10m ", 600),
        ("1H30M", 5400),
    ])
    def test_valid(self, text, seconds):
        assert parse_duration(text) == seconds

    @pytest.mark.parametrize("text", ["", "banana", "h30", "1x", ":30",
                                      "1h30", "12:345"])
    def test_invalid(self, text):
        with pytest.raises(ValueError):
            parse_duration(text)


class TestParseWakeTime:
    def test_future_today(self):
        t = parse_wake_time("13:00", base=SAT_NOON)
        assert t == SAT_NOON.replace(hour=13)

    def test_past_rolls_to_tomorrow(self):
        t = parse_wake_time("07:30", base=SAT_NOON)
        assert t.date() == SAT_NOON.date() + dt.timedelta(days=1)
        assert (t.hour, t.minute) == (7, 30)

    def test_exact_now_rolls_to_tomorrow(self):
        t = parse_wake_time("12:00", base=SAT_NOON)
        assert t.date() == SAT_NOON.date() + dt.timedelta(days=1)

    def test_seconds(self):
        t = parse_wake_time("13:00:30", base=SAT_NOON)
        assert t.second == 30

    def test_relative(self):
        t = parse_wake_time("+1h30m", base=SAT_NOON)
        assert t == SAT_NOON + dt.timedelta(hours=1, minutes=30)

    @pytest.mark.parametrize("text", ["", "25:00 pm", "banana", "7"])
    def test_invalid(self, text):
        with pytest.raises(ValueError):
            parse_wake_time(text, base=SAT_NOON)


class TestNextOccurrence:
    def test_no_weekdays_passthrough(self):
        assert next_occurrence("13:00", None, SAT_NOON).weekday() == 5

    def test_weekday_constraint_skips_to_monday(self):
        t = next_occurrence("07:30", (0, 1, 2, 3, 4), SAT_NOON)
        assert t.weekday() == 0
        assert (t.hour, t.minute) == (7, 30)

    def test_same_day_kept_when_allowed(self):
        t = next_occurrence("13:00", (5,), SAT_NOON)
        assert t.date() == SAT_NOON.date()

    def test_past_time_on_allowed_day_next_week(self):
        t = next_occurrence("07:30", (5,), SAT_NOON)  # Sat 07:30 passed
        assert t.weekday() == 5
        assert t.date() == SAT_NOON.date() + dt.timedelta(days=7)

    def test_relative_ignores_weekdays(self):
        t = next_occurrence("+2h", (0,), SAT_NOON)
        assert t == SAT_NOON + dt.timedelta(hours=2)


class TestFmtDelta:
    @pytest.mark.parametrize("seconds,text", [
        (0, "0:00:00"), (59, "0:00:59"), (3661, "1:01:01"),
        (86400 * 2 + 3661, "2d 1:01:01"), (-5, "0:00:00"),
    ])
    def test_format(self, seconds, text):
        assert fmt_delta(seconds) == text


class TestParseDays:
    def test_names(self):
        assert parse_days("mon,wed,fri") == (0, 2, 4)

    def test_full_names_and_case(self):
        assert parse_days("Monday,SUNDAY") == (0, 6)

    def test_shortcuts(self):
        assert parse_days("daily") == (0, 1, 2, 3, 4, 5, 6)
        assert parse_days("weekdays") == (0, 1, 2, 3, 4)
        assert parse_days("weekend") == (5, 6)

    def test_dedup_and_sort(self):
        assert parse_days("fri,mon,fri") == (0, 4)

    def test_invalid_exits(self):
        with pytest.raises(SystemExit):
            parse_days("blursday")
