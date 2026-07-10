# Publishing strawalarm — where and how

Recommended order: **PyPI → COPR → announce → AUR / Flathub** as
interest grows. Each step is independent.

## 0. Pre-flight (mostly done)

- [x] README with screenshot, features, install, the "AIMP alarm on
      Linux" search hook in the first paragraph
- [x] MIT LICENSE, tagged releases, repo topics
- [x] AppStream metainfo (`data/io.github.tengro.strawalarm.metainfo.xml`)
- [ ] Enable GitHub Issues (Settings → General → Features)
- [ ] Add a short CHANGELOG.md (can be generated from release notes)

## 1. PyPI — `pipx install strawalarm` (easiest, do first)

1. Create an account at https://pypi.org, enable 2FA.
2. The clean way is **Trusted Publishing** (no API tokens): on PyPI,
   Account → Publishing → add a "pending publisher" for
   `Tengro/strawalarm`, workflow `release.yml`, environment `pypi`.
3. Add `.github/workflows/release.yml`:

   ```yaml
   name: release
   on:
     release: { types: [published] }
   jobs:
     pypi:
       runs-on: ubuntu-latest
       environment: pypi
       permissions: { id-token: write }
       steps:
         - uses: actions/checkout@v4
         - run: pipx run build
         - uses: pypa/gh-action-pypi-publish@release/v1
   ```

4. Every future `gh release create vX.Y.Z` auto-publishes.
   Users then run: `pipx install "strawalarm[gui]"`.

Manual alternative: `pipx run build && pipx run twine upload dist/*`.

## 2. Fedora COPR — `dnf install` for your own distro

The spec (`packaging/strawalarm.spec`) and the SRPM recipe
(`.copr/Makefile`) live in the repo. Setup:

1. Log in at https://copr.fedorainfracloud.org with a Fedora (FAS)
   account (create at https://accounts.fedoraproject.org).
2. New Project: name `strawalarm`, chroots `fedora-44-x86_64` +
   `fedora-rawhide-x86_64` (add aarch64 if feeling generous).
3. In the project: Packages → New Package: name `strawalarm`,
   source type **SCM**, clone URL
   `https://github.com/Tengro/strawalarm.git`, SRPM build method
   **make srpm**. Save, then "Rebuild".
4. Auto-rebuild on push: project Settings → Integrations → copy the
   webhook URL into GitHub repo Settings → Webhooks (push events).
5. Per release: bump Version + %changelog in the spec (part of the
   release checklist).
6. Users: `dnf copr enable tengro/strawalarm && dnf install strawalarm`.

## 3. AUR — for Arch users (cheap to add, or let a user adopt it)

A `PKGBUILD` that pip-installs the wheel and copies the desktop
file/icon. Needs an AUR account + SSH key; testing is easiest in an
Arch container (`podman run -it archlinux`). If an Arch user shows up
in the issues, offering them co-maintainership is the classic move.

## 4. Flathub — biggest GUI-app reach, most work (do last)

Honest assessment: strawalarm shells out to `playerctl`,
`systemd-inhibit`, `busctl` and `rtcwake`, which do not exist inside
the Flatpak sandbox. A Flathub build therefore needs real porting, not
just packaging:

- talk to MPRIS, logind (Inhibit fd via D-Bus), and PowerDevil
  directly over D-Bus (e.g. via QtDBus, already available in PySide6)
- finish-args: `--socket=session-bus` filtered to
  `org.mpris.MediaPlayer2.*`, `org.kde.Solid.PowerManagement`,
  `org.freedesktop.login1`
- rename app id to `io.github.tengro.strawalarm` (desktop file, icon,
  metainfo `<id>`), runtime `org.kde.Platform` + PySide6 pip module
- submit a manifest PR to https://github.com/flathub/flathub

Worth it once there are users asking for it; not before.

## 5. Announcing (the part that actually finds the AIMP refugees)

- **AlternativeTo**: create a listing for strawalarm and mark it as an
  alternative to **AIMP** — this is literally where "AIMP alarm Linux"
  searches end up.
- **Reddit**: r/kde, r/Fedora (flair: showcase), r/linuxaudio;
  mention the Strawberry integration.
- **KDE Discuss** (discuss.kde.org) — KDE users are the target
  audience (PowerDevil wake integration is a good hook).
- **Strawberry**: the project has GitHub Discussions — a "companion
  app" post tends to be welcome; be clear it's third-party.
- **Hacker News**: "Show HN: AIMP-style music alarm for Linux that
  wakes your PC from suspend" — the RTC/CAP_WAKE_ALARM detective story
  from the README makes a genuinely good post.
- **Mastodon/Fediverse**: #KDE #Linux #Plasma tags; KDE's community
  boosts small-app posts often.

One good screenshot + a 30-second GIF (peek/OBS) of the fade-out →
suspend → self-wake → fade-in cycle outperforms paragraphs.

## 6. Afterwards

- Semantic versioning, keep release notes human-readable.
- The Fedora PowerDevil CAP_WAKE_ALARM gap is worth a bug report at
  https://bugzilla.redhat.com (component: powerdevil) — link it from
  the README so affected users find it.
