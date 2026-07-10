"""Native D-Bus transport over PyGObject/Gio (ROADMAP 3.1).

Synchronous calls only — they need no main loop, so the same code
serves the CLI, the GUI and the daemon. If PyGObject is missing (or
STRAWALARM_LEGACY_TRANSPORT=1 is set), callers fall back to their old
playerctl/busctl subprocess paths, which remain for one release.
"""

from __future__ import annotations

import os

try:
    from gi.repository import Gio, GLib
    _HAVE_GI = True
except (ImportError, ValueError):
    _HAVE_GI = False

DBUS = ("org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus")
PROPS = "org.freedesktop.DBus.Properties"


class Error(Exception):
    pass


def available() -> bool:
    if os.environ.get("STRAWALARM_LEGACY_TRANSPORT"):
        return False
    return _HAVE_GI


_buses: dict = {}


def _bus(system: bool = False):
    key = "system" if system else "session"
    if key not in _buses:
        try:
            _buses[key] = Gio.bus_get_sync(
                Gio.BusType.SYSTEM if system else Gio.BusType.SESSION, None)
        except GLib.Error as e:
            raise Error(str(e)) from e
    return _buses[key]


def call(dest, path, iface, method, signature="", args=(),
         system=False, timeout_ms=3000):
    """Synchronous method call. Returns the unpacked result tuple."""
    params = GLib.Variant(f"({signature})", tuple(args)) \
        if signature else None
    try:
        result = _bus(system).call_sync(
            dest, path, iface, method, params, None,
            Gio.DBusCallFlags.NONE, timeout_ms, None)
    except GLib.Error as e:
        raise Error(e.message or str(e)) from e
    return result.unpack() if result is not None else ()


def get_prop(dest, path, iface, prop, system=False):
    return call(dest, path, PROPS, "Get", "ss", (iface, prop),
                system=system)[0]


def set_prop(dest, path, iface, prop, variant_sig, value, system=False):
    call(dest, path, PROPS, "Set", "ssv",
         (iface, prop, GLib.Variant(variant_sig, value)), system=system)


def name_has_owner(name: str, system: bool = False) -> bool:
    try:
        return call(*DBUS, "NameHasOwner", "s", (name,), system=system)[0]
    except Error:
        return False


def list_names(system: bool = False) -> list[str]:
    try:
        return call(*DBUS, "ListNames", system=system)[0]
    except Error:
        return []


def name_owner_pid(name: str, system: bool = False) -> int | None:
    try:
        return call(*DBUS, "GetConnectionUnixProcessID", "s", (name,),
                    system=system)[0]
    except Error:
        return None


def call_with_fd(dest, path, iface, method, signature, args,
                 system=False) -> int:
    """Method call whose reply carries a unix fd (logind Inhibit).
    Returns the fd (caller owns it — close to release)."""
    params = GLib.Variant(f"({signature})", tuple(args))
    try:
        result, fd_list = _bus(system).call_with_unix_fd_list_sync(
            dest, path, iface, method, params, GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE, 3000, None, None)
    except GLib.Error as e:
        raise Error(e.message or str(e)) from e
    index = result.unpack()[0]
    fds = fd_list.steal_fds()
    return fds[index]
