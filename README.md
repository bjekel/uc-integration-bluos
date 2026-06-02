# BluOS Integration for Unfolded Circle Remote

> ⚠️ **This project has been completely vibe-coded.**
> Every line — code, tests, and this README — was written by an AI assistant
> through conversational prompting, with no line-by-line human authoring. It
> works for the author's own devices, but it has **not** been formally reviewed
> or audited. Use it at your own risk, and validate behaviour against your own
> hardware before relying on it.

A custom integration for the [Unfolded Circle Remote](https://www.unfoldedcircle.com/)
(Remote Two / Remote 3) that controls BluOS-enabled streaming players —
Bluesound, NAD, and DALI devices.

It runs as a WebSocket server (port `9300`) built on the
[`ucapi`](https://github.com/unfoldedcircle/integration-python-library) library
and talks to players through [`pyblu`](https://github.com/LouisChrist/pyblu).

---

## Features

### Media player
- **Transport:** play / pause, stop, next / previous, fast-forward, rewind, seek
- **Power:** on / off / toggle (play-pause based)
- **Volume:** set level, up / down, mute / unmute / toggle
- **Metadata:** title, artist, album, cover art, duration, live position
- **Modes:** shuffle, repeat (off → all → one)
- **Sources:** select inputs and presets
- **Browse & search:** navigate the BluOS music library and play items directly
  (`browse_media`, `search_media`, `play_media`), plus clear playlist

### Presets (Select entity)
A dropdown of the player's saved presets for quick recall.

### Multi-room grouping
Leader-driven grouping exposed as simple commands (see below). A dedicated
**group sensor** entity shows each player's role (leader / follower / standalone),
the group leader, and its followers.

### Remote entity
A bindable command surface that delegates to the media player, so grouping and
playback commands can be mapped to buttons and used in activities/macros.

### Other
- **Auto-discovery** of BluOS devices via mDNS (`_musc._tcp.local`)
- **Fast updates** via long-polling with ETag support
- **Power-efficient:** the integration avoids needlessly waking the Remote
  (no fixed-cadence attribute pushes) and fully disconnects players on standby

---

## Supported devices

Any BluOS-enabled device should work, including:

- **Bluesound:** NODE, POWERNODE, PULSE, VAULT
- **NAD:** M10, M33, C 658, C 700, T 758 V3i
- **DALI:** CALLISTO, SOUND HUB, OBERON C
- Other BluOS-based players

---

## Installation

### On the Remote (from a release package)

1. Download the latest `uc-intg-bluos-<version>-aarch64.tar.gz` from the
   releases page.
2. Upload it to your Remote via the web configurator
   (**Integrations → Install custom**).
3. Run the setup flow to add your devices.

### Setup flow

When configuring, choose one of:

- **Discover devices automatically** — finds BluOS players on your network via mDNS.
- **Enter IP address manually** — for players that don't discover cleanly.

---

## Simple commands

Usable in activities and macros, exposed on the media player / remote entity:

| Command | Description |
|---|---|
| `PRESET_1`, `PRESET_2`, … | Load a saved preset by its number |
| `REFRESH_PRESETS` | Re-read the preset list from the device |
| `SHUFFLE_TOGGLE` | Toggle shuffle |
| `REPEAT_TOGGLE` | Cycle repeat: off → all → one |
| `SLEEP_TIMER` | Cycle sleep timer: 15 → 30 → 45 → 60 → 90 → off (minutes) |

### Grouping commands

These are generated on the **leader** player. `GROUP_TOGGLE_<room>` is created
per other configured player, using that player's name:

| Command | Description |
|---|---|
| `GROUP_TOGGLE_<room>` | Add/remove that room from this player's group |
| `GROUP_ALL` | Group all configured players under this leader |
| `UNGROUP_ALL` | Disband this player's group |
| `LEAVE_GROUP` | Make this player leave the group it belongs to |

---

## Configuration

Each device stores the following (defaults shown). These are persisted in
`config.json` under `UC_CONFIG_HOME`:

| Field | Default | Description |
|---|---|---|
| `address` | — | IP address of the BluOS device |
| `port` | `11000` | BluOS API port |
| `volume_step` | `5` | Step size for volume up/down |
| `timeout` | `5.0` | Connection timeout (seconds) |
| `standby_timeout` | `60` | Long-poll timeout while the Remote is in standby |
| `active_poll_timeout` | `30` | Long-poll timeout while active |
| `model` | — | Device model (informational) |

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `UC_CONFIG_HOME` | `./data` | Directory for `config.json` |
| `UC_LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## Development

This project uses [devenv](https://devenv.sh) (Nix-based) for a reproducible
environment. **Always enter the devenv shell before running project commands** —
don't invoke `pip`/`python` directly.

```bash
git clone https://github.com/<owner>/uc-integration-bluos.git
cd uc-integration-bluos
devenv shell        # prints the command summary on entry
```

### Available commands

Printed on shell entry:

| Command | Description |
|---|---|
| `run` | Run the integration locally |
| `test` | Run the test suite (`pytest tests/ -v`) |
| `lint` | pylint + black + isort checks |
| `format` | Auto-format (black + isort) |
| `build` | PyInstaller build (host arch) |
| `package` | `build` + tarball (host arch) |
| `build-aarch64` | PyInstaller build for ARM64 via Docker |
| `package-aarch64` | `build-aarch64` + tarball |
| `clean` | Remove build artifacts |
| `setup-qemu` | Install QEMU arm64 emulation (x86-64 hosts only) |
| `register-integration` | Register the driver with a local Remote/simulator |
| `register-integration-remote` | Register with a remote device (`UC_REMOTE_HOST` / `UC_REMOTE_PIN`) |

### Running tests

```bash
test                                              # whole suite
pytest tests/test_media_player.py -v              # one file
pytest tests/test_bluos.py::TestBluOSPlayer::test_volume_up -v
```

Outside devenv, set `PYTHONPATH=intg-bluos` first.

---

## Architecture

```
intg-bluos/
├── driver.py        # entry point: UC WebSocket server + background poller
├── bluos.py         # pyblu wrapper: connection, events, playback, volume/mute
│                    #   workers, browse/search, grouping/sync-status
├── media_player.py  # MediaPlayer entity: commands, state mapping, grouping
├── select_entity.py # Select entity: preset dropdown
├── sensor_entity.py # Sensor entity: multi-room group membership
├── remote_entity.py # Remote entity: bindable commands → media player
├── config.py        # device config dataclass + JSON persistence + manager
├── discover.py      # mDNS discovery (_musc._tcp.local)
└── setup_flow.py    # setup wizard (auto-discover or manual IP)
```

**Data flow:** `driver.py` creates one `BluOSPlayer` per device, listens to its
`CONNECTED` / `DISCONNECTED` / `UPDATE` events, and creates the matching UC
entities. A background task long-polls each player for status. Volume and mute
commands flow through an `asyncio.Queue` worker for sequential, debounced API
calls. Entities only push attributes to the Remote when values actually change,
to keep the Remote asleep as much as possible.

---

## Troubleshooting

**Device not discovered**
- Ensure the player and Remote are on the same network/subnet.
- Check that mDNS/Bonjour isn't blocked by your router.
- Fall back to manual IP entry.

**Connection issues**
- Verify the device is powered on and reachable.
- Confirm its IP hasn't changed (consider a DHCP reservation).
- Increase `timeout` on slow networks.

**State not updating**
- Updates arrive via long-polling within seconds.
- Set `UC_LOG_LEVEL=DEBUG` for detail.

**Volume jumps unexpectedly when grouped**
- BluOS reports a grouped player's own volume as `-1` and the real level via
  group volume; the integration handles this — make sure you're on a recent
  version.

---

## License

Licensed under the **Mozilla Public License 2.0 (MPL-2.0)** — the same license
used by the official Unfolded Circle integrations and the `ucapi` library. See
[`LICENSE`](LICENSE).

## Acknowledgments

- [pyblu](https://github.com/LouisChrist/pyblu) — Python BluOS API client
- [Unfolded Circle](https://www.unfoldedcircle.com/) — the Remote and integration SDK
