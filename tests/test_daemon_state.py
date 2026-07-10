"""The daemon's pure plumbing: spec wire format and state snapshots.
(The daemon process itself needs Qt and is exercised live, not in CI.)"""

import json

from strawalarm import state
from strawalarm.core import (Phase, SleepSpec, WakeSpec, session_state)

from conftest import tick_until
from test_session import make


class TestSpecsWireFormat:
    def test_roundtrip(self):
        sleep = SleepSpec(seconds=5400, fade=30, suspend_after=True)
        wake = WakeSpec(time_spec="07:30", playlist="Morning", volume=40,
                        fade=60, weekdays=(0, 2, 4))
        raw = json.dumps(state.specs_to_dict("strawberry", sleep, wake))
        player, s2, w2 = state.specs_from_dict(json.loads(raw))
        assert player == "strawberry"
        assert s2 == sleep
        assert w2 == wake
        assert isinstance(w2.weekdays, tuple)

    def test_partial_specs(self):
        raw = state.specs_to_dict("x", None, WakeSpec(time_spec="+8h"))
        player, sleep, wake = state.specs_from_dict(raw)
        assert sleep is None and wake.time_spec == "+8h"

    def test_unknown_fields_rejected(self):
        import pytest
        with pytest.raises(TypeError):
            state.specs_from_dict({"sleep": {"bogus_field": 1}})


class TestSessionState:
    def test_idle(self):
        st = session_state(None)
        assert st == {"active": False, "phase": "idle", "text": "Idle.",
                      "countdown": None, "snoozable": False,
                      "recurring": False}

    def test_active_wake(self, clock, player, fpower):
        s = make(player, fpower, clock,
                 wake=WakeSpec(time_spec="+600s", wake_lead=60))
        s.start()
        st = session_state(s)
        assert st["active"] and st["phase"] == "wake_wait"
        assert 0 < st["countdown"] <= 600
        assert not st["snoozable"]
        s.cancel()

    def test_snoozable_during_alarm(self, clock, player, fpower):
        s = make(player, fpower, clock,
                 wake=WakeSpec(time_spec="+2s", keep_awake=600,
                               wake_lead=1))
        s.start()
        tick_until(s, clock, lambda: s.phase == Phase.KEEP_AWAKE)
        assert session_state(s)["snoozable"]
        s.cancel()

    def test_error_text(self, clock, player, fpower):
        s = make(player, fpower, clock,
                 wake=WakeSpec(time_spec="+2s", playlist="Nonexistent",
                               wake_lead=1, keep_awake=0))
        s.start()
        tick_until(s, clock, lambda: not s.active)
        st = session_state(s)
        assert not st["active"] and "Error" in st["text"]

    def test_json_serializable(self, clock, player, fpower):
        s = make(player, fpower, clock,
                 wake=WakeSpec(time_spec="+600s", wake_lead=60))
        s.start()
        json.dumps(session_state(s))  # must not raise
        s.cancel()
