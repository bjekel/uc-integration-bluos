# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A custom integration for the Unfolded Circle Remote that enables control of BluOS-enabled streaming players (Bluesound, NAD, DALI devices). It runs as a WebSocket server on port 9300 using the `ucapi` library and communicates with BluOS players via the `pyblu` library.

## Development Commands

The project uses `devenv` (Nix-based). Inside the devenv shell, these scripts are available:

```bash
lint              # pylint, black, and isort checks
format            # auto-format with black and isort
test              # run all tests: pytest tests/ -v
run               # run the integration locally
build             # PyInstaller build (host arch)
build-aarch64     # PyInstaller build for ARM64 via Docker
package           # build + create tarball
package-aarch64   # build-aarch64 + create tarball
clean             # remove build artifacts
```

Run a single test file or test case:
```bash
pytest tests/test_media_player.py -v
pytest tests/test_media_player.py::TestBluOSMediaPlayer::test_command_on -v
```

Set `PYTHONPATH=intg-bluos` when running pytest outside devenv.

Environment variables:
- `UC_CONFIG_HOME` — where `config.json` is stored (default: `./data`)
- `UC_LOG_LEVEL` — log level (default: `INFO`)

## Architecture

```
driver.py        # entry point, UC API WebSocket server, background poller
bluos.py         # pyblu wrapper — connection, events, playback, volume workers, browse/search
media_player.py  # UC MediaPlayer entity — command handling, state mapping, browse
select_entity.py # UC Select entity — preset dropdown
config.py        # device config dataclass, JSON persistence, device manager with callbacks
discover.py      # mDNS discovery on _musc._tcp.local
setup_flow.py    # setup wizard state machine (auto-discover or manual IP)
```

**Data flow:** `driver.py` creates a `BluOSPlayer` (bluos.py) per device and listens to its events (`CONNECTED`, `DISCONNECTED`, `UPDATE`). It also creates the UC entities (`BluOSMediaPlayer`, `BluOSPresetSelect`) per device and routes incoming UC Remote commands to the appropriate player. A background poller task calls `player.poll_status()` using long-polling with etag support.

**State is module-level in driver.py:** `_configured_players`, `_entities`, `_select_entities`, `_devices`, `_REMOTE_IN_STANDBY`.

## Key Patterns

**Volume/mute worker queue:** Volume and mute commands go through an `asyncio.Queue` worker to ensure sequential API calls. Target state (`_target_volume`, `_target_mute`) is tracked separately for UI responsiveness, and a 100ms debounce window prevents jitter.

**Attribute change tracking:** Entities only push attributes to the UC Remote when values change. Each entity tracks `_last_attributes` to detect diffs. Media info is cleared on OFF/STANDBY/UNAVAILABLE transitions.

**Reconnection:** Exponential backoff (1s → 30s max) via `_schedule_reconnect()`. Long-polling uses configurable timeout (default 60s in standby).

**Browse/search:** XML responses from BluOS are parsed with ElementTree. `play_url` is cached by `browseKey` for items that are both browsable and playable. Search requires a `search_key` from the parent browse response.

## Version Management

When bumping the version, keep all three files in sync:

- `version.txt` — plain version string (e.g. `0.12.3`)
- `pyproject.toml` — `version` field under `[project]`
- `driver.json` — `version` field

The CI build workflow reads the version from `driver.json` to name the output artifact (`uc-intg-bluos-<version>-aarch64.tar.gz`). If `driver.json` is out of sync, the artifact filename will show the wrong version.

After committing, create a tag using the plain version number without a `v` prefix (e.g. `0.12.3`, not `v0.12.3`).
