# Contributing

Before proposing a feature, skim [ROADMAP.md](ROADMAP.md) — especially
the **non-goals** and the **decision log**; most "why doesn't it just…"
questions are answered there.

## Development setup

```sh
git clone https://github.com/Tengro/strawalarm && cd strawalarm
python3 -m venv .venv --system-site-packages
.venv/bin/pip install -e ".[dev]"       # add ,gui if PySide6 isn't system-wide
.venv/bin/pytest tests/                  # 90%+ coverage of core.py is the gate
.venv/bin/ruff check src tests
```

The engine (`core.py`) must stay testable without a desktop: it talks
to the world only through the injected `Player` and the `power`/
`notify` modules, all faked in `tests/conftest.py`. Every core change
lands with tests. Anything touching the "you will wake up" path
(arming, RTC, inhibitors, recurrence) needs a test for its failure
mode, not just its happy path.

## Release checklist

1. Update `CHANGELOG.md`; bump the version in `pyproject.toml`,
   `src/strawalarm/__init__.py` and `packaging/strawalarm.spec`
   (Version + a %changelog entry); add a `<release>` entry to
   `data/io.github.tengro.strawalarm.metainfo.xml`.
2. Commit, then `git tag vX.Y.Z && git push --follow-tags`.
3. `gh release create vX.Y.Z --title ... --notes ...` — publishing the
   GitHub release triggers the PyPI upload via Trusted Publishing.
4. Check the `release` workflow went green and
   `pipx install strawalarm[gui]` pulls the new version.
