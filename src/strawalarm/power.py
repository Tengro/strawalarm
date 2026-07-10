"""System power integration: sleep inhibition, scheduled RTC wake,
suspend-on-demand.

Native D-Bus (Gio) when available — inhibition is then a real logind
Inhibit() fd that the kernel closes with our process, no child process
needed. The original systemd-inhibit/busctl subprocess paths remain as
fallback (STRAWALARM_LEGACY_TRANSPORT=1 forces them).

Wake-from-suspend uses PowerDevil's scheduleWakeup on KDE (its
privileged helper programs the RTC — what KAlarm uses), then a direct
sysfs wakealarm write, then rtcwake, whichever is actually permitted.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import signal
import subprocess

from . import bus

PD_DEST = "org.kde.Solid.PowerManagement"
PD_PATH = "/org/kde/Solid/PowerManagement"
PD_IFACE = "org.kde.Solid.PowerManagement"
LOGIND = ("org.freedesktop.login1", "/org/freedesktop/login1",
          "org.freedesktop.login1.Manager")

SYSFS_WAKEALARM = "/sys/class/rtc/rtc0/wakealarm"
RTC_DEV = "/dev/rtc0"

PR_SET_PDEATHSIG = 1
CAP_WAKE_ALARM = 35  # capability bit, include/uapi/linux/capability.h


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _die_with_parent():
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(
        PR_SET_PDEATHSIG, signal.SIGTERM)


class Inhibitor:
    """Holds a logind sleep block while acquired. Natively this is an
    Inhibit() fd (auto-released if we die); the fallback is a
    `systemd-inhibit ... sleep infinity` child tied to us by PDEATHSIG."""

    def __init__(self):
        self._fd = None
        self._proc = None

    @property
    def active(self) -> bool:
        if self._fd is not None:
            return True
        return self._proc is not None and self._proc.poll() is None

    def acquire(self, why: str) -> bool:
        if self.active:
            return True
        if bus.available():
            try:
                self._fd = bus.call_with_fd(
                    *LOGIND[:2], LOGIND[2], "Inhibit", "ssss",
                    ("sleep", "Straw Alarm", why, "block"), system=True)
                return True
            except bus.Error:
                pass  # fall through to the subprocess path
        if not shutil.which("systemd-inhibit"):
            return False
        # "sleep" only: suspend is blocked but the screen still dims.
        self._proc = subprocess.Popen(
            ["systemd-inhibit", "--what=sleep", "--who=Straw Alarm",
             f"--why={why}", "--mode=block", "sleep", "infinity"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=_die_with_parent)
        return True

    def release(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None


def _powerdevil_pid() -> int | None:
    if bus.available():
        return bus.name_owner_pid(PD_DEST)
    pid = _run(["pidof", "org_kde_powerdevil"])
    return int(pid.split()[0]) if pid else None


def _powerdevil_can_wake() -> bool:
    """PowerDevil's scheduleWakeup only truly wakes the machine if its
    process holds CAP_WAKE_ALARM (effective). Some distros ship the
    binary without the file capability, in which case the API accepts
    cookies but the RTC never gets armed — treat that as unavailable."""
    pid = _powerdevil_pid()
    if not pid:
        return False
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    return bool(int(line.split()[1], 16)
                                >> CAP_WAKE_ALARM & 1)
    except (OSError, ValueError):
        pass
    return False


def _powerdevil_present() -> bool:
    if bus.available():
        return bus.name_has_owner(PD_DEST)
    return _run(["busctl", "--user", "status", PD_DEST]) is not None


def wake_backend() -> str | None:
    """Which wake-from-suspend mechanism is *actually usable*, if any.
    Existence of a tool is not enough — rtcwake and the sysfs wakealarm
    need write access to the RTC, which plain users don't have."""
    if _powerdevil_present() and _powerdevil_can_wake():
        return "powerdevil"
    if os.access(SYSFS_WAKEALARM, os.W_OK):
        return "sysfs"
    if shutil.which("rtcwake") and os.access(RTC_DEV, os.W_OK):
        return "rtcwake"
    return None


def _sysfs_schedule(epoch: int) -> bool:
    """Arm the RTC by writing the sysfs wakealarm attribute directly.
    Assumes the hardware clock runs UTC (the Linux default)."""
    try:
        try:  # clear any pending alarm first; harmless if none
            with open(SYSFS_WAKEALARM, "w") as f:
                f.write("0")
        except OSError:
            pass
        with open(SYSFS_WAKEALARM, "w") as f:
            f.write(str(epoch))
        return True
    except OSError:
        return False


def _pd_schedule(epoch: int) -> int | None:
    """PowerDevil scheduleWakeup -> cookie, or None on failure."""
    if bus.available():
        try:
            return bus.call(PD_DEST, PD_PATH, PD_IFACE, "scheduleWakeup",
                            "sot", ("org.strawalarm", "/wakeup", epoch))[0]
        except bus.Error:
            return None
    out = _run(["busctl", "--user", "call", PD_DEST, PD_PATH, PD_IFACE,
                "scheduleWakeup", "sot",
                "org.strawalarm", "/wakeup", str(epoch)])
    if out:  # "u <cookie>"
        try:
            return int(out.split()[1])
        except (IndexError, ValueError):
            return None
    return None


def schedule_wakeup(epoch: int) -> tuple[str | None, int | None]:
    """Program an RTC wake at the given UNIX time.
    Returns (backend, cookie); (None, None) if nothing worked."""
    backend = wake_backend()
    if backend == "powerdevil":
        cookie = _pd_schedule(epoch)
        if cookie is not None:
            return "powerdevil", cookie
        backend = "sysfs"  # PowerDevil balked — try the RTC directly
    if backend == "sysfs" and _sysfs_schedule(epoch):
        return "sysfs", None
    if backend and shutil.which("rtcwake"):
        r = subprocess.run(["rtcwake", "-m", "no", "-t", str(epoch)],
                           capture_output=True)
        if r.returncode == 0:
            return "rtcwake", None
    return None, None


def clear_wakeup(cookie: int | None):
    if cookie is not None:
        if bus.available():
            try:
                bus.call(PD_DEST, PD_PATH, PD_IFACE, "clearWakeup",
                         "i", (cookie,))
            except bus.Error:
                pass
            return
        _run(["busctl", "--user", "call", PD_DEST, PD_PATH, PD_IFACE,
              "clearWakeup", "i", str(cookie)])
        return
    if os.access(SYSFS_WAKEALARM, os.W_OK):
        try:
            with open(SYSFS_WAKEALARM, "w") as f:
                f.write("0")
            return
        except OSError:
            pass
    if shutil.which("rtcwake") and os.access(RTC_DEV, os.W_OK):
        subprocess.run(["rtcwake", "-m", "disable"], capture_output=True)


def rtc_is_localtime(adjtime_path: str = "/etc/adjtime") -> bool:
    """True if the hardware clock runs in local time (common on
    Windows dual-boot). RTC wake alarms are programmed in UTC terms,
    so a local-time RTC fires hours off."""
    try:
        with open(adjtime_path) as f:
            lines = f.read().splitlines()
        return len(lines) >= 3 and lines[2].strip() == "LOCAL"
    except OSError:
        return False


def can_suspend() -> bool:
    if bus.available():
        try:
            return bus.call(*LOGIND, "CanSuspend", system=True)[0] == "yes"
        except bus.Error:
            return False
    out = _run(["busctl", "--system", "call", *LOGIND, "CanSuspend"])
    return out is not None and "yes" in out


def suspend():
    """Ask logind to suspend the machine now (non-interactive polkit)."""
    if bus.available():
        try:
            bus.call(*LOGIND, "Suspend", "b", (False,), system=True)
        except bus.Error:
            pass
        return
    _run(["busctl", "--system", "call", *LOGIND, "Suspend", "b", "false"])
