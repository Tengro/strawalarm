"""strawalarm command-line interface."""

import argparse
import datetime as dt
import signal
import sys

from . import __version__
from .core import Session, SleepSpec, WakeSpec, parse_duration, run_blocking
from .mpris import Player


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def pick_player(args) -> Player:
    try:
        player = Player.pick(args.player)
    except LookupError as e:
        sys.exit(str(e))
    if not args.player:
        others = [p.name for p in Player.list_all() if p.name != player.name]
        if others:
            log(f"Using player: {player.name} (--player to override; "
                f"also running: {', '.join(others)})")
    return player


def cmd_list(args):
    players = Player.list_all()
    if not players:
        sys.exit("No MPRIS players are running.")
    for p in players:
        print(f"{p.name}  ({p.identity()}, {p.status()})")
        if p.has_playlists():
            for path, name in p.playlists():
                pid = path.rsplit("/", 1)[-1]
                print(f"  [{pid}] {name}")
        else:
            print("  (no MPRIS playlist support)")


def build_wake(args) -> WakeSpec:
    return WakeSpec(time_spec=args.time if hasattr(args, "time") else args.wake,
                    playlist=args.playlist, volume=args.volume,
                    fade=args.fade_in)


def cmd_sleep(args):
    sleep = SleepSpec(tracks=args.tracks, fade=args.fade, pause=args.pause)
    if not args.tracks:
        try:
            sleep.seconds = parse_duration(args.duration)
        except ValueError as e:
            sys.exit(str(e))
    wake = build_wake(args) if args.wake else None
    run(Session(pick_player(args), sleep=sleep, wake=wake, log=log))


def cmd_wake(args):
    run(Session(pick_player(args), wake=build_wake(args), log=log))


def run(session):
    try:
        ok = run_blocking(session)
    except (RuntimeError, LookupError, ValueError) as e:
        sys.exit(str(e))
    if not ok:
        sys.exit(1)


def cmd_gui(_args):
    from .gui import main as gui_main
    gui_main()


def add_common(sp):
    sp.add_argument("--player", metavar="NAME",
                    help="MPRIS player to control (default: auto-pick)")


def add_wake_opts(sp):
    sp.add_argument("--playlist", metavar="NAME",
                    help="open playlist to play (default: resume playback)")
    sp.add_argument("--volume", type=int, metavar="PCT",
                    help="alarm volume 0-100 (default: keep player volume)")
    sp.add_argument("--fade-in", type=int, default=0, metavar="SEC",
                    help="ramp volume up over SEC seconds (default: off)")


def main():
    p = argparse.ArgumentParser(
        prog="strawalarm",
        description="Sleep timer and playlist alarm for MPRIS2 media players "
                    "(Strawberry, VLC, Elisa, ...), AIMP-style.")
    p.add_argument("--version", action="version",
                   version=f"strawalarm {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="show running players and their "
                                     "open playlists")
    pl.set_defaults(func=cmd_list)

    ps = sub.add_parser("sleep", help="stop playback after a duration or "
                                      "a number of tracks")
    ps.add_argument("duration", nargs="?",
                    help="e.g. 1h30m, 90m, 01:30:00, or minutes")
    ps.add_argument("--tracks", type=int, metavar="N",
                    help="stop after N tracks instead (current track is #1)")
    ps.add_argument("--fade", type=int, default=0, metavar="SEC",
                    help="fade out over the last SEC seconds (default: off)")
    ps.add_argument("--pause", action="store_true",
                    help="pause instead of stop (keeps position)")
    ps.add_argument("--wake", metavar="TIME",
                    help="then set an alarm, e.g. 07:30 or +8h")
    add_wake_opts(ps)
    add_common(ps)
    ps.set_defaults(func=cmd_sleep)

    pw = sub.add_parser("wake", help="play (a playlist) at a given time")
    pw.add_argument("time", help="e.g. 07:30, 07:30:00, or +8h")
    add_wake_opts(pw)
    add_common(pw)
    pw.set_defaults(func=cmd_wake)

    pg = sub.add_parser("gui", help="launch the graphical interface")
    pg.set_defaults(func=cmd_gui)

    args = p.parse_args()
    if args.cmd == "sleep":
        if not args.duration and not args.tracks:
            ps.error("give a duration or --tracks N")
        if args.duration and args.tracks:
            ps.error("duration and --tracks are mutually exclusive")
        if (args.playlist or args.volume is not None or args.fade_in) \
                and not args.wake:
            ps.error("--playlist/--volume/--fade-in need --wake TIME")

    signal.signal(signal.SIGINT, lambda *_: sys.exit("\nCancelled."))
    args.func(args)


if __name__ == "__main__":
    main()
