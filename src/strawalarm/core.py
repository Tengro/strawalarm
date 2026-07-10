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

from . import notify, power, state
from .mpris import Player

DEFAULT_ALARM_VOLUME = 0.5
LAUNCH_TIMEOUT = 30
LAUNCH_GRACE = 3       # seconds for a freshly started player to settle
DEFAULT_WAKE_LEAD = 180    # wake the system this many seconds early
DEFAULT_KEEP_AWAKE = 1800  # hold the sleep-block this long after the alarm
RESUME_SETTLE = 12         # audio-stack grace after a suspend/resume jump
WATCHDOG_WINDOW = 45       # re-play window after the alarm starts
WATCHDOG_KICKS = 3         # max automatic playback restarts
RETRY_DELAY = 30           # recurring alarm: retry a failed firing once


# ---------- parsing helpers ----------

def parse_duration(s: str) -> int:
    """'1h30m', '90m', '45s', '01:30:00', bare number = minutes. -> seconds"""
    s = s.strip().lower().replace(" ", "")
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
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    return (f"{d}d " if d else "") + f"{h}:{m:02d}:{s:02d}"


WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def crosses_dst(start: dt.datetime, end: dt.datetime) -> bool:
    """True if a DST transition lies between the two local datetimes —
    wall-clock arithmetic is then one hour off from elapsed time."""
    return start.astimezone().utcoffset() != end.astimezone().utcoffset()


def next_occurrence(spec: str, weekdays=None,
                    base: dt.datetime | None = None) -> dt.datetime:
    """Next datetime matching the time spec, optionally constrained to a
    set of weekdays (0 = Monday). Relative specs ('+8h') ignore weekdays."""
    t = parse_wake_time(spec, base)
    if weekdays and not spec.strip().startswith("+"):
        for _ in range(7):
            if t.weekday() in weekdays:
                break
            t += dt.timedelta(days=1)
    return t


# ---------- specs ----------

@dataclass
class SleepSpec:
    seconds: int | None = None   # duration mode
    tracks: int | None = None    # ... or track-count mode
    fade: int = 0                # fade-out seconds (0 = off)
    pause: bool = False          # pause instead of stop
    suspend_after: bool = False  # suspend the machine after stopping


@dataclass
class WakeSpec:
    time_spec: str               # "07:30", "07:30:00" or "+8h"
    playlist: str | None = None  # name of an open playlist, or None = resume
    volume: int | None = None    # target percent, None = keep player volume
    fade: int = 0                # fade-in seconds (0 = off)
    wake_system: bool = True     # program an RTC wake before the alarm
    wake_lead: int = DEFAULT_WAKE_LEAD    # seconds before alarm to wake
    keep_awake: int = DEFAULT_KEEP_AWAKE  # post-alarm sleep-block (0 = off)
    snooze: int = 600            # snooze interval in seconds
    weekdays: tuple | None = None  # restrict "at" alarms to weekdays (0=Mon)


class Phase(enum.Enum):
    IDLE = "idle"
    SLEEP_WAIT = "sleep_wait"
    FADE_OUT = "fade_out"
    WAKE_WAIT = "wake_wait"
    WAIT_PLAYER = "wait_player"
    FADE_IN = "fade_in"
    KEEP_AWAKE = "keep_awake"
    SNOOZE = "snooze"
    RETRY = "retry"
    DONE = "done"
    ERROR = "error"


SNOOZABLE = (Phase.FADE_IN, Phase.KEEP_AWAKE)


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
        self.inhibitor = power.Inhibitor()
        self._wake_cookie = None
        self._wake_scheduled = False
        self._keep_awake_deadline = 0.0
        self._last_tick = 0.0
        self._settle_until = 0.0
        self._watchdog_until = 0.0
        self._watchdog_next = 0.0
        self._watchdog_kicks = 0
        self._snooze_deadline = 0.0
        self.cancelled = False
        # A recurring alarm-only session re-arms itself after each firing
        # and never dies to a single bad morning.
        self.recurring = bool(wake and wake.weekdays and not sleep)
        self._retry_at = 0.0
        self._retry_used = False

    # ---------- lifecycle ----------

    def start(self):
        if self.wake and self.wake.wake_system \
                and power.wake_backend() is None:
            # Warn while the user is still awake, not hours later at arm
            # time — PowerDevil loses CAP_WAKE_ALARM on package updates.
            self.log("Warning: wake-from-suspend is unavailable — if the "
                     "PC suspends, the alarm will NOT wake it. (PowerDevil "
                     "usually loses CAP_WAKE_ALARM after a package update; "
                     "see the strawalarm README for the permanent fix.)")
            notify.send("Straw Alarm: wake-from-suspend unavailable",
                        "If the PC suspends, the alarm will not wake it. "
                        "See the README for the CAP_WAKE_ALARM fix.",
                        critical=True)
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
            if self.inhibitor.acquire("Sleep timer running"):
                self.log("Blocking system sleep while the music plays.")
            self.phase = Phase.SLEEP_WAIT
            state.save(self)
        else:
            self._arm_wake()

    def cancel(self):
        if self.phase in (Phase.FADE_OUT, Phase.FADE_IN) \
                and self._prefade_volume is not None:
            self.player.set_volume(self._prefade_volume)
        self._cleanup_power(clear_wake=True)
        self.cancelled = True
        self.phase = Phase.DONE
        state.clear()
        self.log("Cancelled.")

    def snooze(self) -> bool:
        """Pause the ringing alarm and re-fire after wake.snooze seconds."""
        if self.phase not in SNOOZABLE:
            return False
        self.player.pause()
        self._watchdog_until = 0.0  # don't fight the snooze
        self._snooze_deadline = time.time() + self.wake.snooze
        self.phase = Phase.SNOOZE
        again = f"{dt.datetime.fromtimestamp(self._snooze_deadline):%H:%M:%S}"
        self.log(f"Snoozed — alarm again at {again}.")
        notify.send("Snoozed", f"Alarm again at {again}.")
        return True

    def _cleanup_power(self, clear_wake: bool):
        self.inhibitor.release()
        if clear_wake and self._wake_scheduled:
            power.clear_wakeup(self._wake_cookie)
            self._wake_scheduled = False
            self._wake_cookie = None

    def tick(self):
        """Advance the state machine. Safe to call every 100-1000 ms."""
        try:
            self._tick()
        except Exception as e:  # surface, don't crash the frontend loop
            self._on_error(e)

    def _on_error(self, e):
        self.log(f"Error: {e}")
        if self.recurring and not self._retry_used:
            self._retry_used = True
            self._retry_at = time.time() + RETRY_DELAY
            self._watchdog_until = 0.0
            self.phase = Phase.RETRY
            self.log(f"Recurring alarm — retrying in {RETRY_DELAY}s.")
            return
        if self.recurring:
            # Never let one bad morning kill the whole recurrence.
            notify.send("Straw Alarm: alarm failed",
                        f"{e} — re-armed for the next scheduled day.",
                        critical=True)
            try:
                self.inhibitor.release()
                self._arm_wake()
                return
            except Exception as arm_error:
                self.log(f"Error re-arming: {arm_error}")
        self.error = str(e)
        self.phase = Phase.ERROR
        state.clear()
        notify.send("Straw Alarm: session failed", str(e), critical=True)
        self._cleanup_power(clear_wake=True)

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
        if self.phase == Phase.KEEP_AWAKE:
            return ("Keeping system awake for", self._keep_awake_deadline - now)
        if self.phase == Phase.SNOOZE:
            return ("Snoozing — alarm again in", self._snooze_deadline - now)
        if self.phase == Phase.RETRY:
            return ("Alarm hiccuped — retrying in", self._retry_at - now)
        if self.phase == Phase.ERROR:
            return (f"Error: {self.error}", None)
        if self.phase == Phase.DONE:
            return ("Done", None)
        return ("Idle", None)

    # ---------- internals ----------

    def _tick(self):
        now = time.time()
        if self._last_tick and now - self._last_tick > 30 \
                and self.phase == Phase.WAKE_WAIT:
            # The machine just resumed from suspend. If the alarm is due,
            # give PipeWire & friends a moment before poking the player.
            self._settle_until = now + RESUME_SETTLE
            self.log(f"System resumed (clock jumped "
                     f"{fmt_delta(now - self._last_tick)}); letting audio "
                     f"settle for {RESUME_SETTLE}s.")
        self._last_tick = now
        if self.phase == Phase.SLEEP_WAIT:
            if self.sleep.tracks:
                self._tick_tracks(now)
            else:
                self._tick_duration(now)
        elif self.phase == Phase.FADE_OUT:
            self._tick_fade_out(now)
        elif self.phase == Phase.WAKE_WAIT:
            if now >= self.wake_at.timestamp() and now >= self._settle_until:
                self._reach_wake_time(now)
        elif self.phase == Phase.WAIT_PLAYER:
            self._tick_wait_player(now)
        elif self.phase == Phase.FADE_IN:
            self._tick_fade_in(now)
            self._tick_watchdog(now)
        elif self.phase == Phase.KEEP_AWAKE:
            self._tick_watchdog(now)
            if now >= self._keep_awake_deadline:
                self.inhibitor.release()
                self.log("Sleep-block released; normal power settings apply.")
                self._complete()
        elif self.phase == Phase.SNOOZE:
            if now >= self._snooze_deadline:
                self._begin_alarm(now, resume_only=True)
        elif self.phase == Phase.RETRY:
            if now >= self._retry_at:
                self._reach_wake_time(now)

    def _tick_watchdog(self, now):
        """For a short window after the alarm starts, make sure playback
        actually sticks — a player poked right after resume can start,
        hit a half-initialized audio device, and pause itself."""
        if now >= self._watchdog_until or now < self._watchdog_next \
                or self._watchdog_kicks >= WATCHDOG_KICKS:
            return
        self._watchdog_next = now + 3
        if self.player.status() != "Playing":
            self._watchdog_kicks += 1
            self.log(f"Player stopped unexpectedly — restarting playback "
                     f"({self._watchdog_kicks}/{WATCHDOG_KICKS}).")
            self.player.play()

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
        if self.inhibitor.active:
            self.inhibitor.release()
            self.log("Sleep-block released; the PC may suspend now.")
        self._after_stop()

    def _after_stop(self):
        self.inhibitor.release()  # no-op if _do_stop already released it
        if self.wake:
            self._arm_wake()
        else:
            self.phase = Phase.DONE
            state.clear()
        if self.sleep and self.sleep.suspend_after:
            self.log("Suspending the machine now.")
            power.suspend()

    def _arm_wake(self):
        # base derives from time.time() so tests can fake one clock
        self.wake_at = next_occurrence(
            self.wake.time_spec, self.wake.weekdays,
            base=dt.datetime.fromtimestamp(time.time()))
        delta = self.wake_at.timestamp() - time.time()
        what = f'playlist "{self.wake.playlist}"' if self.wake.playlist \
            else "resume playback"
        self.log(f"Alarm set for {self.wake_at:%a %Y-%m-%d %H:%M:%S} "
                 f"(in {fmt_delta(delta)}) — {what}.")
        self._retry_used = False
        self._sanity_check_environment()
        if self.wake.wake_system:
            wake_epoch = int(self.wake_at.timestamp() - self.wake.wake_lead)
            if wake_epoch > time.time():
                backend, cookie = power.schedule_wakeup(wake_epoch)
                if backend:
                    self._wake_scheduled = True
                    self._wake_cookie = cookie
                    self.log(
                        f"System wake programmed for "
                        f"{dt.datetime.fromtimestamp(wake_epoch):%H:%M:%S} "
                        f"({fmt_delta(self.wake.wake_lead)} early, "
                        f"via {backend}).")
                else:
                    self.log("Warning: no wake-from-suspend backend "
                             "(PowerDevil/rtcwake) — the alarm can't wake "
                             "a suspended machine.")
        self.phase = Phase.WAKE_WAIT
        state.save(self)

    def _sanity_check_environment(self):
        """Warn at arm time about setups that silently shift alarms."""
        if self.wake.wake_system and power.rtc_is_localtime():
            msg = ("the hardware clock runs in LOCAL time (dual-boot "
                   "Windows setup?) — the RTC wake may fire hours off. "
                   "Consider 'timedatectl set-local-rtc 0'.")
            self.log(f"Warning: {msg}")
            notify.send("Straw Alarm: RTC runs in local time", msg,
                        critical=True)
        start = dt.datetime.fromtimestamp(time.time())
        if crosses_dst(start, self.wake_at):
            msg = (f"a daylight-saving change happens before "
                   f"{self.wake_at:%a %H:%M} — double-check the alarm "
                   "lands on the wall-clock time you expect.")
            self.log(f"Warning: {msg}")
            notify.send("Straw Alarm: DST change before the alarm", msg)

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

    def _begin_alarm(self, now, resume_only=False):
        self._wake_scheduled = False  # RTC alarm consumed (or moot) by now
        self._wake_cookie = None
        self._watchdog_kicks = 0
        if self.inhibitor.acquire("Alarm playing"):
            self.log("Blocking system sleep for the alarm.")
        target = self._alarm_target_volume()
        fade = self.wake.fade
        self.player.set_volume(0.0 if fade else target)
        if not resume_only and self.wake.playlist \
                and self.player.has_playlists():
            path, name = self.player.find_playlist(self.wake.playlist)
            self.player.activate_playlist(path)
            self.log(f'Wake up! Playing "{name}".')
            notify.send("Wake up!", f'Playing "{name}".')
        else:
            self.player.play()
            self.log("Wake up! Resuming playback.")
            notify.send("Wake up!", "Resuming playback.")
        if self.player.status() != "Playing":
            self.player.play()
        self._watchdog_until = now + (fade or 0) + WATCHDOG_WINDOW
        self._watchdog_next = now + 3
        if fade:
            self._fade_start = now
            self._fade_deadline = now + fade
            self._fade_target = target
            self._prefade_volume = target  # restore point if cancelled
            self.phase = Phase.FADE_IN
            self.log(f"Fading in to {round(target * 100)}% over {fade}s...")
        else:
            self._finish_alarm()

    def _tick_fade_in(self, now):
        span = self._fade_deadline - self._fade_start
        k = min(1.0, (now - self._fade_start) / span) if span else 1.0
        self.player.set_volume(self._fade_target * k)
        if k >= 1.0:
            self.log("Alarm volume reached. Good morning!")
            self._finish_alarm()

    def _finish_alarm(self):
        if self.wake.keep_awake > 0 and self.inhibitor.active:
            self._keep_awake_deadline = time.time() + self.wake.keep_awake
            self.log(f"Keeping the system awake for "
                     f"{fmt_delta(self.wake.keep_awake)} "
                     "(cancel to release early).")
            self.phase = Phase.KEEP_AWAKE
        else:
            self.inhibitor.release()
            self._complete()

    def _complete(self):
        """Alarm ran its course: recurring sessions re-arm, others end."""
        if self.recurring and not self.cancelled:
            self.log("Recurring alarm — re-arming for the next "
                     "scheduled day.")
            self._arm_wake()
            notify.send("Straw Alarm re-armed",
                        f"Next alarm: {self.wake_at:%a %H:%M}.")
        else:
            self.phase = Phase.DONE
            state.clear()


PREVIEW_FADE = 3
PREVIEW_PLAY = 10


class Preview:
    """Compressed dry-run of the alarm path so users can verify
    playlist, volume and fades in ~15 seconds instead of betting a
    morning on it. Snapshots the player state and restores it after."""

    def __init__(self, player: Player, playlist: str | None = None,
                 volume: int | None = None, log=None):
        self.player = player
        self.playlist = playlist
        self.volume = volume
        self.log = log or (lambda msg: None)
        self.active = False
        self._end = 0.0

    def start(self):
        p = self.player
        if not p.running():
            raise RuntimeError(f"{p.name} is not running.")
        self._prev_volume = p.volume()
        self._prev_status = p.status()
        self._prev_playlist = (p.active_playlist()
                               if p.has_playlists() else None)
        if self.volume is not None:
            self._target = max(0.0, min(1.0, self.volume / 100))
        else:
            self._target = self._prev_volume or DEFAULT_ALARM_VOLUME
        path = name = None
        if self.playlist and p.has_playlists():
            # resolve BEFORE touching the player — a failed lookup must
            # not leave the volume muted
            path, name = p.find_playlist(self.playlist)
        p.set_volume(0.0)
        if path:
            p.activate_playlist(path)
            self.log(f'Preview: "{name}" at {round(self._target * 100)}%, '
                     f'{PREVIEW_FADE}s fade...')
        else:
            p.play()
            self.log(f"Preview: resuming at {round(self._target * 100)}%, "
                     f"{PREVIEW_FADE}s fade...")
        self._t0 = time.time()
        self._end = self._t0 + PREVIEW_FADE + PREVIEW_PLAY
        self.active = True

    def tick(self):
        if not self.active:
            return
        now = time.time()
        k = min(1.0, (now - self._t0) / PREVIEW_FADE)
        self.player.set_volume(self._target * k)
        if now >= self._end:
            self.finish()

    def finish(self):
        """Restore the player to its pre-preview state (playlist,
        play/pause status, volume — track position is not preserved)."""
        if not self.active:
            return
        self.active = False
        p = self.player
        if self._prev_playlist:
            p.activate_playlist(self._prev_playlist)
        if self._prev_status == "Paused":
            p.pause()
        elif self._prev_status != "Playing":
            p.stop()
        p.set_volume(self._prev_volume if self._prev_volume is not None
                     else self._target)
        self.log("Preview finished — player state restored.")

    def status(self):
        return ("Previewing the alarm", max(0.0, self._end - time.time()))


def run_blocking(session: Session, interval: float = 0.25):
    """Drive a session to completion (CLI mode)."""
    try:
        session.start()
        while session.active:
            time.sleep(interval)
            session.tick()
    finally:
        if session.active:  # interrupted — drop blocks and RTC alarm
            session.cancel()
    return session.phase == Phase.DONE
