"""Timer/alarm engine shared by the CLI and the GUI.

A Session is a small state machine driven by tick() every ~250 ms, so a
GUI can run it from an event-loop timer and a CLI from a sleep loop.

Phases:
  SLEEP_WAIT -> FADE_OUT -> (stop) -> WAKE_WAIT -> WAIT_PLAYER
             -> FADE_IN -> DONE
Any subset applies depending on whether sleep and/or wake are configured.
"""

from __future__ import annotations

import datetime as dt
import enum
import re
import time
from dataclasses import dataclass

from .mpris import Player

DEFAULT_ALARM_VOLUME = 0.5
LAUNCH_TIMEOUT = 30
LAUNCH_GRACE = 3  # seconds for a freshly started player to settle


# ---------- parsing helpers ----------

def parse_duration(s: str) -> int:
    """'1h30m', '90m', '45s', '01:30:00', bare number = minutes. -> seconds"""
    s = s.strip().lower()
    if re.fullmatch(r"\d{1,3}:\d{2}(:\d{2})?", s):
        parts = [int(p) for p in s.split(":")]
        if len(parts) == 2:
            parts.append(0)
        h, m, sec = parts
        return h * 3600 + m * 60 + sec
    if re.fullmatch(r"\d+", s):
        return int(s) * 60
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", s)
    if not m or not any(m.groups()):
        raise ValueError(f'Cannot parse duration "{s}" '
                         '(try 1h30m, 90m, 45s, 01:30:00, or minutes).')
    h, mi, sec = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + sec


def parse_wake_time(s: str, base: dt.datetime | None = None) -> dt.datetime:
    """'07:30', '07:30:00' -> next occurrence; '+1h30m' -> relative."""
    base = base or dt.datetime.now()
    s = s.strip()
    if s.startswith("+"):
        return base + dt.timedelta(seconds=parse_duration(s[1:]))
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if not m:
        raise ValueError(f'Cannot parse time "{s}" '
                         '(try 07:30, 07:30:00, or +1h30m).')
    h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    target = base.replace(hour=h, minute=mi, second=sec, microsecond=0)
    if target <= base:
        target += dt.timedelta(days=1)
    return target


def fmt_delta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


# ---------- specs ----------

@dataclass
class SleepSpec:
    seconds: int | None = None   # duration mode
    tracks: int | None = None    # ... or track-count mode
    fade: int = 0                # fade-out seconds (0 = off)
    pause: bool = False          # pause instead of stop


@dataclass
class WakeSpec:
    time_spec: str               # "07:30", "07:30:00" or "+8h"
    playlist: str | None = None  # name of an open playlist, or None = resume
    volume: int | None = None    # target percent, None = keep player volume
    fade: int = 0                # fade-in seconds (0 = off)


class Phase(enum.Enum):
    IDLE = "idle"
    SLEEP_WAIT = "sleep_wait"
    FADE_OUT = "fade_out"
    WAKE_WAIT = "wake_wait"
    WAIT_PLAYER = "wait_player"
    FADE_IN = "fade_in"
    DONE = "done"
    ERROR = "error"


class Session:
    def __init__(self, player: Player, sleep: SleepSpec | None = None,
                 wake: WakeSpec | None = None, log=None):
        if not sleep and not wake:
            raise ValueError("Session needs a sleep spec, a wake spec, or both.")
        self.player = player
        self.sleep = sleep
        self.wake = wake
        self.log = log or (lambda msg: None)
        self.phase = Phase.IDLE
        self.error: str | None = None
        self.wake_at: dt.datetime | None = None
        self._desktop_entry = None
        self._initial_volume = None
        self._prefade_volume = None
        self._fade_deadline = 0.0
        self._fade_start = 0.0
        self._fade_from = 0.0
        self._fade_target = 0.0
        self._stop_deadline = 0.0
        self._track_changes = 0
        self._last_trackid = None
        self._next_poll = 0.0
        self._launch_deadline = 0.0
        self._player_ready_at = 0.0

    # ---------- lifecycle ----------

    def start(self):
        if self.player.running():
            self._desktop_entry = self.player.desktop_entry()
            self._initial_volume = self.player.volume()
        if self.sleep:
            if not self.player.running():
                raise RuntimeError(
                    f"{self.player.name} is not running, nothing to stop.")
            self._prefade_volume = self.player.volume()
            if self.sleep.tracks:
                self._last_trackid = self.player.trackid()
                self.log(f"Sleep timer: stopping after {self.sleep.tracks} "
                         "track(s), counting the current one.")
            else:
                secs = self.sleep.seconds
                self._stop_deadline = time.time() + secs
                self.log(f"Sleep timer: stopping in {fmt_delta(secs)} "
                         f"(at {dt.datetime.now() + dt.timedelta(seconds=secs):%H:%M:%S}).")
            if self.sleep.fade:
                self.log(f"Will fade out over the last {self.sleep.fade}s.")
            self.phase = Phase.SLEEP_WAIT
        else:
            self._arm_wake()

    def cancel(self):
        if self.phase in (Phase.FADE_OUT, Phase.FADE_IN) \
                and self._prefade_volume is not None:
            self.player.set_volume(self._prefade_volume)
        self.phase = Phase.DONE
        self.log("Cancelled.")

    def tick(self):
        """Advance the state machine. Safe to call every 100-1000 ms."""
        try:
            self._tick()
        except Exception as e:  # surface, don't crash the frontend loop
            self.error = str(e)
            self.phase = Phase.ERROR
            self.log(f"Error: {e}")

    @property
    def active(self):
        return self.phase not in (Phase.IDLE, Phase.DONE, Phase.ERROR)

    # ---------- status for frontends ----------

    def status(self) -> tuple[str, float | None]:
        """(human text, countdown seconds or None)"""
        now = time.time()
        if self.phase == Phase.SLEEP_WAIT and self.sleep.tracks:
            n = self.sleep.tracks
            return (f"Playing track {self._track_changes + 1} of {n}, "
                    "then stopping", None)
        if self.phase == Phase.SLEEP_WAIT:
            return ("Stopping in", self._stop_deadline - now)
        if self.phase == Phase.FADE_OUT:
            return ("Fading out", self._fade_deadline - now)
        if self.phase == Phase.WAKE_WAIT:
            return ("Alarm in", self.wake_at.timestamp() - now)
        if self.phase == Phase.WAIT_PLAYER:
            return ("Waiting for player to start", None)
        if self.phase == Phase.FADE_IN:
            return ("Fading in", self._fade_deadline - now)
        if self.phase == Phase.ERROR:
            return (f"Error: {self.error}", None)
        if self.phase == Phase.DONE:
            return ("Done", None)
        return ("Idle", None)

    # ---------- internals ----------

    def _tick(self):
        now = time.time()
        if self.phase == Phase.SLEEP_WAIT:
            if self.sleep.tracks:
                self._tick_tracks(now)
            else:
                self._tick_duration(now)
        elif self.phase == Phase.FADE_OUT:
            self._tick_fade_out(now)
        elif self.phase == Phase.WAKE_WAIT:
            if now >= self.wake_at.timestamp():
                self._reach_wake_time(now)
        elif self.phase == Phase.WAIT_PLAYER:
            self._tick_wait_player(now)
        elif self.phase == Phase.FADE_IN:
            self._tick_fade_in(now)

    def _tick_duration(self, now):
        fade = min(self.sleep.fade, self.sleep.seconds)
        if fade and now >= self._stop_deadline - fade:
            self._begin_fade_out(now, self._stop_deadline)
        elif now >= self._stop_deadline:
            self._do_stop()

    def _tick_tracks(self, now):
        if now < self._next_poll:
            return
        self._next_poll = now + 1.0
        tid = self.player.trackid()
        if tid is None:  # player gone
            self.log("Player disappeared; sleep timer ends here.")
            self._after_stop()
            return
        if tid != self._last_trackid:
            self._last_trackid = tid
            self._track_changes += 1
            n = self.sleep.tracks
            if self._track_changes >= n:
                self._do_stop()
                return
            self.log(f"Track {self._track_changes + 1}/{n} started.")
        if self.sleep.fade and self._track_changes == self.sleep.tracks - 1:
            pos, length = self.player.position_s(), self.player.length_s()
            if pos is not None and length:
                remaining = length - pos
                if 0 < remaining <= self.sleep.fade:
                    self._begin_fade_out(now, now + remaining)

    def _begin_fade_out(self, now, deadline):
        self._prefade_volume = self.player.volume() or self._prefade_volume
        self._fade_start = now
        self._fade_from = self._prefade_volume or 0.0
        self._fade_deadline = deadline
        self.phase = Phase.FADE_OUT
        self.log(f"Fading out over {fmt_delta(deadline - now)}...")

    def _tick_fade_out(self, now):
        if now >= self._fade_deadline:
            self._do_stop()
            return
        span = self._fade_deadline - self._fade_start
        k = max(0.0, (self._fade_deadline - now) / span) if span else 0.0
        self.player.set_volume(self._fade_from * k)

    def _do_stop(self):
        if self.sleep.pause:
            self.player.pause()
        else:
            self.player.stop()
        if self._prefade_volume is not None:
            self.player.set_volume(self._prefade_volume)
        self.log(f"Playback {'paused' if self.sleep.pause else 'stopped'}.")
        self._after_stop()

    def _after_stop(self):
        if self.wake:
            self._arm_wake()
        else:
            self.phase = Phase.DONE

    def _arm_wake(self):
        self.wake_at = parse_wake_time(self.wake.time_spec)
        delta = self.wake_at.timestamp() - time.time()
        what = f'playlist "{self.wake.playlist}"' if self.wake.playlist \
            else "resume playback"
        self.log(f"Alarm set for {self.wake_at:%Y-%m-%d %H:%M:%S} "
                 f"(in {fmt_delta(delta)}) — {what}.")
        self.phase = Phase.WAKE_WAIT

    def _reach_wake_time(self, now):
        if self.player.running():
            self._begin_alarm(now)
            return
        self.log(f"{self.player.name} is not running, launching it...")
        if not self.player.launch(self._desktop_entry):
            raise RuntimeError(
                f"Could not launch {self.player.name} "
                "(no desktop entry or binary found).")
        self._launch_deadline = now + LAUNCH_TIMEOUT
        self._player_ready_at = 0.0
        self.phase = Phase.WAIT_PLAYER

    def _tick_wait_player(self, now):
        if self.player.running():
            if not self._player_ready_at:
                self._player_ready_at = now + LAUNCH_GRACE
            if now >= self._player_ready_at:
                self._begin_alarm(now)
        elif now >= self._launch_deadline:
            raise RuntimeError(
                f"{self.player.name} did not appear on D-Bus "
                f"within {LAUNCH_TIMEOUT}s.")

    def _alarm_target_volume(self) -> float:
        if self.wake.volume is not None:
            return max(0.0, min(1.0, self.wake.volume / 100))
        for v in (self._initial_volume, self.player.volume()):
            if v:
                return v
        return DEFAULT_ALARM_VOLUME

    def _begin_alarm(self, now):
        target = self._alarm_target_volume()
        fade = self.wake.fade
        self.player.set_volume(0.0 if fade else target)
        if self.wake.playlist and self.player.has_playlists():
            path, name = self.player.find_playlist(self.wake.playlist)
            self.player.activate_playlist(path)
            self.log(f'Wake up! Playing "{name}".')
        else:
            self.player.play()
            self.log("Wake up! Resuming playback.")
        if self.player.status() != "Playing":
            self.player.play()
        if fade:
            self._fade_start = now
            self._fade_deadline = now + fade
            self._fade_target = target
            self._prefade_volume = target  # restore point if cancelled
            self.phase = Phase.FADE_IN
            self.log(f"Fading in to {round(target * 100)}% over {fade}s...")
        else:
            self.phase = Phase.DONE

    def _tick_fade_in(self, now):
        span = self._fade_deadline - self._fade_start
        k = min(1.0, (now - self._fade_start) / span) if span else 1.0
        self.player.set_volume(self._fade_target * k)
        if k >= 1.0:
            self.phase = Phase.DONE
            self.log("Alarm volume reached. Good morning!")


def run_blocking(session: Session, interval: float = 0.25):
    """Drive a session to completion (CLI mode)."""
    session.start()
    while session.active:
        time.sleep(interval)
        session.tick()
    return session.phase == Phase.DONE
