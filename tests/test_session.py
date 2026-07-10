"""State-machine tests for core.Session with fully faked player, power
layer and clock. Every test runs in milliseconds."""

import pytest

from strawalarm.core import (LAUNCH_TIMEOUT, Phase, Session, SleepSpec,
                             WakeSpec, run_blocking)

from conftest import tick_until


def make(player, fpower, clock, sleep=None, wake=None):
    logs = []
    session = Session(player, sleep=sleep, wake=wake, log=logs.append)
    session.logs = logs
    return session


# ---------- sleep timer ----------

class TestSleepDuration:
    def test_stops_at_deadline(self, clock, player, fpower):
        s = make(player, fpower, clock, sleep=SleepSpec(seconds=10))
        s.start()
        assert s.phase == Phase.SLEEP_WAIT
        assert s.inhibitor.held
        tick_until(s, clock, lambda: not s.active)
        assert s.phase == Phase.DONE
        assert player.called("stop")
        assert player.status() == "Stopped"
        assert not s.inhibitor.held

    def test_pause_mode_keeps_position(self, clock, player, fpower):
        s = make(player, fpower, clock,
                 sleep=SleepSpec(seconds=5, pause=True))
        s.start()
        tick_until(s, clock, lambda: not s.active)
        assert player.called("pause") and not player.called("stop")

    def test_fade_ramps_down_and_restores_volume(self, clock, player,
                                                 fpower):
        s = make(player, fpower, clock,
                 sleep=SleepSpec(seconds=30, fade=10))
        s.start()
        tick_until(s, clock, lambda: s.phase == Phase.FADE_OUT)
        tick_until(s, clock, lambda: not s.active)
        fade_vols = [c[1] for c in player.calls if c[0] == "set_volume"]
        ramp = fade_vols[:-1]  # last call restores the original volume
        assert ramp and all(a >= b for a, b in zip(ramp, ramp[1:]))
        assert ramp[-1] < 0.1
        assert player.volume() == 0.5  # restored after stop
        assert player.called("stop")

    def test_cancel_during_fade_restores_volume(self, clock, player,
                                                fpower):
        s = make(player, fpower, clock,
                 sleep=SleepSpec(seconds=30, fade=20))
        s.start()
        tick_until(s, clock, lambda: s.phase == Phase.FADE_OUT)
        tick_until(s, clock, lambda: player.volume() < 0.3)
        s.cancel()
        assert s.cancelled and s.phase == Phase.DONE
        assert player.volume() == 0.5
        assert not s.inhibitor.held

    def test_not_running_player_refuses_start(self, clock, player, fpower):
        player._running = False
        s = make(player, fpower, clock, sleep=SleepSpec(seconds=5))
        with pytest.raises(RuntimeError):
            s.start()


class TestSleepTracks:
    def test_stops_after_n_track_changes(self, clock, player, fpower):
        s = make(player, fpower, clock, sleep=SleepSpec(tracks=2))
        s.start()
        clock.advance(2)
        s.tick()
        player._trackid = "/track/2"  # track 1 finished
        tick_until(s, clock, lambda: s._track_changes == 1, step=1)
        assert s.active
        player._trackid = "/track/3"  # track 2 finished
        tick_until(s, clock, lambda: not s.active, step=1)
        assert player.called("stop")

    def test_player_disappearing_ends_timer(self, clock, player, fpower):
        s = make(player, fpower, clock, sleep=SleepSpec(tracks=5))
        s.start()
        player._running = False
        tick_until(s, clock, lambda: not s.active, step=1)
        assert s.phase == Phase.DONE
        assert not player.called("stop")  # nothing left to stop
        assert not s.inhibitor.held

    def test_fade_timed_to_final_track_end(self, clock, player, fpower):
        player._length = 200.0
        player._position = 100.0
        s = make(player, fpower, clock, sleep=SleepSpec(tracks=1, fade=10))
        s.start()
        clock.advance(2)
        s.tick()
        assert s.phase == Phase.SLEEP_WAIT  # 100s remaining > fade
        player._position = 195.0  # 5s remaining <= fade
        tick_until(s, clock, lambda: s.phase == Phase.FADE_OUT, step=1)
        tick_until(s, clock, lambda: not s.active)
        assert player.called("stop")
        assert any(c[1] < 0.1 for c in player.calls if c[0] == "set_volume")


# ---------- alarm ----------

def wake_spec(**kw):
    defaults = dict(time_spec="+600s", fade=0, keep_awake=0,
                    wake_lead=60, snooze=120)
    defaults.update(kw)
    return WakeSpec(**defaults)


class TestWake:
    def test_schedules_rtc_with_lead(self, clock, player, fpower):
        s = make(player, fpower, clock, wake=wake_spec())
        s.start()
        assert s.phase == Phase.WAKE_WAIT
        assert fpower.scheduled == [int(s.wake_at.timestamp()) - 60]

    def test_no_wake_system_skips_rtc(self, clock, player, fpower):
        s = make(player, fpower, clock, wake=wake_spec(wake_system=False))
        s.start()
        assert fpower.scheduled == []

    def test_warns_at_start_when_no_backend(self, clock, player, fpower):
        fpower.backend = None
        s = make(player, fpower, clock, wake=wake_spec())
        s.start()
        assert "Warning" in s.logs[0]

    def test_alarm_plays_playlist_at_volume(self, clock, player, fpower):
        s = make(player, fpower, clock,
                 wake=wake_spec(playlist="Morning", volume=30))
        s.start()
        tick_until(s, clock, lambda: not s.active)
        assert player.called("activate") == [("activate", "/pl/1")]
        assert player.volume() == 0.3
        assert player.status() == "Playing"

    def test_alarm_without_playlist_resumes(self, clock, player, fpower):
        player._status = "Stopped"
        s = make(player, fpower, clock, wake=wake_spec())
        s.start()
        tick_until(s, clock, lambda: not s.active)
        assert player.called("play") and not player.called("activate")

    def test_volume_defaults_when_player_muted(self, clock, player,
                                               fpower):
        player._volume = 0.0
        s = make(player, fpower, clock, wake=wake_spec())
        s.start()
        tick_until(s, clock, lambda: not s.active)
        assert player.volume() == 0.5  # DEFAULT_ALARM_VOLUME

    def test_fade_in_ramps_up(self, clock, player, fpower):
        s = make(player, fpower, clock,
                 wake=wake_spec(volume=80, fade=10))
        s.start()
        tick_until(s, clock, lambda: s.phase == Phase.FADE_IN)
        vols = [c[1] for c in player.calls if c[0] == "set_volume"]
        assert vols[0] == 0.0  # starts from silence
        tick_until(s, clock, lambda: not s.active)
        vols = [c[1] for c in player.calls if c[0] == "set_volume"]
        assert all(a <= b + 1e-6 for a, b in zip(vols, vols[1:]))
        assert player.volume() == 0.8

    def test_keep_awake_holds_then_releases(self, clock, player, fpower):
        s = make(player, fpower, clock, wake=wake_spec(keep_awake=300))
        s.start()
        tick_until(s, clock, lambda: s.phase == Phase.KEEP_AWAKE)
        assert s.inhibitor.held
        tick_until(s, clock, lambda: not s.active)
        assert s.phase == Phase.DONE
        assert not s.inhibitor.held

    def test_cancel_while_waiting_clears_rtc(self, clock, player, fpower):
        s = make(player, fpower, clock, wake=wake_spec())
        s.start()
        s.cancel()
        assert fpower.cleared == [42]
        assert s.cancelled


class TestCombined:
    def test_sleep_then_wake_then_suspend_order(self, clock, player,
                                                fpower):
        s = make(player, fpower, clock,
                 sleep=SleepSpec(seconds=10, suspend_after=True),
                 wake=wake_spec())
        s.start()
        tick_until(s, clock, lambda: s.phase == Phase.WAKE_WAIT)
        assert not s.inhibitor.held  # released before machine may sleep
        kinds = [e[0] for e in fpower.events]
        assert kinds.index("schedule") < kinds.index("suspend")
        tick_until(s, clock, lambda: not s.active)
        assert player.called("play")


class TestSnooze:
    def test_snooze_pauses_and_refires_resume_only(self, clock, player,
                                                   fpower):
        s = make(player, fpower, clock,
                 wake=wake_spec(playlist="Morning", keep_awake=3600,
                                snooze=120))
        s.start()
        tick_until(s, clock, lambda: s.phase == Phase.KEEP_AWAKE)
        assert s.snooze() is True
        assert s.phase == Phase.SNOOZE
        assert player.status() == "Paused"
        assert s.inhibitor.held  # stays held through the snooze
        tick_until(s, clock, lambda: s.phase == Phase.KEEP_AWAKE)
        assert player.status() == "Playing"
        assert len(player.called("activate")) == 1  # no playlist re-jump

    def test_snooze_refused_outside_alarm(self, clock, player, fpower):
        s = make(player, fpower, clock, wake=wake_spec())
        s.start()
        assert s.snooze() is False  # WAKE_WAIT is not snoozable


class TestWatchdog:
    def test_restarts_choked_player_max_three_times(self, clock, player,
                                                    fpower):
        s = make(player, fpower, clock, wake=wake_spec(keep_awake=3600))
        s.start()
        tick_until(s, clock, lambda: s.phase == Phase.KEEP_AWAKE)
        plays_before = len(player.called("play"))
        for _ in range(5):  # choke it repeatedly
            player._status = "Paused"
            clock.advance(4)
            s.tick()
        kicks = len(player.called("play")) - plays_before
        assert kicks == 3  # capped


class TestResume:
    def test_clock_jump_defers_alarm_for_settle(self, clock, player,
                                                fpower):
        s = make(player, fpower, clock, wake=wake_spec())
        s.start()
        clock.advance(1)
        s.tick()
        clock.advance(700)  # "suspend" straight past the alarm time
        s.tick()
        assert s.phase == Phase.WAKE_WAIT  # settling, not firing
        clock.advance(5)
        s.tick()
        assert s.phase == Phase.WAKE_WAIT
        clock.advance(10)
        s.tick()
        assert not s.active  # settle over, alarm fired

    def test_player_relaunched_at_wake(self, clock, player, fpower):
        s = make(player, fpower, clock, wake=wake_spec())
        s.start()
        player._running = False
        tick_until(s, clock, lambda: s.phase == Phase.WAIT_PLAYER)
        assert player.launched
        player._running = True
        tick_until(s, clock, lambda: not s.active)
        assert player.called("play")

    def test_launch_timeout_errors_and_cleans_up(self, clock, player,
                                                 fpower):
        s = make(player, fpower, clock, wake=wake_spec())
        s.start()
        player._running = False
        player.launch = lambda hint=None: True  # launches, never appears
        tick_until(s, clock, lambda: s.phase == Phase.WAIT_PLAYER)
        clock.advance(LAUNCH_TIMEOUT + 5)
        s.tick()
        assert s.phase == Phase.ERROR
        assert "did not appear" in s.error
        assert not s.inhibitor.held


class TestErrors:
    def test_missing_playlist_errors_and_releases(self, clock, player,
                                                  fpower):
        s = make(player, fpower, clock,
                 wake=wake_spec(playlist="Nonexistent"))
        s.start()
        tick_until(s, clock, lambda: not s.active)
        assert s.phase == Phase.ERROR
        assert "Nonexistent" in s.error
        assert not s.inhibitor.held

    def test_needs_some_spec(self, clock, player, fpower):
        with pytest.raises(ValueError):
            Session(player)


class TestRunBlocking:
    def test_drives_to_completion(self, clock, player, fpower):
        s = make(player, fpower, clock, sleep=SleepSpec(seconds=5))
        assert run_blocking(s) is True
        assert player.called("stop")
