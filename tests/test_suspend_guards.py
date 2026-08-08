"""Guards against the 2026-08-08 missed alarm: a machine that cannot
wake itself must never be put to sleep, and a suspend-without-wake spec
must be called out while the user is still awake."""

from strawalarm.core import Phase, SleepSpec, WakeSpec

from conftest import tick_until
from test_session import make


class TestSuspendWithoutWakeWarning:
    def test_warns_at_start(self, clock, player, fpower, fnotify):
        s = make(player, fpower, clock,
                 sleep=SleepSpec(seconds=60, suspend_after=True),
                 wake=WakeSpec(time_spec="+2h", wake_system=False))
        s.start()
        assert any("suspend" in line.lower() and "wake" in line.lower()
                   for line in s.logs if "Warning" in line)
        assert any(n[2] and "suspend without wake" in n[0].lower()
                   for n in fnotify)
        s.cancel()

    def test_no_warning_when_wake_system_on(self, clock, player, fpower,
                                            fnotify):
        s = make(player, fpower, clock,
                 sleep=SleepSpec(seconds=60, suspend_after=True),
                 wake=WakeSpec(time_spec="+2h", wake_system=True))
        s.start()
        assert not any("suspend without wake" in n[0].lower()
                       for n in fnotify)
        s.cancel()

    def test_no_warning_without_wake_at_all(self, clock, player, fpower,
                                            fnotify):
        # Sleep-only with suspend is a legitimate "put me to bed" use.
        s = make(player, fpower, clock,
                 sleep=SleepSpec(seconds=60, suspend_after=True))
        s.start()
        assert not any("suspend without wake" in n[0].lower()
                       for n in fnotify)
        s.cancel()


class TestRefuseSuspendOnFailedRtc:
    def test_stays_awake_when_rtc_not_programmed(self, clock, player,
                                                 fpower, fnotify):
        fpower.backend = None  # scheduling will fail
        s = make(player, fpower, clock,
                 sleep=SleepSpec(seconds=10, suspend_after=True),
                 wake=WakeSpec(time_spec="+2h", wake_system=True))
        s.start()
        tick_until(s, clock, lambda: s.phase == Phase.WAKE_WAIT)
        assert fpower.suspends == 0  # refused
        assert any(n[2] and "refused to suspend" in n[0].lower()
                   for n in fnotify)
        assert s.active  # alarm still armed, machine awake
        s.cancel()

    def test_suspends_normally_when_rtc_armed(self, clock, player, fpower):
        s = make(player, fpower, clock,
                 sleep=SleepSpec(seconds=10, suspend_after=True),
                 wake=WakeSpec(time_spec="+2h", wake_system=True,
                               wake_lead=60))
        s.start()
        tick_until(s, clock, lambda: s.phase == Phase.WAKE_WAIT)
        assert fpower.scheduled and fpower.suspends == 1
        s.cancel()

    def test_wake_disabled_suspend_honored(self, clock, player, fpower):
        # Explicit wake_system=False still suspends (with the arm-time
        # warning) — the user's explicit choice wins.
        s = make(player, fpower, clock,
                 sleep=SleepSpec(seconds=10, suspend_after=True),
                 wake=WakeSpec(time_spec="+2h", wake_system=False))
        s.start()
        tick_until(s, clock, lambda: s.phase == Phase.WAKE_WAIT)
        assert fpower.suspends == 1
        s.cancel()
