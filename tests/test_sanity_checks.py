"""Arm-time environment warnings: local-time RTC and DST crossings."""

import datetime as dt
import time

import pytest

from strawalarm import power
from strawalarm.core import WakeSpec, crosses_dst

from test_session import make


class TestRtcLocaltime:
    def make_adjtime(self, tmp_path, third_line):
        f = tmp_path / "adjtime"
        f.write_text(f"0.0 0 0.0\n0\n{third_line}\n")
        return str(f)

    def test_local_detected(self, tmp_path):
        assert power.rtc_is_localtime(self.make_adjtime(tmp_path, "LOCAL"))

    def test_utc_not_flagged(self, tmp_path):
        assert not power.rtc_is_localtime(self.make_adjtime(tmp_path, "UTC"))

    def test_missing_file_not_flagged(self, tmp_path):
        assert not power.rtc_is_localtime(str(tmp_path / "nope"))

    def test_arm_warns_when_local(self, clock, player, fpower, fnotify,
                                  monkeypatch):
        from strawalarm import core
        monkeypatch.setattr(core.power, "rtc_is_localtime", lambda: True)
        s = make(player, fpower, clock, wake=WakeSpec(time_spec="+600s"))
        s.start()
        assert any("local time" in line for line in s.logs)
        assert any(n[2] and "RTC" in n[0] for n in fnotify)
        s.cancel()


@pytest.fixture
def kyiv_tz(monkeypatch):
    monkeypatch.setenv("TZ", "Europe/Kyiv")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


class TestDst:
    # Europe 2026: clocks spring forward on Sun 2026-03-29 03:00.
    def test_crossing_detected(self, kyiv_tz):
        before = dt.datetime(2026, 3, 28, 23, 0)
        after = dt.datetime(2026, 3, 29, 8, 0)
        assert crosses_dst(before, after)

    def test_plain_night_clean(self, kyiv_tz):
        a = dt.datetime(2026, 7, 4, 23, 0)
        b = dt.datetime(2026, 7, 5, 8, 0)
        assert not crosses_dst(a, b)

    def test_arm_warns_on_crossing(self, clock, player, fpower, fnotify,
                                   kyiv_tz):
        # fake clock at Sat 2026-03-28 23:00 local, alarm 8h later
        clock.t = dt.datetime(2026, 3, 28, 23, 0).timestamp()
        s = make(player, fpower, clock, wake=WakeSpec(time_spec="+8h"))
        s.start()
        assert any("daylight-saving" in line for line in s.logs)
        assert any("DST" in n[0] for n in fnotify)
        s.cancel()
