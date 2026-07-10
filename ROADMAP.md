# Strawalarm Roadmap

A living document. Items carry acceptance criteria — a thing is *done*
when the criteria pass, not when the code exists. Effort: S (< 1 day),
M (1–3 days), L (a week-ish of evenings).

## The promise, and the principles

The product promise is a single sentence: **"you will wake up."**
Every priority below is ranked by how directly it protects that
promise. Design principles, in tension order:

1. **Honest failure beats silent failure.** If the alarm can't do its
   job, the user must learn *when arming*, or at worst via a
   notification — never from oversleeping (learned the hard way,
   twice: the CAP_WAKE_ALARM update wipe, the 4 AM log warning).
2. **Own as little as possible.** Drive the user's player, the
   system's power machinery, the desktop's tray — don't replace them.
3. **Zero exotic dependencies.** Python + Qt + the tools a Linux
   desktop already has. Anyone can read all ~2k lines in an evening.
4. **The CLI is a first-class citizen**, not a debug tool. Everything
   the GUI does must be scriptable.

## Where we are (v0.7.0)

Working today: sleep timer (duration / clock time / track count) with
fade-out; alarm to an open playlist with fade-in, volume, snooze,
weekday recurrence; suspend integration (inhibit, suspend-after-stop,
RTC self-wake via PowerDevil, keep-awake window, resume settle +
playback watchdog); Qt GUI with tray, persistence and live hints; CLI
with the same features; D-Bus remote control (phone via KDE Connect);
single-instance. Hardware-verified: full fade-out → suspend →
self-wake → fade-in cycle on Fedora 44 / Plasma 6.

Not existing today: tests, CI, log files, failure notifications,
armed-state recovery, any packaging beyond `install.sh`.

## Risk register

| # | Risk (ranked by promise damage) | Today's exposure | Fixed by |
|---|---|---|---|
| R1 | Alarm dies with the GUI process (crash, logout, OOM, accidental quit) — silently | Total loss, no notice | 2.1, 2.2, 2.3 |
| R2 | Recurring alarm hits a transient re-arm error → recurrence dies with one log line nobody sees | Oversleep next cycle | 1.3 |
| R3 | Subprocess text parsing (playerctl/busctl output, shlex layout assumptions, locale) breaks quietly | Any feature, any update | 3.1 |
| R4 | No regression safety net — every refactor is tested by "did I wake up?" | Blocks all of Phase 2/3 | 1.1 |
| R5 | Environment assumptions: RTC-in-localtime (dual-boot), DST-transition nights, non-Strawberry MPRIS quirks, 30 s resume heuristic | Wrong-time or missed alarm | 1.5, 1.4, field reports |
| R6 | PowerDevil capability regression returns in a new form | Mitigated (units + honest probe + arm-time dialog) | watch |
| R7 | Incidents can't be diagnosed after the fact (no log file) | Slow/impossible support | 1.2 |

## Phase 1 — Trust ✅ (shipped in v0.8.0)

Strangers' first bug report must be diagnosable; their first morning
must not fail silently. **The Show HN moment is one-shot — do not
spend it before this phase is done.**

### 1.1 Test suite + CI — M
The heart (`core.Session`) is tick-driven and takes an injected
player: fake the player, monkeypatch `time.time`, and every phase
transition becomes a fast unit test. Parsers are pure functions.
- pytest: `parse_duration`, `parse_wake_time`, `next_occurrence`,
  `fmt_delta`, `parse_days` (property: round-trips, edge minutes,
  weekday wrap); `Session` with `FakePlayer` — full happy paths
  (duration / tracks / at + wake / snooze / recurrence), fade math,
  cancel-in-every-phase releases inhibitor + wake, watchdog kicks,
  resume-settle gating, error paths.
- `power` and `mpris` behind small fakes; no test touches the real bus.
- GitHub Actions: ruff + pytest on 3.10 and latest, on PR and push.
- **Done when:** a deliberately broken fade or leaked inhibitor fails
  CI; coverage of core.py ≥ 90 %.

### 1.2 File logging — S
`~/.local/state/strawalarm/strawalarm.log` (XDG_STATE_HOME), same
lines the GUI/CLI show, via `logging` with rotation (1 MB × 3). The
Session `log` callback writes to both sinks.
- **Done when:** both frontends produce the file; a crash post-mortem
  is possible with the window closed; README's bug-report section
  points to it.

### 1.3 Failure & event notifications — S
Desktop notifications (`notify-send`, graceful no-op if absent) for:
alarm fired, snoozed, recurring re-arm success ("next: Mon 07:30")
and **especially** re-arm failure; arming-time wake-backend warnings.
KDE Connect mirrors these to the phone for free.
- Recurring re-arm gets one retry after 30 s before declaring failure.
- **Done when:** killing the player before a recurring re-arm produces
  a visible desktop notification, and the retry path is unit-tested.

### 1.4 Alarm preview — S
"Preview" button beside Snooze: runs the real alarm path compressed —
switch playlist, 3 s fade-in to target volume, play 10 s, restore
previous player state (volume, playlist, paused). No suspend parts.
- **Done when:** a user can verify playlist + volume + fades in
  15 seconds without betting a morning on it.

### 1.5 Environment sanity checks — S
At arm time with wake enabled: warn if `/etc/adjtime` says `LOCAL`
(sysfs backend would set a wrong-time alarm); warn if the wake time
crosses a DST transition (compare UTC offsets of now vs target).
- **Done when:** both conditions produce arm-time warnings, unit-tested
  with fabricated inputs.

### 1.6 Release pipeline — S
CHANGELOG.md (backfilled from v0.1–v0.7 release notes), PyPI Trusted
Publishing workflow per PUBLISHING.md, release checklist in
CONTRIBUTING.md (bump, changelog, tag, `gh release` → CI publishes).
- **Done when:** `pipx install strawalarm[gui]` works from a tagged
  release with no manual upload step.

## Phase 2 — Durability ✅ (2.1+2.2 in v0.9.0, 2.3 in v0.10.0)

### 2.1 Armed-state persistence & recovery — M
On arm, serialize the session spec + phase deadlines to
`~/.local/state/strawalarm/armed.json`; delete on clean finish/cancel.
On next GUI start, if the file exists and deadlines are still in the
future: offer to re-arm (notification + dialog); if the alarm time was
*missed* while nothing ran, say so explicitly.
- **Done when:** kill -9 the GUI with an armed alarm, relaunch →
  one-click re-arm with correct remaining time.

### 2.2 Autostart — S
GUI toggle "Start with the desktop (hidden in tray)" → manages an XDG
autostart entry with `--hidden`. Combined with 2.1, login → armed
recurring alarm restores itself unattended (no dialog in `--hidden`
mode when recovery is unambiguous — a recurring alarm whose next
occurrence is in the future).
- **Done when:** reboot mid-recurrence → next alarm fires with zero
  interaction.

### 2.3 Daemon split — L (the architectural maturity step)
`strawalarmd`: a headless QtCore process owning Session + the D-Bus
service (the v0.7.0 remote interface is the blueprint — it becomes the
*only* path; add `arm(json_spec)`, `get_state`, signals
`phaseChanged`, `logLine`). Run as a systemd user service
(`Restart=on-failure`). GUI and CLI become pure frontends; GUI works
without the daemon (falls back to in-process Session) so casual usage
stays simple.
- Interface versioned from day one (`io.github.tengro.strawalarm1`).
- Kills R1 completely: alarms survive GUI crashes and logouts
  (lingering optional), `Restart=` survives daemon crashes.
- **Done when:** `systemctl --user kill strawalarmd` mid-countdown →
  alarm still fires (state restore from 2.1 on restart); GUI shows
  live state of a session it didn't start.
- **Decision to make first:** whether 2.1's file format *is* the
  daemon's state format (it should be).

## Phase 3 — Reach

### 3.1 Native D-Bus port — L ✅ (shipped in v0.11.0)
Replace playerctl/busctl/notify-send subprocess calls behind the
existing `Player`/`power` abstractions: QtDBus in GUI/daemon, GLib
(`gi`) or a vendored minimal client for the CLI (decide by measuring
import cost; CLI startup must stay < 100 ms). Subprocess code paths
stay as fallback for one release, behind an env flag, then die.
- Prerequisite: 1.1 (this is exactly the refactor tests exist for).
- Unlocks: Flathub (no host binaries inside the sandbox), kills R3,
  ends 4-subprocesses-per-second fades, enables signal-driven track
  changes (no 1 Hz polling — catches rapid skips).
- **Done when:** `strace -f -e execve` during a full session shows no
  playerctl/busctl spawns; all tests green on both transports first.

### 3.2 Packaging: COPR → AUR → Flathub — M each
In that order (own distro first, Flathub last since it needs 3.1 plus
the `io.github.tengro.strawalarm` app-id rename of desktop file/icon).
Session-bus filter: `org.mpris.MediaPlayer2.*`,
`org.kde.Solid.PowerManagement`, logind.

### 3.3 Multiple alarms / profiles — M after 2.3
Step 1 (cheap, GUI-only): named presets — save/load the settings
bundle ("Weekday morning", "Weekend"). Step 2 (needs daemon): N
concurrent Sessions with per-alarm state, list UI, and the earliest
wake owning the RTC slot (RTC hardware holds one alarm — daemon must
always program min(next wakes) and reprogram on completion).

### 3.4 KDE-native polish — M
KNotification with inline **Snooze** action (KDE Connect relays the
button to the phone — snooze from the lock screen with zero setup),
tray icon countdown badge, later a Plasmoid. Nice, never load-bearing.

## Announcement plan

Gate: Phase 1 complete. Order: AlternativeTo listing (as AIMP
alternative — that's where the target audience searches), KDE Discuss
+ Strawberry Discussions (friendly, feedback-rich), r/kde + r/Fedora,
then Show HN with the CAP_WAKE_ALARM detective story. Prepare: 30 s
GIF of fade-out → suspend → self-wake → fade-in, and the Fedora
Bugzilla report for the PowerDevil capability gap (link it — it's the
credibility artifact).

## Non-goals (deliberate, with reasons)

- **Editing a running session.** Retroactive semantics are a swamp;
  persistence makes cancel → tweak → re-arm a 3-second operation.
- **Windows/macOS.** The whole value is Linux plumbing (MPRIS, logind,
  RTC). A port shares almost no load-bearing code.
- **Bundling/replacing a player.** MPRIS-only, forever. If a player's
  MPRIS is broken, we file bugs upstream, not work around with
  player-specific hacks beyond the existing proxy-name filter.
- **System-mixer volume.** Player volume is the right scope; mixing
  both confuses restore logic and users.
- **Cron-style general scheduler.** Weekday recurrence is the ceiling;
  beyond that, users have systemd timers + the CLI.

## Decision log

| Decision | Why | Revisit when |
|---|---|---|
| Subprocess playerctl/busctl for v0.x | Shipped a working app in days; zero deps | 3.1 (planned death) |
| Inhibit `sleep` only, not `idle` | Screen must dim while music plays (user-reported) | — |
| Fixed post-alarm keep-awake (default 30 min) | User chose over until-dismissed; simple | Field feedback |
| Don't trust PowerDevil without CapEff bit 35 | Fedora ships uncapped binary; API silently no-ops | Fedora fixes packaging |
| systemd path-unit re-applies setcap | Package updates wipe file caps (bit us: 6.7.1→6.7.2) | Fedora fixes packaging |
| Snooze re-fires `resume_only` (no playlist re-activate) | Re-activating could jump tracks | — |
| Recurrence only for alarm-only sessions | Auto-repeating a sleep timer would stop music daily | Profiles (3.3) |
| GUI-owned engine, D-Bus remote as thin layer | Right size for v0.x; blueprint for daemon | 2.3 |
| One armed session at a time | Multi-alarm needs daemon-grade state | 3.3 |

## Conventions

Semver (0.x: minor = features, patch = fixes). Every release: CHANGELOG
entry, tag, GitHub release (CI publishes to PyPI after 1.6). Every
core change: tests first or with. Every "it broke at night" incident:
post-mortem note in the decision log.
