#!/usr/bin/env python3
"""BluOS integration driver for Unfolded Circle Remote."""

import asyncio
import logging
import os
import sys
from typing import Any

import aiohttp
import setup_flow
import ucapi
from bluos import BluOSPlayer
from bluos import Events as BluOSEvents
from config import BluOSDevice, Devices
from media_player import BluOSMediaPlayer
from pyblu.errors import PlayerError, PlayerUnreachableError
from remote_entity import BluOSRemote
from select_entity import BluOSPresetSelect
from sensor_entity import BluOSGroupSensor
from ucapi import EntityTypes

_LOG = logging.getLogger(__name__)

# Polling interval when no players are configured (in seconds)
NO_PLAYERS_POLL_INTERVAL = 5

# Event loop
_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)

# Integration API
api = ucapi.IntegrationAPI(_LOOP)

# Configured players and entities
_configured_players: dict[str, BluOSPlayer] = {}
_entities: dict[str, BluOSMediaPlayer] = {}
_select_entities: dict[str, BluOSPresetSelect] = {}
_sensor_entities: dict[str, BluOSGroupSensor] = {}
_remote_entities: dict[str, BluOSRemote] = {}
_devices: Devices | None = None

# Reverse maps for O(1) entity-id → device-id lookup in command handler and subscribe handler.
_entity_id_to_device_id: dict[str, str] = {}
_select_entity_id_to_device_id: dict[str, str] = {}
_sensor_entity_id_to_device_id: dict[str, str] = {}
_remote_entity_id_to_device_id: dict[str, str] = {}


def _group_targets(device_id: str) -> list[BluOSPlayer]:
    """Return the other configured players ``device_id`` can be grouped with."""
    return [player for did, player in _configured_players.items() if did != device_id]


# Remote state
_REMOTE_IN_STANDBY = False

# Event that is set while the remote is active and cleared during standby.
# The status poller waits on this instead of sleep-looping, so it consumes
# no CPU cycles while the remote is in standby.
_poller_active = asyncio.Event()

# In-flight long-poll tasks from the current poller iteration. Held so that
# _on_enter_standby() can cancel them, releasing their HTTP connections to the
# BluOS devices immediately instead of letting them linger until poll timeout.
_active_poll_tasks: list[asyncio.Task] = []

# Last device state pushed to the remote. ucapi notifies the remote on every
# set_device_state() call even when the value is unchanged, and those messages
# can wake the remote from low-power mode, so we suppress redundant updates.
# Reset on each remote (re)connect so a fresh client always gets the state.
_last_device_state: ucapi.DeviceStates | None = None


def _get_driver_path() -> str:
    """Get the path to driver.json, handling PyInstaller bundles."""
    _LOG.debug("sys.executable: %s", sys.executable)
    _LOG.debug("cwd: %s", os.getcwd())

    # Check multiple possible locations for driver.json
    candidates = [
        # Current working directory (UC Remote sets cwd to package root)
        "driver.json",
        # Relative to executable's parent (package_root/bin/driver -> package_root/driver.json)
        os.path.join(os.path.dirname(os.path.dirname(sys.executable)), "driver.json"),
        # Same directory as executable
        os.path.join(os.path.dirname(sys.executable), "driver.json"),
    ]

    for path in candidates:
        _LOG.debug("Checking for driver.json at: %s (exists: %s)", path, os.path.isfile(path))
        if os.path.isfile(path):
            _LOG.info("Found driver.json at: %s", path)
            return path

    _LOG.warning("driver.json not found in any expected location, using fallback")
    return "driver.json"


def _on_device_added(device: BluOSDevice) -> None:
    """Handle device added callback."""
    _LOG.info("Device added: %s", device.name)
    _LOOP.create_task(_add_player(device))


def _on_device_removed(device_id: str) -> None:
    """Handle device removed callback."""
    _LOG.info("Device removed: %s", device_id)
    _LOOP.create_task(_remove_player(device_id))


async def _add_player(device: BluOSDevice) -> None:
    """Add a BluOS player."""
    if device.id in _configured_players:
        _LOG.debug("Player already exists: %s", device.id)
        return

    player = BluOSPlayer(device, _LOOP)

    # Register event handlers
    player.events.on(BluOSEvents.CONNECTED, lambda: _on_player_connected(device.id))
    player.events.on(BluOSEvents.DISCONNECTED, lambda: _on_player_disconnected(device.id))
    player.events.on(BluOSEvents.UPDATE, _on_player_update)

    _configured_players[device.id] = player

    # Create media player entity
    entity = BluOSMediaPlayer(device, player, lambda did=device.id: _group_targets(did))
    _entities[device.id] = entity
    _entity_id_to_device_id[entity.id] = device.id

    # Register entity with API
    if api.available_entities.contains(entity.id):
        api.available_entities.remove(entity.id)
    api.available_entities.add(entity)

    _LOG.info("Registered entity: %s", entity.id)

    # Create select entity for presets (options may be empty until player connects)
    select_entity = BluOSPresetSelect(device, player)
    _select_entities[device.id] = select_entity
    _select_entity_id_to_device_id[select_entity.id] = device.id

    # Register select entity with API
    if api.available_entities.contains(select_entity.id):
        api.available_entities.remove(select_entity.id)
    api.available_entities.add(select_entity)

    _LOG.info("Registered select entity: %s", select_entity.id)

    # Create group sensor entity (multi-room membership)
    sensor_entity = BluOSGroupSensor(device, player, lambda did=device.id: _group_targets(did))
    _sensor_entities[device.id] = sensor_entity
    _sensor_entity_id_to_device_id[sensor_entity.id] = device.id

    if api.available_entities.contains(sensor_entity.id):
        api.available_entities.remove(sensor_entity.id)
    api.available_entities.add(sensor_entity)

    _LOG.info("Registered group sensor entity: %s", sensor_entity.id)

    # Create remote entity (control surface delegating to the media player)
    remote_entity = BluOSRemote(device, player, entity)
    _remote_entities[device.id] = remote_entity
    _remote_entity_id_to_device_id[remote_entity.id] = device.id

    if api.available_entities.contains(remote_entity.id):
        api.available_entities.remove(remote_entity.id)
    api.available_entities.add(remote_entity)

    _LOG.info("Registered remote entity: %s", remote_entity.id)

    # Connect if not in standby
    if not _REMOTE_IN_STANDBY:
        await player.connect()


async def _remove_player(device_id: str) -> None:
    """Remove a BluOS player."""
    if device_id in _configured_players:
        player = _configured_players.pop(device_id)
        await player.disconnect()

    if device_id in _entities:
        entity = _entities.pop(device_id)
        _entity_id_to_device_id.pop(entity.id, None)
        if api.available_entities.contains(entity.id):
            api.available_entities.remove(entity.id)
        if api.configured_entities.contains(entity.id):
            api.configured_entities.remove(entity.id)

    if device_id in _select_entities:
        select_entity = _select_entities.pop(device_id)
        _select_entity_id_to_device_id.pop(select_entity.id, None)
        if api.available_entities.contains(select_entity.id):
            api.available_entities.remove(select_entity.id)
        if api.configured_entities.contains(select_entity.id):
            api.configured_entities.remove(select_entity.id)

    if device_id in _sensor_entities:
        sensor_entity = _sensor_entities.pop(device_id)
        _sensor_entity_id_to_device_id.pop(sensor_entity.id, None)
        if api.available_entities.contains(sensor_entity.id):
            api.available_entities.remove(sensor_entity.id)
        if api.configured_entities.contains(sensor_entity.id):
            api.configured_entities.remove(sensor_entity.id)

    if device_id in _remote_entities:
        remote_entity = _remote_entities.pop(device_id)
        _remote_entity_id_to_device_id.pop(remote_entity.id, None)
        if api.available_entities.contains(remote_entity.id):
            api.available_entities.remove(remote_entity.id)
        if api.configured_entities.contains(remote_entity.id):
            api.configured_entities.remove(remote_entity.id)


def _any_player_connected() -> bool:
    """Check if any player is currently connected."""
    return any(player.available for player in _configured_players.values())


async def _set_device_state(state: ucapi.DeviceStates) -> None:
    """Push device state to the remote, skipping the call if it is unchanged.

    ucapi notifies the remote on every set_device_state() call even when the
    value is the same; suppressing redundant pushes avoids waking the remote
    from low-power mode unnecessarily.
    """
    global _last_device_state
    if state == _last_device_state:
        return
    _last_device_state = state
    await api.set_device_state(state)


async def _update_device_state() -> None:
    """Update the integration device state based on player connections."""
    connected = _any_player_connected()
    new_state = ucapi.DeviceStates.CONNECTED if connected else ucapi.DeviceStates.DISCONNECTED
    _LOG.debug("Updating device state to %s (any player connected: %s)", new_state, connected)
    await _set_device_state(new_state)


def _on_player_connected(device_id: str) -> None:
    """Handle player connected event."""
    _LOG.info("Player connected: %s", device_id)
    if _LOG.isEnabledFor(logging.DEBUG):
        for pid, player in _configured_players.items():
            _LOG.debug("Player %s available: %s", pid, player.available)

    if _REMOTE_IN_STANDBY:
        _LOG.debug("Remote in standby, skipping entity updates on player connect")
        return

    _LOOP.create_task(_update_device_state())

    if device_id in _entities:
        entity = _entities[device_id]
        # Update simple commands with current presets
        entity.update_options()
        # Trigger initial status poll
        _LOOP.create_task(_poll_player(device_id))

    # Refresh the remote's commands/UI (presets now loaded) and mark it on
    if device_id in _remote_entities:
        remote_entity = _remote_entities[device_id]
        remote_entity.update_options()
        changed = remote_entity.update_attributes({})
        if changed:
            api.configured_entities.update_attributes(remote_entity.id, changed)

    # Update select entity options now that presets are loaded
    if device_id in _select_entities:
        select_entity = _select_entities[device_id]
        player = _configured_players[device_id]
        _LOG.debug(
            "Refreshing select entity options for %s with %d presets: %s",
            device_id,
            len(player.presets),
            [p.name for p in player.presets],
        )
        changed = select_entity.refresh_options()
        if changed:
            _LOG.info(
                "Select entity %s options updated: %s",
                select_entity.id,
                select_entity.attributes.get("options", []),
            )
            # Update configured_entities to notify UC of the change
            if api.configured_entities.contains(select_entity.id):
                api.configured_entities.update_attributes(select_entity.id, changed)
            # Also update available_entities to ensure new subscriptions get correct data
            if api.available_entities.contains(select_entity.id):
                api.available_entities.update_attributes(select_entity.id, changed)


def _on_player_disconnected(device_id: str) -> None:
    """Handle player disconnected event."""
    _LOG.info("Player disconnected: %s", device_id)

    if _REMOTE_IN_STANDBY:
        _LOG.debug("Remote in standby, skipping entity updates on player disconnect")
        return

    _LOOP.create_task(_update_device_state())

    if device_id in _entities:
        entity = _entities[device_id]
        changed = entity.set_unavailable()
        if changed:
            api.configured_entities.update_attributes(entity.id, changed)

    # Set select entity unavailable
    if device_id in _select_entities:
        select_entity = _select_entities[device_id]
        changed = select_entity.set_unavailable()
        if changed:
            api.configured_entities.update_attributes(select_entity.id, changed)

    # Set group sensor unavailable
    if device_id in _sensor_entities:
        sensor_entity = _sensor_entities[device_id]
        changed = sensor_entity.set_unavailable()
        if changed:
            api.configured_entities.update_attributes(sensor_entity.id, changed)

    # Set remote entity unavailable
    if device_id in _remote_entities:
        remote_entity = _remote_entities[device_id]
        changed = remote_entity.set_unavailable()
        if changed:
            api.configured_entities.update_attributes(remote_entity.id, changed)


def _on_player_update(device_id: str, attributes: dict[str, Any]) -> None:
    """Handle player update event."""
    if _REMOTE_IN_STANDBY:
        return

    if device_id in _entities:
        entity = _entities[device_id]
        changed = entity.update_attributes(attributes)
        if changed:
            api.configured_entities.update_attributes(entity.id, changed)

    # Update select entity attributes
    if device_id in _select_entities:
        select_entity = _select_entities[device_id]
        changed = select_entity.update_attributes(attributes)
        if changed:
            api.configured_entities.update_attributes(select_entity.id, changed)

    # Update group sensor attributes
    if device_id in _sensor_entities:
        sensor_entity = _sensor_entities[device_id]
        changed = sensor_entity.update_attributes(attributes)
        if changed:
            api.configured_entities.update_attributes(sensor_entity.id, changed)

    # Update remote entity state
    if device_id in _remote_entities:
        remote_entity = _remote_entities[device_id]
        changed = remote_entity.update_attributes(attributes)
        if changed:
            api.configured_entities.update_attributes(remote_entity.id, changed)


async def _poll_player(device_id: str) -> None:
    """Poll a single player for status."""
    if device_id not in _configured_players:
        return

    player = _configured_players[device_id]
    await player.poll_status(use_etag=False)  # Initial poll without etag


async def _poll_single_player(device_id: str, player: BluOSPlayer) -> bool:
    """Poll a single player's status. Returns True if polling succeeded."""
    try:
        await player.poll_status(use_etag=True)
        return True
    except PlayerUnreachableError as e:
        _LOG.warning("Player %s unreachable: %s", device_id, e)
        return False
    except PlayerError as e:
        _LOG.error("Player error polling %s: %s", device_id, e)
        return False
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        _LOG.warning("Network error polling %s: %s", device_id, e)
        return False


async def _reconnect_player(device_id: str, player: BluOSPlayer) -> None:
    """Attempt to reconnect an unavailable player."""
    _LOG.debug("Player %s unavailable, attempting reconnect", device_id)
    try:
        connected = await player.connect()
        if connected:
            _LOG.info("Reconnected to player %s via poller", device_id)
    except PlayerUnreachableError as e:
        _LOG.debug("Player %s still unreachable: %s", device_id, e)
    except PlayerError as e:
        _LOG.debug("Reconnect attempt failed for %s: %s", device_id, e)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        _LOG.debug("Network error reconnecting %s: %s", device_id, e)


async def _status_poller() -> None:
    """Background task to poll player status using long-polling."""
    while True:
        # Suspend completely during standby — no CPU wake-ups until the remote wakes up.
        await _poller_active.wait()

        if not _configured_players:
            await asyncio.sleep(NO_PLAYERS_POLL_INTERVAL)
            continue

        # Separate available and unavailable players
        available_players = [
            (device_id, player) for device_id, player in list(_configured_players.items()) if player.available
        ]
        # Only attempt reconnect for players that don't already have a reconnect task running,
        # to avoid racing with the exponential-backoff reconnect scheduled inside BluOSPlayer.
        unavailable_players = [
            (device_id, player)
            for device_id, player in list(_configured_players.items())
            if not player.available and not player.is_reconnecting
        ]

        # Poll all available players in parallel. Tasks are created explicitly
        # (rather than handing coroutines straight to gather) so that
        # _on_enter_standby() can cancel them mid-flight.
        polled_any = False
        if available_players:
            global _active_poll_tasks
            _active_poll_tasks = [
                _LOOP.create_task(_poll_single_player(device_id, player)) for device_id, player in available_players
            ]
            try:
                results = await asyncio.gather(*_active_poll_tasks, return_exceptions=True)
            finally:
                _active_poll_tasks = []
            # Cancelled polls (e.g. on standby) come back as CancelledError, not True.
            polled_any = any(r is True for r in results)

        # Attempt to reconnect unavailable players that have no active reconnect task
        if unavailable_players:
            await asyncio.gather(
                *[_reconnect_player(device_id, player) for device_id, player in unavailable_players],
                return_exceptions=True,
            )

        # Small delay if no players were polled to prevent tight loop during reconnection
        if not polled_any:
            await asyncio.sleep(NO_PLAYERS_POLL_INTERVAL)


# UC API Event Handlers


async def _connect_unavailable_players() -> None:
    """Connect all players that are currently unavailable."""
    players_to_connect = [p for p in _configured_players.values() if not p.available]
    if players_to_connect:
        await _set_device_state(ucapi.DeviceStates.CONNECTING)
        await asyncio.gather(
            *[player.connect() for player in players_to_connect],
            return_exceptions=True,
        )


@api.listens_to(ucapi.Events.CONNECT)
async def _on_connect() -> None:
    """Handle UC Remote connect event."""
    global _last_device_state
    _LOG.info("UC Remote connected")
    # Force the next push so a freshly (re)connected remote always receives the
    # current device state, even if it matches the last value we sent.
    _last_device_state = None
    await _connect_unavailable_players()
    await _update_device_state()


@api.listens_to(ucapi.Events.DISCONNECT)
async def _on_disconnect() -> None:
    """Handle UC Remote disconnect event."""
    _LOG.info("UC Remote disconnected")


@api.listens_to(ucapi.Events.ENTER_STANDBY)
async def _on_enter_standby() -> None:
    """Handle UC Remote entering standby."""
    global _REMOTE_IN_STANDBY
    _LOG.info("UC Remote entering standby")
    _REMOTE_IN_STANDBY = True
    _poller_active.clear()  # Suspend the status poller completely during standby
    # Cancel any in-flight long-polls so their HTTP connections to the BluOS
    # devices are released before we tear the sessions down.
    for task in _active_poll_tasks:
        task.cancel()
    # Fully disconnect every player: closes the aiohttp session and stops the
    # volume/mute workers, matching the disconnect-on-standby convention used by
    # the other UC integrations. A connection held open across the remote's
    # suspend tends to come back stale and only fails after wake; tearing it down
    # now means we reconnect cleanly on EXIT_STANDBY. disconnect() also cancels
    # any pending reconnect backoff, so the explicit cancel_reconnect() loop is
    # no longer needed.
    await asyncio.gather(
        *[player.disconnect() for player in _configured_players.values()],
        return_exceptions=True,
    )
    await _set_device_state(ucapi.DeviceStates.DISCONNECTED)


@api.listens_to(ucapi.Events.EXIT_STANDBY)
async def _on_exit_standby() -> None:
    """Handle UC Remote exiting standby."""
    global _REMOTE_IN_STANDBY
    _LOG.info("UC Remote exiting standby")
    _REMOTE_IN_STANDBY = False
    _poller_active.set()  # Resume the status poller
    await _connect_unavailable_players()
    await _update_device_state()

    # Force refresh status for all available players
    # Clear cached attributes so all current values are sent to the remote
    for device_id, entity in _entities.items():
        entity.clear_cached_attributes()
        if device_id in _select_entities:
            _select_entities[device_id].clear_cached_attributes()
        if device_id in _sensor_entities:
            _sensor_entities[device_id].clear_cached_attributes()
        if device_id in _remote_entities:
            _remote_entities[device_id].clear_cached_attributes()

    # Parallel status refresh for all available players
    available_players = [(device_id, player) for device_id, player in _configured_players.items() if player.available]
    if available_players:
        _LOG.debug("Refreshing status for %d players after standby exit", len(available_players))
        await asyncio.gather(
            *[player.poll_status(use_etag=False) for _, player in available_players],
            return_exceptions=True,
        )


@api.listens_to(ucapi.Events.SUBSCRIBE_ENTITIES)
async def _on_subscribe_entities(entity_ids: list[str]) -> None:
    """Handle entity subscription."""
    _LOG.info("Subscribed to entities: %s", entity_ids)

    for entity_id in entity_ids:
        # O(1) lookup via reverse maps populated when entities are registered
        if device_id := _entity_id_to_device_id.get(entity_id):
            if device_id in _configured_players:
                player = _configured_players[device_id]
                # Clear cached state so the next poll pushes all attributes to the remote,
                # even if the entity was polled before this subscription (e.g. during setup).
                _entities[device_id].clear_cached_attributes()
                if not player.available:
                    await player.connect()
                else:
                    await player.poll_status(use_etag=False)
            continue

        if device_id := _select_entity_id_to_device_id.get(entity_id):
            select_entity = _select_entities[device_id]
            if device_id in _configured_players:
                player = _configured_players[device_id]
                if player.available and player.presets:
                    changed = select_entity.refresh_options()
                    if changed:
                        api.configured_entities.update_attributes(select_entity.id, changed)
                    else:
                        # Even if no change, send current attributes to ensure UC has them
                        api.configured_entities.update_attributes(select_entity.id, select_entity.attributes)
            continue

        if device_id := _sensor_entity_id_to_device_id.get(entity_id):
            sensor_entity = _sensor_entities[device_id]
            # Push current group state; the next poll refreshes it.
            sensor_entity.clear_cached_attributes()
            api.configured_entities.update_attributes(sensor_entity.id, sensor_entity.attributes)
            continue

        if device_id := _remote_entity_id_to_device_id.get(entity_id):
            remote_entity = _remote_entities[device_id]
            remote_entity.clear_cached_attributes()
            api.configured_entities.update_attributes(remote_entity.id, remote_entity.attributes)


@api.listens_to(ucapi.Events.UNSUBSCRIBE_ENTITIES)
async def _on_unsubscribe_entities(entity_ids: list[str]) -> None:
    """Handle entity unsubscription."""
    _LOG.info("Unsubscribed from entities: %s", entity_ids)


# Setup Flow Handler


async def _setup_handler(msg: ucapi.SetupDriver) -> ucapi.SetupAction:
    """Handle setup driver messages."""
    result = await setup_flow.driver_setup_handler(msg)

    # Check if setup completed with device data
    if isinstance(result, ucapi.SetupComplete):
        device = setup_flow.get_configured_device()
        if device and _devices is not None:
            # Add device to config (don't trigger callback - we'll await _add_player directly)
            is_new = _devices.add_or_update(device, trigger_callbacks=False)

            # Add player and wait for entity registration
            if is_new:
                _LOG.info("New device configured: %s (%s)", device.name, device.id)
                await _add_player(device)

    return result


# Entity Command Handler


async def _entity_command_handler(
    entity_type: EntityTypes,
    entity_id: str,
    cmd_id: str,
    params: dict[str, Any] | None,
) -> ucapi.StatusCodes:
    """Handle entity commands."""
    _LOG.debug("Command %s for %s (type: %s)", cmd_id, entity_id, entity_type)

    # O(1) lookup via reverse maps populated when entities are registered
    if device_id := _entity_id_to_device_id.get(entity_id):
        return await _entities[device_id].command(cmd_id, params)

    if device_id := _select_entity_id_to_device_id.get(entity_id):
        return await _select_entities[device_id].command(cmd_id, params)

    _LOG.warning("Entity not found: %s", entity_id)
    return ucapi.StatusCodes.NOT_FOUND


def _configure_logging() -> None:
    """Configure logging based on environment."""
    log_level = os.getenv("UC_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Reduce noise from libraries
    logging.getLogger("zeroconf").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


async def _main() -> None:
    """Main entry point."""
    global _devices

    _configure_logging()
    _LOG.info("Starting BluOS integration")
    _poller_active.set()  # Start in active state

    # Get configuration path
    config_home = os.getenv("UC_CONFIG_HOME", os.path.join(os.getcwd(), "data"))
    _LOG.info("Configuration path: %s", config_home)

    # Initialize device configuration
    _devices = Devices(
        config_home,
        add_handler=_on_device_added,
        remove_handler=_on_device_removed,
    )
    _devices.load()
    setup_flow.set_devices(_devices)

    # Register existing devices
    existing_devices = _devices.all()
    if existing_devices:
        await _set_device_state(ucapi.DeviceStates.CONNECTING)
    for device in existing_devices:
        await _add_player(device)

    # Update device state after initial player setup
    await _update_device_state()

    # Set up entity command handler
    api.entity_command_handler = _entity_command_handler

    # Start background status poller
    _LOOP.create_task(_status_poller())

    # Run the integration API with setup handler
    await api.init(_get_driver_path(), _setup_handler)


if __name__ == "__main__":
    try:
        _LOOP.run_until_complete(_main())
        _LOOP.run_forever()
    except KeyboardInterrupt:
        _LOG.info("Shutting down...")
    finally:
        for player in _configured_players.values():
            _LOOP.run_until_complete(player.disconnect())
        _LOOP.close()
