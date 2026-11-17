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
select_entity.py # UC Select entities — presets, physical input, sleep timer
sensor_entity.py # UC Sensor entities — group membership, source, volume (dB), stream quality
remote_entity.py # UC Remote entity — bindable command surface, delegates to media_player
config.py        # device config dataclass, JSON persistence, device manager with callbacks
discover.py      # mDNS discovery on _musc._tcp.local
setup_flow.py    # setup wizard state machine (auto-discover or manual IP)
i18n.py          # translations: _a()/_am() build {"en","de","nl"} dicts from locales/*.po
```

**Data flow:** `driver.py` creates a `BluOSPlayer` (bluos.py) per device and listens to its events (`CONNECTED`, `DISCONNECTED`, `UPDATE`). It also creates the UC entities (`BluOSMediaPlayer`, `BluOSPresetSelect`, `BluOSGroupSensor`, `BluOSRemote`) per device and routes incoming UC Remote commands to the appropriate player. The `BluOSRemote` delegates command execution to the device's `BluOSMediaPlayer` so there is a single command-dispatch path. A background poller task calls `player.poll_status()` using long-polling with etag support.

**State is module-level in driver.py:** `_configured_players`, `_entities`, `_select_entities`, `_sensor_entities`, `_remote_entities`, `_status_entities`, `_devices`, `_REMOTE_IN_STANDBY`.

**Status entities:** the input and sleep-timer selects and the source, volume
(dB) and stream-quality sensors only mirror the player status, so the driver
keeps them in one registry (`_status_entities[device_id]`, a list, plus
`_status_entity_id_to_device_id`) and drives them through the same four calls:
`update_attributes(attrs)`, `set_unavailable()`, `clear_cached_attributes()` and
(selects) `command()`. To add another one, add a class with that interface to
`_STATUS_ENTITY_TYPES`; registration, removal, restart, standby and subscribe
handling then come for free. Their values come from keys in
`BluOSPlayer._status_to_attributes` (`volume_db`, `sleep_timer`, `input_name`,
`service_name`, `stream_quality`). Entity names are plain English, like the
older entities (only setup texts are translated).

**Our own /Status long-poll:** `BluOSPlayer._fetch_status` sends the status
request itself instead of calling `pyblu.Player.status()`, because pyblu's
`Status` drops `quality`, `streamFormat` and `serviceName`. It is the same single
request per poll (`etag` + `timeout` query params; the player holds it open until
the status changes or the poll timeout passes), the body is still parsed by
pyblu's public `parse_status`, and `parse_status_extras` reads the extra fields
from the same body. Errors are mapped like pyblu's `@_wrap_in_unreachable_error`:
timeouts and connection errors → `PlayerUnreachableError` (poll loop marks the
player unavailable and reconnects); other HTTP errors → `PlayerError`. Keep that
mapping if you touch it, or a dropped player skips the reconnect path. Tests: a
`tests/conftest.py` autouse fixture routes `_fetch_status` through the mocked
`pyblu.Player.status()` so the many existing mocks keep working; tests of the
real request use `@pytest.mark.real_status_fetch` (see
`tests/test_status_entities.py`, which runs a local server with real long-poll
behaviour).

**Multi-room grouping:** the media player exposes generated simple commands — `GROUP_TOGGLE_<room>` (toggle a room in/out of this player's group), `GROUP_ALL`, `UNGROUP_ALL`, `LEAVE_GROUP` — driven from the leader. `BluOSPlayer` caches `SyncStatus` on each poll and emits `group_role`/`group_leader`/`group_followers`, which the group sensor renders. Endpoint→room-name resolution uses the other configured players via an injected `_group_targets` accessor.

## Key Patterns

**Volume/mute worker queue:** Volume and mute commands go through an `asyncio.Queue` worker to ensure sequential API calls. Target state (`_target_volume`, `_target_mute`) is tracked separately for UI responsiveness, and a 100ms debounce window prevents jitter.

**Attribute change tracking:** Entities only push attributes to the UC Remote when values change. Each entity tracks `_last_attributes` to detect diffs. Media info is cleared on OFF/STANDBY/UNAVAILABLE transitions.

**Reconnection:** Exponential backoff (1s → 30s max) via `_schedule_reconnect()`. Long-polling uses configurable timeout (default 60s in standby).

**Reconfigure screen (keep the field ids stable):** follows the official UC /
`ucapi-framework` convention — a `choice` dropdown (configured devices; dummy
`""` entry when empty) followed by an `action` dropdown (`add`, `configure`,
`remove`, `reset`, `backup`, `restore`). The community Integration Manager
(JackJPowell/uc-intg-manager) automates backups against these ids: it submits
`{"choice", "action": "backup"}` in one step, reads the `backup_data` textarea
and then aborts the setup — so the backup screen must never modify config.
Restore reads the `restore_data` textarea and re-shows the screen with an error
on invalid input. `Devices.import_config` *diffs* against the current config:
removed devices are removed, new ones added, changed ones restarted in one task
(`update_handler` → `_restart_player`), identical ones left running. Never go
back to remove-all-then-add — it races a remove and an add task for the same id.
`_restart_player` carries the Remote's subscriptions over to the recreated
entity objects (ucapi dispatches commands to the objects in
`configured_entities`, so a stale one would talk to the old, closed player). `configure` keeps the device id (entity ids and the user's
activities stay intact) and the driver restarts the player.

**Translations:** all setup texts go through `i18n._a("English text")`. Add the
string to every file in `intg-bluos/locales/` (`de.po`, `nl.po`); a test fails
if any setup string lacks a translation. The `.po` files are parsed at runtime
(no `msgfmt` step) and bundled via `--add-data intg-bluos/locales:locales` in
every PyInstaller invocation (devenv scripts, `driver.spec`, Gitea build).
`driver.json` is static, so its `nl`/`de` texts are maintained by hand.

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
- **`media_position_updated_at` travels with every position.** Whenever
  `media_position` is pushed, `update_attributes` adds
  `media_position_updated_at` (ISO-8601 UTC, the moment the position was
  sampled), so the Remote interpolates the progress bar from the real sample
  time instead of from when the message arrived or when it woke up to process
  it. This matters more *because* we push position so rarely: each push has to
  anchor the bar correctly for a long stretch. Rules:
  - Never push the timestamp on its own — it only rides along with a position
    push that happens anyway, so it adds zero wakes.
  - When a position is throttled (kept local, not pushed), update the timestamp
    locally too. ucapi answers entity-state requests straight from
    `self.attributes`; a fresh position paired with a stale timestamp would make
    the Remote extrapolate too far ahead.
  - The official Apple TV / Android TV integrations, Kodi and `ucapi-framework`
    all send this attribute; tests in `tests/test_media_player.py` cover the
    pairing and the "no extra push" guarantee.
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
- **Status sensors and selects:** they are diff-pushed like everything else.
  Stream quality and source change at most once per track or source switch, which
  coincides with a media-player push that happens anyway. The sleep-timer select
  maps a counting-down timer to the step that covers it (23 min left → "30 min"),
  so it does not push every minute. Never add a status value that changes every
  poll (remaining sleep minutes, elapsed time, bitrate fluctuations) without
  throttling it the same way.
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
