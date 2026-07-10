"""Desktop notifications: native org.freedesktop.Notifications call
when the Gio transport is up, notify-send subprocess otherwise, silent
no-op where neither exists. KDE Connect mirrors these to paired phones,
so alarm/failure events reach the user even away from the desk."""

import shutil
import subprocess

from . import bus

NOTIFY = ("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
          "org.freedesktop.Notifications")


def send(summary: str, body: str = "", critical: bool = False) -> bool:
    if bus.available():
        try:
            from gi.repository import GLib
            urgency = GLib.Variant("y", 2 if critical else 1)
            bus.call(*NOTIFY, "Notify", "susssasa{sv}i",
                     ("Straw Alarm", 0, "strawalarm", summary, body,
                      [], {"urgency": urgency}, -1))
            return True
        except bus.Error:
            pass
    if not shutil.which("notify-send"):
        return False
    subprocess.run(
        ["notify-send", "--app-name=Straw Alarm", "--icon=strawalarm",
         f"--urgency={'critical' if critical else 'normal'}",
         summary, body],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True
