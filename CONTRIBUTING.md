# Contributing

Thanks for your interest in improving the BluOS integration for the Unfolded
Circle Remote! Contributions of all kinds are welcome — bug reports, fixes,
features, and documentation.

## Reporting issues

Open an issue describing the problem. For bugs, please include:

- Your device model (Bluesound / NAD / DALI) and BluOS version.
- The integration version (`driver.json` `version` field).
- Relevant logs — run with `UC_LOG_LEVEL=DEBUG` to capture more detail.
- Steps to reproduce.

## Development setup

This project uses [devenv](https://devenv.sh) (Nix-based) for a reproducible
development environment. **Always enter the devenv shell before running any
project commands** — don't invoke `pip` or `python` directly outside of it.

```bash
git clone https://github.com/<owner>/uc-integration-bluos.git
cd uc-integration-bluos
devenv shell
```

Entering the shell prints a summary of the available scripts:

| Script           | What it does                                  |
|------------------|-----------------------------------------------|
| `run`            | Run the integration locally                   |
| `test`           | Run all tests (`pytest tests/ -v`)            |
| `lint`           | pylint, black, and isort checks               |
| `format`         | Auto-format with black and isort              |
| `build`          | PyInstaller build (host arch)                 |
| `build-aarch64`  | PyInstaller build for ARM64 via Docker        |
| `package`        | build + create tarball                        |
| `clean`          | Remove build artifacts                        |

If you run tests outside devenv, set `PYTHONPATH=intg-bluos` so the integration
modules resolve:

```bash
PYTHONPATH=intg-bluos pytest tests/ -v
```

## Making changes

1. **Branch** off `master`. Use a descriptive name (e.g. `fix/group-refresh`,
   `feat/sleep-timer`).
2. **Write tests.** New behaviour and bug fixes should come with tests under
   `tests/`. Run the full suite with `test` before submitting.
3. **Format and lint.** Run `format` then `lint` — CI enforces Black, isort, and
   Pylint with a line length of **120**.
4. **Keep commits focused** and write clear messages. Conventional-commit style
   prefixes (`fix:`, `feat:`, `docs:`, `chore:`) are used in this repo.
5. **Open a pull request** against `master` with a short description of the
   change and the motivation behind it.

### Architecture & conventions

Before adding features, skim [`CLAUDE.md`](CLAUDE.md) — it documents the module
layout, data flow, and key patterns (the volume/mute worker queue, attribute
change tracking, reconnection backoff, browse/search).

**Power efficiency is a hard design constraint.** The Remote spends most of its
life asleep, and every WebSocket message the integration pushes wakes it. Do not
push attributes on a fixed cadence (e.g. `media_position`), always diff before
pushing, and dedupe `device_state`. The "Power Efficiency" section of `CLAUDE.md`
has the full rules — please follow them for any change that touches polling or
attribute updates.

## Tests

```bash
test                                              # all tests
pytest tests/test_media_player.py -v              # a single file
pytest tests/test_media_player.py::TestBluOSMediaPlayer::test_command_on -v
```

## Versioning & releases

Releases are cut by the maintainer; contributors don't need to bump versions.
For reference, the project follows **SemVer** and keeps three files in sync —
`version.txt`, `pyproject.toml`, and `driver.json` — with git tags in the plain
`x.y.z` form (no `v` prefix).

## License

By contributing, you agree that your contributions are licensed under the
**Mozilla Public License 2.0 (MPL-2.0)**, the same license that covers this
project. See [`LICENSE`](LICENSE).
