# Strawalarm

**Sleep timer and music alarm for Linux — the AIMP "stop after / wake up
with playlist" feature, for any MPRIS2 player.**

If you searched for "AIMP alarm on Linux": this is that. Fall asleep to
your music (playback stops after a timer or a number of tracks, with a
gentle fade-out), and wake up to a *different* playlist at a set time,
fading in from silence — all in your own music player.

Built for [Strawberry](https://www.strawberrymusicplayer.org/), works
with anything that speaks MPRIS2 (VLC, Elisa, Audacious, ...). Playlist
switching uses the optional MPRIS `Playlists` interface — the alarm
picks from the playlists **already open in your player**; on players
without it, the alarm simply resumes playback.

## Features

- **Sleep timer**: stop or pause after a duration (`1h30m`) or after N
  tracks (the fade-out is timed to end exactly with the last track)
- **Alarm**: at a given time, switch to one of the player's open
  playlists, set the volume, and play — relaunching the player first if
  you closed it
- **Fades**: smooth volume fade-out into sleep, fade-in from silence on
  wake; your original volume is restored after the sleep fade
- **GUI and CLI**: a small Qt app that follows your desktop theme
  (looks native on KDE Plasma), plus a scriptable command line
- No daemon, no config files, no Python dependencies beyond Qt for the
  GUI. Talks to the player through `playerctl` and `busctl`.

## Requirements

- Linux with systemd (`busctl`) and [playerctl](https://github.com/altdesktop/playerctl)
- Python ≥ 3.10; PySide6 for the GUI
- An MPRIS2-capable player

Fedora: `sudo dnf install playerctl python3-pyside6`
Debian/Ubuntu: `sudo apt install playerctl python3-pyside6`

## Install

From a checkout — puts `strawalarm`/`strawalarm-gui` in `~/.local/bin`
and adds a launcher ("Strawalarm") to your application menu:

```sh
./install.sh
```

Or with pipx: `pipx install "strawalarm[gui] @ git+https://github.com/tengro/strawalarm"`
(then copy `data/strawalarm.desktop` and the icon yourself if you want
the menu entry).

## Usage

GUI: launch **Strawalarm** from your app menu, or `strawalarm gui`.

CLI:

```sh
strawalarm list                    # running players + their open playlists

# Sleep timer
strawalarm sleep 1h30m --fade 30           # stop in 1h30m, fade the last 30s
strawalarm sleep --tracks 5 --fade 20      # 5 tracks, fade ending with track 5
strawalarm sleep 1h --pause                # pause instead of stop

# Alarm
strawalarm wake 07:30 --playlist Morning --volume 40 --fade-in 60
strawalarm wake +8h --playlist Morning     # relative time

# The full night in one command
strawalarm sleep 45m --fade 30 --wake 07:30 --playlist Morning \
          --volume 40 --fade-in 60
```

Durations: `1h30m`, `90m`, `45s`, `01:30:00`, or bare minutes. Playlist
names match case-insensitively (exact, then unique substring). With
several players running, pick one with `--player NAME`.

The CLI runs in the foreground (Ctrl+C cancels). To detach:
`systemd-run --user strawalarm wake 07:30 --playlist Morning`.

## Notes / known limits

- Timers don't survive machine suspend — meant for an always-on box.
  Waking the machine itself (RTC alarm) is on the roadmap.
- Volume control is the player's own volume, not the system mixer.
- Without `--fade`, `--tracks N` stops the instant track N+1 starts, so
  you may hear a sub-second blip; with a fade it ends cleanly.

## License

MIT
