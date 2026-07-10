"""Armed-session persistence: the alarm survives process death
(risk R1). Session saves its spec and deadlines here on every arm;
frontends offer recovery on launch. This file format is deliberately
the future daemon's state format (ROADMAP 2.3).
"""

import dataclasses
import datetime as dt
import json
import os
import time

STATE_VERSION = 1
SLEEP_PHASES = ("sleep_wait", "fade_out")


def specs_to_dict(player_name, sleep, wake) -> dict:
    """Wire/disk format for a session spec (D-Bus arm calls, armed.json)."""
    return {"player": player_name,
            "sleep": dataclasses.asdict(sleep) if sleep else None,
            "wake": dataclasses.asdict(wake) if wake else None}


def specs_from_dict(data: dict):
    """-> (player_name, SleepSpec|None, WakeSpec|None). Raises TypeError
    on unknown fields, ValueError on garbage."""
    from .core import SleepSpec, WakeSpec  # avoid module cycle
    sleep = SleepSpec(**data["sleep"]) if data.get("sleep") else None
    wake = WakeSpec(**data["wake"]) if data.get("wake") else None
    if wake and wake.weekdays:
        wake.weekdays = tuple(wake.weekdays)  # JSON turned it into a list
    return data.get("player"), sleep, wake


def armed_path() -> str:
    state = os.environ.get("XDG_STATE_HOME",
                           os.path.expanduser("~/.local/state"))
    return os.path.join(state, "strawalarm", "armed.json")


def save(session):
    data = {
        "version": STATE_VERSION,
        "pid": os.getpid(),
        "saved_at": time.time(),
        "player": session.player.name,
        "sleep": dataclasses.asdict(session.sleep) if session.sleep
        else None,
        "wake": dataclasses.asdict(session.wake) if session.wake else None,
        "phase": session.phase.value,
        "stop_deadline": session._stop_deadline or None,
        "wake_at": session.wake_at.timestamp() if session.wake_at else None,
    }
    path = armed_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass  # persistence is best-effort; the session still runs


def load() -> dict | None:
    try:
        with open(armed_path()) as f:
            data = json.load(f)
        return data if data.get("version") == STATE_VERSION else None
    except (OSError, ValueError):
        return None


def clear():
    try:
        os.unlink(armed_path())
    except OSError:
        pass


def owner_alive(data: dict) -> bool:
    """True if the process that saved this state is still running
    (then it owns the session — don't offer recovery)."""
    pid = data.get("pid")
    if not pid or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def plan_recovery(data: dict, now: float | None = None) -> dict:
    """Turn a loaded armed.json into a recovery decision.

    kinds: "active" (owner still runs), "stale" (nothing recoverable),
    "missed" (one-shot alarm time passed while nothing ran),
    "rearm" (specs ready to start; 'sleep'/'wake' keys, 'message',
    and 'missed' flag when a recurring occurrence was skipped)."""
    from .core import fmt_delta  # avoid module cycle
    now = now or time.time()
    if owner_alive(data):
        return {"kind": "active"}
    _, sleep, wake = specs_from_dict(data)
    stop_deadline = data.get("stop_deadline")
    wake_at = data.get("wake_at")

    if (data.get("phase") in SLEEP_PHASES and sleep and sleep.seconds
            and stop_deadline and stop_deadline > now):
        remaining = max(1, int(stop_deadline - now))
        sleep = dataclasses.replace(sleep, seconds=remaining)
        return {"kind": "rearm", "sleep": sleep, "wake": wake,
                "player": data.get("player"),
                "message": f"sleep timer with {fmt_delta(remaining)} left"
                + (" plus the alarm" if wake else "")}
    if wake:
        if wake_at and wake_at > now:
            pinned = dt.datetime.fromtimestamp(wake_at)
            wake = dataclasses.replace(wake,
                                       time_spec=pinned.strftime("%H:%M:%S"))
            return {"kind": "rearm", "sleep": None, "wake": wake,
                    "player": data.get("player"),
                    "message": f"alarm at {pinned:%a %H:%M:%S} "
                               f"(in {fmt_delta(wake_at - now)})"}
        if wake.weekdays:
            missed = bool(wake_at and wake_at <= now)
            return {"kind": "rearm", "sleep": None, "wake": wake,
                    "player": data.get("player"), "missed": missed,
                    "message": "recurring alarm"
                    + (" (one occurrence was missed)" if missed else "")}
        if wake_at and wake_at <= now:
            when = dt.datetime.fromtimestamp(wake_at)
            return {"kind": "missed",
                    "message": f"The alarm set for {when:%a %H:%M} was "
                               "missed while strawalarm wasn't running."}
    return {"kind": "stale"}
