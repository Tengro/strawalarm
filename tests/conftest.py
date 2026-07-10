"""Shared fakes: a controllable clock, an MPRIS player, and a power
layer — so Session tests run in milliseconds and never touch the bus."""

import pytest

from strawalarm import core


class FakeClock:
    def __init__(self, start=1_000_000_000.0):
        self.t = start

    def time(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds

    def advance(self, seconds):
        self.t += seconds


class FakePlayer:
    name = "fakeplayer"

    def __init__(self):
        self._status = "Playing"
        self._volume = 0.5
        self._running = True
        self._trackid = "/track/1"
        self._position = 10.0
        self._length = 200.0
        self._has_playlists = True
        self.launched = False
        self.calls = []

    # discovery / state
    def running(self):
        return self._running

    def desktop_entry(self):
        return None

    def status(self):
        return self._status

    def volume(self):
        return self._volume

    def set_volume(self, v):
        self._volume = v
        self.calls.append(("set_volume", round(v, 4)))

    def trackid(self):
        return self._trackid if self._running else None

    def position_s(self):
        return self._position

    def length_s(self):
        return self._length

    # control
    def play(self):
        self._status = "Playing"
        self.calls.append(("play",))

    def pause(self):
        self._status = "Paused"
        self.calls.append(("pause",))

    def stop(self):
        self._status = "Stopped"
        self.calls.append(("stop",))

    # playlists
    def has_playlists(self):
        return self._has_playlists

    def active_playlist(self):
        return "/pl/previous"

    def find_playlist(self, query):
        if query == "Morning":
            return ("/pl/1", "Morning")
        raise LookupError(f"no playlist {query!r}")

    def activate_playlist(self, path):
        self._status = "Playing"
        self.calls.append(("activate", path))

    # launching
    def launch(self, desktop_entry=None):
        self.launched = True
        return True

    # test helpers
    def called(self, name):
        return [c for c in self.calls if c[0] == name]


class FakeInhibitor:
    def __init__(self):
        self.held = False
        self.history = []

    @property
    def active(self):
        return self.held

    def acquire(self, why):
        self.held = True
        self.history.append(("acquire", why))
        return True

    def release(self):
        if self.held:
            self.history.append(("release",))
        self.held = False


class FakePower:
    def __init__(self):
        self.backend = "powerdevil"
        self.scheduled = []
        self.cleared = []
        self.suspends = 0
        self.events = []

    def wake_backend(self):
        return self.backend

    def schedule_wakeup(self, epoch):
        if self.backend is None:
            return (None, None)
        self.scheduled.append(epoch)
        self.events.append(("schedule", epoch))
        return (self.backend, 42)

    def clear_wakeup(self, cookie):
        self.cleared.append(cookie)
        self.events.append(("clear", cookie))

    def suspend(self):
        self.suspends += 1
        self.events.append(("suspend",))


@pytest.fixture(autouse=True)
def state_home(tmp_path, monkeypatch):
    """Every test gets its own XDG state dir — session persistence and
    file logging never touch the real home."""
    home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def fnotify(monkeypatch):
    """No test may fire real desktop notifications."""
    sent = []
    monkeypatch.setattr(
        core.notify, "send",
        lambda summary, body="", critical=False:
        sent.append((summary, body, critical)) or True)
    return sent


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(core.time, "time", c.time)
    monkeypatch.setattr(core.time, "sleep", c.sleep)
    return c


@pytest.fixture
def player():
    return FakePlayer()


@pytest.fixture
def fpower(monkeypatch):
    fp = FakePower()
    monkeypatch.setattr(core.power, "Inhibitor", FakeInhibitor)
    monkeypatch.setattr(core.power, "wake_backend", fp.wake_backend)
    monkeypatch.setattr(core.power, "schedule_wakeup", fp.schedule_wakeup)
    monkeypatch.setattr(core.power, "clear_wakeup", fp.clear_wakeup)
    monkeypatch.setattr(core.power, "suspend", fp.suspend)
    monkeypatch.setattr(core.power, "rtc_is_localtime", lambda: False)
    return fp


def tick_until(session, clock, cond, step=0.25, limit=1_000_000):
    """Advance the fake clock and tick until cond() holds."""
    start = clock.t
    while not cond():
        clock.advance(step)
        session.tick()
        if clock.t - start > limit:
            raise AssertionError(
                f"condition never reached (phase={session.phase})")
