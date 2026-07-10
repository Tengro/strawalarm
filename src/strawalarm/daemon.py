"""strawalarmd — the Straw Alarm daemon (ROADMAP 2.3).

Owns the alarm session in a headless QtCore process, so alarms survive
GUI crashes and closed windows. Run as a systemd user service with
Restart=on-failure: combined with the armed-state file, even a killed
daemon comes back and re-arms itself. The GUI and CLI become frontends
talking to the versioned D-Bus service io.github.tengro.strawalarm1.
"""

import json
import signal
import sys

from PySide6.QtCore import (ClassInfo, QCoreApplication, QObject, QSettings,
                            QTimer, Slot)
from PySide6.QtDBus import QDBusConnection

from . import __version__, filelog, notify, state
from .core import Session, fmt_delta, session_state
from .mpris import Player

SERVICE = "io.github.tengro.strawalarm1"
TICK_MS = 250


def log(msg):
    print(msg, flush=True)  # journald captures stdout for the unit
    filelog.get_logger().info("[daemon] %s", msg)


@ClassInfo({"D-Bus Interface": SERVICE})
class Daemon(QObject):
    def __init__(self):
        super().__init__()
        self.session = None
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)

    # ---------- engine ----------

    def _tick(self):
        if self.session:
            self.session.tick()
            if not self.session.active:
                log(f"Session finished ({self.session.phase.value}).")
                self.session = None
                self.timer.stop()

    def _start(self, player_name, sleep, wake) -> str | None:
        """Build and start a session. Returns an error string or None."""
        try:
            player = Player.pick(player_name)
        except LookupError as e:
            return str(e)
        self.session = Session(player, sleep=sleep, wake=wake, log=log)
        try:
            self.session.start()
        except (RuntimeError, LookupError, ValueError) as e:
            self.session = None
            return str(e)
        self.timer.start()
        return None

    # ---------- D-Bus API (io.github.tengro.strawalarm1) ----------

    @Slot(str, result=str)
    def arm(self, spec_json):
        if self.session and self.session.active:
            return "Error: already armed — cancel first."
        try:
            player_name, sleep, wake = state.specs_from_dict(
                json.loads(spec_json))
        except (ValueError, TypeError, KeyError) as e:
            return f"Error: bad spec: {e}"
        if not sleep and not wake:
            return "Error: nothing to arm."
        error = self._start(player_name, sleep, wake)
        return f"Error: {error}" if error else "OK — " + self.status()

    @Slot(result=str)
    def arm_saved(self):
        """Arm with the specs the GUI last armed (stored in QSettings) —
        this is what 'strawalarm arm' from a phone reaches."""
        saved = QSettings("strawalarm", "strawalarm").value(
            "last_specs", "", type=str)
        if not saved:
            return ("Error: no saved specs yet — arm once from the GUI "
                    "or CLI first.")
        return self.arm(saved)

    @Slot(result=str)
    def cancel(self):
        if self.session and self.session.active:
            self.session.cancel()
            return "Cancelled."
        return "Nothing armed."

    @Slot(result=str)
    def snooze(self):
        if self.session and self.session.snooze():
            return "Snoozed."
        return "No ringing alarm to snooze."

    @Slot(result=str)
    def status(self):
        st = session_state(self.session)
        return st["text"] + (f" {fmt_delta(st['countdown'])}"
                             if st["countdown"] is not None else "")

    @Slot(result=str)
    def get_state(self):
        return json.dumps(session_state(self.session))

    @Slot(result=str)
    def ping(self):
        return __version__

    # ---------- recovery ----------

    def recover(self):
        """Restore an armed session left behind by a dead process —
        the heart of 'the alarm survives anything' (risk R1)."""
        data = state.load()
        if not data:
            return
        plan = state.plan_recovery(data)
        kind = plan["kind"]
        if kind == "active":
            log("Armed state owned by a living process; not touching it.")
            return
        if kind == "stale":
            state.clear()
            return
        if kind == "missed":
            log(plan["message"])
            notify.send("Straw Alarm: alarm was missed", plan["message"],
                        critical=True)
            state.clear()
            return
        if plan["sleep"] is not None:
            # Ambiguous (half-run sleep timer): a human should decide.
            notify.send("Straw Alarm was interrupted while armed",
                        f"Open Straw Alarm to restore: {plan['message']}.")
            return
        error = self._start(plan.get("player"), plan["sleep"], plan["wake"])
        if error:
            log(f"Recovery failed: {error}")
            notify.send("Straw Alarm: could not restore the alarm", error,
                        critical=True)
        else:
            log(f"Recovered: {plan['message']}.")
            notify.send("Straw Alarm restored",
                        f"Re-armed: {plan['message']}.")


def main():
    app = QCoreApplication(sys.argv)
    bus = QDBusConnection.sessionBus()
    if not bus.registerService(SERVICE):
        sys.exit("strawalarmd is already running on this session bus.")
    daemon = Daemon()
    bus.registerObject("/", daemon,
                       QDBusConnection.RegisterOption.ExportAllSlots)
    # Let Python signal handlers run despite the Qt event loop.
    heartbeat = QTimer()
    heartbeat.setInterval(500)
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start()
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    log(f"strawalarmd {__version__} up (service {SERVICE}).")
    daemon.recover()
    rc = app.exec()
    log("strawalarmd stopping — armed state, if any, persists on disk.")
    sys.exit(rc)


if __name__ == "__main__":
    main()
