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
media_player.py  # UC MediaPlayer entity — command handling, state mapping, browse, grouping commands
select_entity.py # UC Select entity — preset dropdown
sensor_entity.py # UC Sensor entity — multi-room group membership state
remote_entity.py # UC Remote entity — bindable command surface, delegates to media_player
config.py        # device config dataclass, JSON persistence, device manager with callbacks
discover.py      # mDNS discovery on _musc._tcp.local
setup_flow.py    # setup wizard state machine (auto-discover or manual IP)
```

**Data flow:** `driver.py` creates a `BluOSPlayer` (bluos.py) per device and listens to its events (`CONNECTED`, `DISCONNECTED`, `UPDATE`). It also creates the UC entities (`BluOSMediaPlayer`, `BluOSPresetSelect`, `BluOSGroupSensor`, `BluOSRemote`) per device and routes incoming UC Remote commands to the appropriate player. The `BluOSRemote` delegates command execution to the device's `BluOSMediaPlayer` so there is a single command-dispatch path. A background poller task calls `player.poll_status()` using long-polling with etag support.

**State is module-level in driver.py:** `_configured_players`, `_entities`, `_select_entities`, `_sensor_entities`, `_remote_entities`, `_devices`, `_REMOTE_IN_STANDBY`.

**Multi-room grouping:** the media player exposes generated simple commands — `GROUP_TOGGLE_<room>` (toggle a room in/out of this player's group), `GROUP_ALL`, `UNGROUP_ALL`, `LEAVE_GROUP` — driven from the leader. `BluOSPlayer` caches `SyncStatus` on each poll and emits `group_role`/`group_leader`/`group_followers`, which the group sensor renders. Endpoint→room-name resolution uses the other configured players via an injected `_group_targets` accessor.

## Key Patterns

**Volume/mute worker queue:** Volume and mute commands go through an `asyncio.Queue` worker to ensure sequential API calls. Target state (`_target_volume`, `_target_mute`) is tracked separately for UI responsiveness, and a 100ms debounce window prevents jitter.

**Attribute change tracking:** Entities only push attributes to the UC Remote when values change. Each entity tracks `_last_attributes` to detect diffs. Media info is cleared on OFF/STANDBY/UNAVAILABLE transitions.

**Reconnection:** Exponential backoff (1s → 30s max) via `_schedule_reconnect()`. Long-polling uses configurable timeout (default 60s in standby).

**Browse/search:** XML responses from BluOS are parsed with ElementTree. `play_url` is cached by `browseKey` for items that are both browsable and playable. Search requires a `search_key` from the parent browse response.

## Power Efficiency (must-follow)

The UC Remote spends most of its life in a low-power sleep. **Every WebSocket
message the integration pushes wakes it** — both `device_state` events and
entity `attribute` updates. Keeping the Remote asleep is a primary design
constraint, not a nice-to-have. On-hardware diagnostics showed per-poll
`media_position` pushes alone were waking the Remote ~every 30s indefinitely
during playback; eliminating them dropped a 6-minute playback window from ~12
wakes to zero. Follow these rules:

- **Never push an attribute on a fixed cadence.** If a value changes every poll
  by nature (position, elapsed time, signal level, anything monotonic), do not
  forward it each time. Push it only when the Remote genuinely needs it.
- **`media_position` specifically:** the Remote interpolates the progress bar
  itself between updates. Only push position on a forced resync, a track change,
  a play/pause/stop transition, or an explicit skip/seek. The throttle lives in
  `BluOSMediaPlayer.update_attributes`; keep `self.attributes` tracking the real
  position so FAST_FORWARD/REWIND/SEEK math stays correct even when not pushed.
- **Always diff before pushing.** Entities compute changed attributes against
  `self.attributes` (the dict ucapi keeps in sync with the Remote) and the
  driver only calls `update_attributes(entity_id, changed)` when `changed` is
  non-empty. Never push a full attribute set unconditionally.
- **Dedupe `device_state`.** Route all state changes through
  `_set_device_state`, which suppresses redundant pushes of an unchanged state.
- **Disconnect on standby.** `ENTER_STANDBY` must fully tear players down (close
  the aiohttp session, stop volume/mute workers, cancel in-flight long-polls and
  reconnect backoff) so nothing wakes the CPU or the Remote while asleep;
  `EXIT_STANDBY` reconnects and forces a no-etag full refresh.
- **New periodic attributes:** before adding any attribute that updates on every
  poll, ask "does this wake the Remote each cycle?" If yes, throttle it the same
  way — push only on meaningful change.
- **Validate on hardware.** The `diagnostics/low-power-investigation` branch
  carries `DIAG` instrumentation (push counters, `position_only_pushes`, task
  snapshots). Use a build off that branch to confirm any power-related change
  before release; `position_only_pushes` should stay ~0 during steady playback.

## Version Management

When bumping the version, keep all three files in sync:

- `version.txt` — plain version string (e.g. `0.12.3`)
- `pyproject.toml` — `version` field under `[project]`
- `driver.json` — `version` field

The CI build workflow reads the version from `driver.json` to name the output artifact (`uc-intg-bluos-<version>-aarch64.tar.gz`). If `driver.json` is out of sync, the artifact filename will show the wrong version.

After committing, create a tag using the plain version number without a `v` prefix (e.g. `0.12.3`, not `v0.12.3`).
