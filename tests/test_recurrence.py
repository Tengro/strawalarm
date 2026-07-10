"""Recurring alarms re-arm themselves, retry a failed firing once, and
surface failures as notifications instead of dying silently (risk R2)."""

from strawalarm.core import RETRY_DELAY, Phase, WakeSpec

from conftest import tick_until
from test_session import make

ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)


def recurring_spec(**kw):
    defaults = dict(time_spec="07:30", weekdays=ALL_DAYS, fade=0,
                    keep_awake=0, wake_lead=60, snooze=120)
    defaults.update(kw)
    return WakeSpec(**defaults)


class TestRearm:
    def test_rearm_after_completion(self, clock, player, fpower, fnotify):
        s = make(player, fpower, clock, wake=recurring_spec())
        s.start()
        first_wake = s.wake_at
        tick_until(s, clock, lambda: s.wake_at != first_wake, step=20)
        assert s.active and s.phase == Phase.WAKE_WAIT
        assert (s.wake_at - first_wake).days == 1
        assert any("re-armed" in n[0] for n in fnotify)
        assert len(fpower.scheduled) == 2  # RTC programmed for both days

    def test_cancel_stops_recurrence(self, clock, player, fpower):
        s = make(player, fpower, clock, wake=recurring_spec())
        s.start()
        s.cancel()
        assert s.phase == Phase.DONE
        assert not s.recurring or s.cancelled

    def test_one_shot_still_finishes(self, clock, player, fpower):
        s = make(player, fpower, clock,
                 wake=WakeSpec(time_spec="+60s", keep_awake=0,
                               wake_lead=30))
        s.start()
        tick_until(s, clock, lambda: not s.active)
        assert s.phase == Phase.DONE


class TestRetry:
    def test_transient_failure_retries_once_then_fires(self, clock, player,
                                                       fpower):
        s = make(player, fpower, clock,
                 wake=recurring_spec(playlist="Morning"))
        s.start()
        real_find = player.find_playlist
        player.find_playlist = lambda q: (_ for _ in ()).throw(
            LookupError("player mid-restart"))
        tick_until(s, clock, lambda: s.phase == Phase.RETRY, step=20)
        player.find_playlist = real_find  # transient issue resolved
        clock.advance(RETRY_DELAY + 1)
        s.tick()
        assert player.called("activate")  # alarm fired on retry
        assert s.active  # and recurrence lives on

    def test_persistent_failure_notifies_and_rearms_next_day(
            self, clock, player, fpower, fnotify):
        s = make(player, fpower, clock,
                 wake=recurring_spec(playlist="Morning"))
        s.start()
        first_wake = s.wake_at
        player.find_playlist = lambda q: (_ for _ in ()).throw(
            LookupError("playlist gone"))
        tick_until(s, clock, lambda: s.phase == Phase.RETRY, step=20)
        clock.advance(RETRY_DELAY + 1)
        s.tick()  # retry also fails
        assert s.phase == Phase.WAKE_WAIT  # recurrence survived
        assert s.wake_at > first_wake
        assert any(n[2] and "failed" in n[0] for n in fnotify)  # critical

    def test_one_shot_failure_is_terminal_and_critical(self, clock, player,
                                                       fpower, fnotify):
        s = make(player, fpower, clock,
                 wake=WakeSpec(time_spec="+60s", playlist="Nonexistent",
                               wake_lead=30, keep_awake=0))
        s.start()
        tick_until(s, clock, lambda: not s.active)
        assert s.phase == Phase.ERROR
        assert any(n[2] for n in fnotify)


class TestEventNotifications:
    def test_alarm_and_snooze_notify(self, clock, player, fpower, fnotify):
        s = make(player, fpower, clock,
                 wake=WakeSpec(time_spec="+60s", playlist="Morning",
                               keep_awake=3600, wake_lead=30, snooze=120))
        s.start()
        tick_until(s, clock, lambda: s.phase == Phase.KEEP_AWAKE)
        assert any("Wake up" in n[0] for n in fnotify)
        s.snooze()
        assert any("Snoozed" in n[0] for n in fnotify)
        s.cancel()

    def test_no_backend_warning_notifies_critical(self, clock, player,
                                                  fpower, fnotify):
        fpower.backend = None
        s = make(player, fpower, clock,
                 wake=WakeSpec(time_spec="+600s"))
        s.start()
        assert any(n[2] and "wake-from-suspend" in n[0] for n in fnotify)
        s.cancel()
