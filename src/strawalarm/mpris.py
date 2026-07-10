"""MPRIS2 client. Native D-Bus (Gio) when available; the original
playerctl/busctl subprocess paths remain as automatic fallback
(force them with STRAWALARM_LEGACY_TRANSPORT=1) for one release.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time

from . import bus

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
OBJ = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PLAYLISTS_IFACE = "org.mpris.MediaPlayer2.Playlists"
ROOT_IFACE = "org.mpris.MediaPlayer2"

# Proxies/daemons that mirror other players; skipped when auto-picking.
PROXY_PREFIXES = ("playerctld", "kdeconnect")


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


class Player:
    """One MPRIS2 player, addressed by the bus-name suffix
    (e.g. "strawberry")."""

    def __init__(self, name: str):
        self.name = name
        self.busname = MPRIS_PREFIX + name

    def __repr__(self):
        return f"Player({self.name!r})"

    # -- discovery ---------------------------------------------------

    @staticmethod
    def list_all() -> list["Player"]:
        if bus.available():
            return [Player(n[len(MPRIS_PREFIX):]) for n in bus.list_names()
                    if n.startswith(MPRIS_PREFIX)]
        out = _run(["playerctl", "--list-all"]) or ""
        return [Player(n) for n in out.splitlines() if n]

    @staticmethod
    def pick(name: str | None = None) -> "Player":
        """Resolve a player by (partial) name, or auto-pick the only
        real player running. Raises LookupError with a helpful message."""
        players = Player.list_all()
        if name:
            matches = [p for p in players if p.name.lower() == name.lower()]
            matches = matches or [p for p in players
                                  if name.lower() in p.name.lower()]
            if len(matches) == 1:
                return matches[0]
            if not matches:
                raise LookupError(
                    f'No running MPRIS player matches "{name}". '
                    f"Running: {Player._names(players)}")
            raise LookupError(
                f'"{name}" is ambiguous: {Player._names(matches)}')
        real = [p for p in players
                if not p.name.startswith(PROXY_PREFIXES)]
        if not real:
            raise LookupError("No MPRIS players are running.")
        return sorted(real, key=lambda p: p.name)[0]

    @staticmethod
    def _names(players):
        return ", ".join(p.name for p in players) or "(none)"

    # -- low-level helpers -------------------------------------------

    def _pctl(self, *args):
        return _run(["playerctl", "-p", self.name, *args])

    def _prop_legacy(self, iface, prop):
        out = _run(["busctl", "--user", "get-property", self.busname,
                    OBJ, iface, prop])
        return shlex.split(out)[1:] if out else None  # drop type token

    def _get(self, iface, prop):
        """Native property read; None on any error."""
        try:
            return bus.get_prop(self.busname, OBJ, iface, prop)
        except bus.Error:
            return None

    def _metadata(self) -> dict:
        return self._get(PLAYER_IFACE, "Metadata") or {}

    # -- state ---------------------------------------------------------

    def running(self) -> bool:
        if bus.available():
            return bus.name_has_owner(self.busname)
        return _run(["busctl", "--user", "status", self.busname]) is not None

    def identity(self) -> str:
        if bus.available():
            return self._get(ROOT_IFACE, "Identity") or self.name
        v = self._prop_legacy(ROOT_IFACE, "Identity")
        return v[0] if v else self.name

    def desktop_entry(self) -> str | None:
        if bus.available():
            return self._get(ROOT_IFACE, "DesktopEntry") or None
        v = self._prop_legacy(ROOT_IFACE, "DesktopEntry")
        return v[0] if v and v[0] else None

    def status(self) -> str:
        if bus.available():
            return self._get(PLAYER_IFACE, "PlaybackStatus") or "Not running"
        return self._pctl("status") or "Not running"

    def volume(self) -> float | None:
        if bus.available():
            v = self._get(PLAYER_IFACE, "Volume")
            return float(v) if v is not None else None
        out = self._pctl("volume")
        try:
            return float(out)
        except (TypeError, ValueError):
            return None

    def set_volume(self, v: float):
        v = max(0.0, min(1.0, v))
        if bus.available():
            try:
                bus.set_prop(self.busname, OBJ, PLAYER_IFACE, "Volume",
                             "d", v)
            except bus.Error:
                pass
            return
        self._pctl("volume", f"{v:.4f}")

    def trackid(self) -> str | None:
        if bus.available():
            tid = self._metadata().get("mpris:trackid")
            return str(tid) if tid is not None else None
        return self._pctl("metadata", "mpris:trackid")

    def position_s(self) -> float | None:
        if bus.available():
            pos = self._get(PLAYER_IFACE, "Position")
            return pos / 1_000_000 if pos is not None else None
        out = self._pctl("position")
        try:
            return float(out)
        except (TypeError, ValueError):
            return None

    def length_s(self) -> float | None:
        if bus.available():
            length = self._metadata().get("mpris:length")
            try:
                return int(length) / 1_000_000
            except (TypeError, ValueError):
                return None
        out = self._pctl("metadata", "mpris:length")
        try:
            return int(out) / 1_000_000
        except (TypeError, ValueError):
            return None

    # -- control -------------------------------------------------------

    def _player_call(self, method):
        if bus.available():
            try:
                bus.call(self.busname, OBJ, PLAYER_IFACE, method)
            except bus.Error:
                pass
            return
        self._pctl(method.lower())

    def play(self):
        self._player_call("Play")

    def pause(self):
        self._player_call("Pause")

    def stop(self):
        self._player_call("Stop")

    # -- playlists (optional MPRIS interface) ----------------------------

    def has_playlists(self) -> bool:
        if bus.available():
            return self._get(PLAYLISTS_IFACE, "PlaylistCount") is not None
        return self._prop_legacy(PLAYLISTS_IFACE, "PlaylistCount") is not None

    def playlists(self) -> list[tuple[str, str]]:
        """The playlists currently open in the player: [(obj_path, name)]."""
        if bus.available():
            try:
                rows = bus.call(self.busname, OBJ, PLAYLISTS_IFACE,
                                "GetPlaylists", "uusb",
                                (0, 200, "User", False))[0]
            except bus.Error:
                return []
            return [(path, name) for path, name, _icon in rows]
        out = _run(["busctl", "--user", "call", self.busname, OBJ,
                    PLAYLISTS_IFACE, "GetPlaylists",
                    "uusb", "0", "200", "User", "false"])
        if not out:
            return []
        tokens = shlex.split(out)  # a(oss) <count> path name icon ...
        return [(tokens[i], tokens[i + 1])
                for i in range(2, len(tokens) - 1, 3)]

    def active_playlist(self) -> str | None:
        """Object path of the currently active playlist, if any."""
        if bus.available():
            v = self._get(PLAYLISTS_IFACE, "ActivePlaylist")
            if v and v[0]:
                return v[1][0]
            return None
        v = self._prop_legacy(PLAYLISTS_IFACE, "ActivePlaylist")
        if v and v[0] == "true":
            return v[1]
        return None

    def find_playlist(self, query: str) -> tuple[str, str]:
        pls = self.playlists()
        q = query.lower()
        exact = [p for p in pls if p[1].lower() == q]
        if len(exact) == 1:
            return exact[0]
        sub = [p for p in pls if q in p[1].lower()]
        if len(sub) == 1:
            return sub[0]
        names = ", ".join(f'"{n}"' for _, n in pls) or "(none)"
        if not sub:
            raise LookupError(
                f'No playlist matches "{query}". Open playlists: {names}')
        raise LookupError(f'"{query}" is ambiguous, matches: '
                          + ", ".join(f'"{n}"' for _, n in sub))

    def activate_playlist(self, path: str):
        if bus.available():
            try:
                bus.call(self.busname, OBJ, PLAYLISTS_IFACE,
                         "ActivatePlaylist", "o", (path,))
            except bus.Error:
                pass
            return
        _run(["busctl", "--user", "call", self.busname, OBJ,
              PLAYLISTS_IFACE, "ActivatePlaylist", "o", path])

    # -- launching -------------------------------------------------------

    def launch(self, desktop_entry: str | None = None) -> bool:
        """Try to start the player app (used when it quit before the alarm)."""
        if desktop_entry:
            for base in (os.path.expanduser("~/.local/share/applications"),
                         "/usr/share/applications",
                         "/var/lib/flatpak/exports/share/applications"):
                path = os.path.join(base, desktop_entry + ".desktop")
                if os.path.exists(path):
                    subprocess.Popen(
                        ["gio", "launch", path], start_new_session=True,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
        binary = self.name.split(".")[0]
        if shutil.which(binary):
            subprocess.Popen([binary], start_new_session=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
        return False

    def wait_running(self, timeout: float = 30) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.running():
                return True
            time.sleep(0.5)
        return False
