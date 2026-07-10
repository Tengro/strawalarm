"""Armed-state persistence and recovery planning (risk R1)."""

import json
import os

from strawalarm import state
from strawalarm.core import Phase, SleepSpec, WakeSpec

from conftest import tick_until
from test_session import make


def dead_pid(data):
    data["pid"] = 999_999_999  # not a real process
    return data


class TestLifecycle:
    def test_arming_writes_state_file(self, clock, player, fpower,
                                      state_home):
        s = make(player, fpower, clock,
                 wake=WakeSpec(time_spec="+600s", wake_lead=60))
        s.start()
        data = state.load()
        assert data["player"] == "fakeplayer"
        assert data["phase"] == "wake_wait"
        assert abs(data["wake_at"] - s.wake_at.timestamp()) < 1

    def test_cancel_clears(self, clock, player, fpower):
        s = make(player, fpower, clock, wake=WakeSpec(time_spec="+600s"))
        s.start()
        s.cancel()
        assert state.load() is None

    def test_completion_clears(self, clock, player, fpower):
        s = make(player, fpower, clock, sleep=SleepSpec(seconds=5))
        s.start()
        tick_until(s, clock, lambda: not s.active)
        assert state.load() is None

    def test_error_clears(self, clock, player, fpower):
        s = make(player, fpower, clock,
                 wake=WakeSpec(time_spec="+60s", playlist="Nonexistent",
                               wake_lead=30, keep_awake=0))
        s.start()
        tick_until(s, clock, lambda: not s.active)
        assert s.phase == Phase.ERROR
        assert state.load() is None

    def test_recurring_rearm_updates_file(self, clock, player, fpower):
        s = make(player, fpower, clock,
                 wake=WakeSpec(time_spec="07:30", weekdays=(0, 1, 2, 3, 4,
                                                            5, 6),
                               keep_awake=0, wake_lead=60))
        s.start()
        first = state.load()["wake_at"]
        tick_until(s, clock,
                   lambda: state.load()["wake_at"] != first, step=20)
        assert state.load()["wake_at"] > first


class TestPlanRecovery:
    def wake_state(self, clock, player, fpower, **wake_kw):
        defaults = dict(time_spec="+600s", wake_lead=60)
        defaults.update(wake_kw)
        s = make(player, fpower, clock, wake=WakeSpec(**defaults))
        s.start()
        data = state.load()
        s.cancel()  # simulate crash: restore file after cancel wiped it
        return data

    def test_future_wake_rearm_pins_exact_time(self, clock, player,
                                               fpower):
        data = dead_pid(self.wake_state(clock, player, fpower))
        plan = state.plan_recovery(data, now=clock.t + 60)
        assert plan["kind"] == "rearm"
        pinned = plan["wake"].time_spec
        assert pinned.count(":") == 2  # resolved HH:MM:SS, not "+600s"
        assert "alarm at" in plan["message"]

    def test_past_oneshot_is_missed(self, clock, player, fpower):
        data = dead_pid(self.wake_state(clock, player, fpower))
        plan = state.plan_recovery(data, now=clock.t + 700)
        assert plan["kind"] == "missed"
        assert "missed" in plan["message"]

    def test_past_recurring_rearms_with_missed_flag(self, clock, player,
                                                    fpower):
        data = dead_pid(self.wake_state(
            clock, player, fpower, time_spec="07:30",
            weekdays=(0, 1, 2, 3, 4, 5, 6)))
        plan = state.plan_recovery(data, now=data["wake_at"] + 3600)
        assert plan["kind"] == "rearm" and plan["missed"]

    def test_sleep_phase_recovers_remaining_time(self, clock, player,
                                                 fpower):
        s = make(player, fpower, clock,
                 sleep=SleepSpec(seconds=7200),
                 wake=WakeSpec(time_spec="07:30"))
        s.start()
        data = dead_pid(state.load())
        s.cancel()
        plan = state.plan_recovery(data, now=clock.t + 3600)
        assert plan["kind"] == "rearm"
        assert abs(plan["sleep"].seconds - 3600) < 5
        assert plan["wake"] is not None

    def test_expired_sleep_no_wake_is_stale(self, clock, player, fpower):
        s = make(player, fpower, clock, sleep=SleepSpec(seconds=100))
        s.start()
        data = dead_pid(state.load())
        s.cancel()
        plan = state.plan_recovery(data, now=clock.t + 7200)
        assert plan["kind"] == "stale"

    def test_living_owner_blocks_recovery(self, clock, player, fpower):
        data = self.wake_state(clock, player, fpower)
        data["pid"] = 1  # init: definitely alive, definitely not ours
        assert state.plan_recovery(data, now=clock.t)["kind"] == "active"

    def test_own_pid_not_treated_as_owner(self, clock, player, fpower):
        data = self.wake_state(clock, player, fpower)
        data["pid"] = os.getpid()
        assert state.plan_recovery(data, now=clock.t)["kind"] == "rearm"

    def test_weekdays_survive_json_roundtrip(self, clock, player, fpower):
        data = dead_pid(self.wake_state(
            clock, player, fpower, time_spec="07:30", weekdays=(0, 2, 4)))
        data = json.loads(json.dumps(data))  # tuples become lists
        plan = state.plan_recovery(data, now=clock.t)
        assert plan["wake"].weekdays == (0, 2, 4)


class TestRobustness:
    def test_corrupt_file_loads_as_none(self, state_home):
        path = state.armed_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{not json")
        assert state.load() is None

    def test_version_mismatch_ignored(self, state_home):
        path = state.armed_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"version": 999}, f)
        assert state.load() is None
