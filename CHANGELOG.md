# Changelog

## 0.10.0 — 2026-07-10

Phase 2 of the [roadmap](ROADMAP.md) complete — plus a rebrand.

- The app is now called **Straw Alarm** (a straw, not a Strawberry —
  it long outgrew a single player). All technical names (`strawalarm`
  package/commands, D-Bus, paths) are unchanged.
- **`strawalarmd`**: a background daemon (systemd user service,
  `Restart=on-failure`) that owns armed sessions. Alarms survive
  closing the GUI, GUI crashes, and SIGKILL of the daemon itself
  (verified live: killed mid-countdown, restarted, re-armed with the
  correct remaining time). D-Bus interface
  `io.github.tengro.strawalarm1`; the GUI auto-detects the daemon,
  arms into it, and live-monitors/controls sessions it didn't start.
  `strawalarm arm|snooze|cancel|status` reach the daemon first, so
  phone commands work with no GUI running at all.
- Without the daemon, everything still runs in-process as before.

## 0.9.0 — 2026-07-10

Phase 2 of the [roadmap](ROADMAP.md), first half: durability.

- Armed sessions persist to `~/.local/state/strawalarm/armed.json`
  (spec + deadlines; cleared on clean finish/cancel). On launch the GUI
  offers one-click re-arm with the correct remaining time; missed
  alarms are reported explicitly instead of vanishing.
- "Start with the desktop (hidden in the tray)" toggle manages an XDG
  autostart entry; started with `--hidden`, unambiguous recoveries
  (alarm-only, recurring) re-arm automatically with a notification —
  a reboot mid-recurrence no longer needs any interaction.
- The state file format is versioned and designed to become the
  daemon's state format (roadmap 2.3).

## 0.8.0 — 2026-07-10

Phase 1 of the [roadmap](ROADMAP.md): trust.

- Test suite: 94 tests over the session state machine, parsers, preview,
  recurrence and sanity checks; CI (ruff + pytest, 90% core-coverage
  gate) on Python 3.10 and 3.13.
- Every session is logged to `~/.local/state/strawalarm/strawalarm.log`
  (rotating) by both frontends.
- Desktop notifications (mirrored to phones by KDE Connect) for alarm
  fired, snoozed, re-armed, and — critically — failures.
- Recurring alarms re-arm themselves inside the session, retry a failed
  firing once after 30 s, and re-arm for the next day rather than dying
  to one bad morning.
- **Preview** button: 15-second compressed dry-run of the alarm
  (playlist, volume, fade), then full player-state restore.
- Arm-time warnings when the hardware clock runs in local time or a DST
  transition sits between now and the alarm.
- PyPI release workflow (Trusted Publishing).

## 0.7.0 — 2026-07-08

- GUI remembers all last-used values across restarts (QSettings).
- D-Bus remote control on the running GUI: `strawalarm
  arm|snooze|cancel|status` — wire into KDE Connect Run Commands for
  phone control.
- Single instance: a second GUI launch raises the existing window.

## 0.6.1 — 2026-07-08

- Survive PowerDevil package updates: `contrib/install-powerdevil-caps.sh`
  installs systemd units re-applying CAP_WAKE_ALARM at boot and after
  updates; wake backends are probed for real RTC permissions; missing
  wake capability warns at arm time (GUI dialog included).

## 0.6.0 — 2026-07-04

- Snooze (GUI button/tray action; Enter or SIGUSR1 in the CLI).
- Recurring weekday alarms (`--days mon,wed,fri` / `weekdays` /
  `weekend` / `daily`, weekday buttons in the GUI).

## 0.5.0 — 2026-07-04

- "At a clock time" and "after a duration" modes for both the sleep
  timer and the alarm, as free-text fields with live target hints
  (replaces the keyboard-hostile time-picker widgets).

## 0.4.x — 2026-07-04

- Suspend integration: sleep-block while music plays (screen still
  dims), optional suspend-after-stop, RTC self-wake via PowerDevil
  (verified on hardware), post-alarm keep-awake window, resume-settle
  grace and a playback watchdog.

## 0.3.0 — 2026-07-04

- System tray with live countdown; hide-to-tray keeps armed timers
  running. Fixed a PySide6 segfault on exit (deterministic Qt teardown).

## 0.2.0 — 2026-07-04

- Rewrite as a package: works with any MPRIS2 player, volume fade-out /
  fade-in, PySide6 GUI, desktop launcher, CLI.

## 0.1.0 — 2026-07-04

- Initial single-file CLI: sleep timer (duration/tracks) and playlist
  alarm for Strawberry.
