"""System power integration: sleep inhibition, scheduled RTC wake,
suspend-on-demand.

- Inhibition uses a `systemd-inhibit ... sleep infinity` child process;
  holding it blocks both manual suspend and idle-triggered suspend
  (PowerDevil and friends honor logind inhibitors). PDEATHSIG ties the
  child's life to ours, so the block can never outlive strawalarm.
- Wake-from-suspend uses PowerDevil's scheduleWakeup D-Bus API on KDE
  (its privileged helper programs the RTC — no root needed; this is
  what KAlarm uses), with a best-effort `rtcwake -m no` fallback.
- Suspend goes through logind, which polkit allows for active sessions.
"""

from __future__ import annotations

import ctypes
import shutil
import signal
import subprocess

PD_DEST = "org.kde.Solid.PowerManagement"
PD_PATH = "/org/kde/Solid/PowerManagement"
PD_IFACE = "org.kde.Solid.PowerManagement"
LOGIND = ("org.freedesktop.login1", "/org/freedesktop/login1",
          "org.freedesktop.login1.Manager")

PR_SET_PDEATHSIG = 1


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _die_with_parent():
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(
        PR_SET_PDEATHSIG, signal.SIGTERM)


class Inhibitor:
    """Holds a logind sleep+idle block while acquired."""

    def __init__(self):
        self._proc = None

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def acquire(self, why: str) -> bool:
        if self.active:
            return True
        if not shutil.which("systemd-inhibit"):
            return False
        # "sleep" only: blocks suspend/hibernate but leaves the idle chain
        # (screen dimming, screen-off) alone — the display should still
        # fade while the sleep-timer music plays.
        self._proc = subprocess.Popen(
            ["systemd-inhibit", "--what=sleep", "--who=Strawalarm",
             f"--why={why}", "--mode=block", "sleep", "infinity"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=_die_with_parent)
        return True

    def release(self):
        if self.active:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None


CAP_WAKE_ALARM = 35  # capability bit, include/uapi/linux/capability.h


def _powerdevil_can_wake() -> bool:
    """PowerDevil's scheduleWakeup only truly wakes the machine if its
    process holds CAP_WAKE_ALARM (effective). Some distros ship the
    binary without the file capability, in which case the API accepts
    cookies but the RTC never gets armed — treat that as unavailable."""
    pid = _run(["pidof", "org_kde_powerdevil"])
    if not pid:
        return False
    try:
        with open(f"/proc/{pid.split()[0]}/status") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    return bool(int(line.split()[1], 16)
                                >> CAP_WAKE_ALARM & 1)
    except (OSError, ValueError):
        pass
    return False


def wake_backend() -> str | None:
    """Which wake-from-suspend mechanism is available, if any."""
    if _run(["busctl", "--user", "status", PD_DEST]) is not None \
            and _powerdevil_can_wake():
        return "powerdevil"
    if shutil.which("rtcwake"):
        return "rtcwake"
    return None


def schedule_wakeup(epoch: int) -> tuple[str | None, int | None]:
    """Program an RTC wake at the given UNIX time.
    Returns (backend, cookie); (None, None) if nothing worked."""
    if wake_backend() == "powerdevil":
        out = _run(["busctl", "--user", "call", PD_DEST, PD_PATH, PD_IFACE,
                    "scheduleWakeup", "sot",
                    "org.strawalarm", "/wakeup", str(epoch)])
        if out:  # "u <cookie>"
            try:
                return "powerdevil", int(out.split()[1])
            except (IndexError, ValueError):
                return "powerdevil", None
    if shutil.which("rtcwake"):
        r = subprocess.run(["rtcwake", "-m", "no", "-t", str(epoch)],
                           capture_output=True)
        if r.returncode == 0:
            return "rtcwake", None
    return None, None


def clear_wakeup(cookie: int | None):
    if cookie is not None:
        _run(["busctl", "--user", "call", PD_DEST, PD_PATH, PD_IFACE,
              "clearWakeup", "i", str(cookie)])
    elif shutil.which("rtcwake"):
        subprocess.run(["rtcwake", "-m", "disable"], capture_output=True)


def can_suspend() -> bool:
    out = _run(["busctl", "--system", "call", *LOGIND, "CanSuspend"])
    return out is not None and "yes" in out


def suspend():
    """Ask logind to suspend the machine now (non-interactive polkit)."""
    _run(["busctl", "--system", "call", *LOGIND, "Suspend", "b", "false"])
