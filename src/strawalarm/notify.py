"""Desktop notifications via notify-send; silent no-op where absent.
KDE Connect mirrors these to paired phones, so alarm/failure events
reach the user even away from the desk."""

import shutil
import subprocess


def send(summary: str, body: str = "", critical: bool = False) -> bool:
    if not shutil.which("notify-send"):
        return False
    subprocess.run(
        ["notify-send", "--app-name=Straw Alarm", "--icon=strawalarm",
         f"--urgency={'critical' if critical else 'normal'}",
         summary, body],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True
