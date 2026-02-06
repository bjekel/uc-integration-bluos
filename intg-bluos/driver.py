#!/usr/bin/env python3
"""BluOS integration driver for Unfolded Circle Remote."""

import asyncio
import logging
import os
import sys
from typing import Any

import setup_flow
import ucapi
from bluos import BluOSPlayer
from bluos import Events as BluOSEvents
from config import BluOSDevice, Devices
from media_player import BluOSMediaPlayer
from ucapi import EntityTypes

_LOG = logging.getLogger(__name__)

# Event loop
_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_LOOP)

# Integration API
api = ucapi.IntegrationAPI(_LOOP)

# Configured players and entities
_configured_players: dict[str, BluOSPlayer] = {}
_entities: dict[str, BluOSMediaPlayer] = {}
_devices: Devices | None = None

# Remote state
_REMOTE_IN_STANDBY = False


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
    entity = BluOSMediaPlayer(device, player)
    _entities[device.id] = entity

    # Register entity with API
    if api.available_entities.contains(entity.id):
        api.available_entities.remove(entity.id)
    api.available_entities.add(entity)

    _LOG.info("Registered entity: %s", entity.id)

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
        if api.available_entities.contains(entity.id):
            api.available_entities.remove(entity.id)
        if api.configured_entities.contains(entity.id):
            api.configured_entities.remove(entity.id)


def _any_player_connected() -> bool:
    """Check if any player is currently connected."""
    return any(player.available for player in _configured_players.values())


async def _update_device_state() -> None:
    """Update the integration device state based on player connections."""
    connected = _any_player_connected()
    new_state = ucapi.DeviceStates.CONNECTED if connected else ucapi.DeviceStates.DISCONNECTED
    _LOG.debug("Updating device state to %s (any player connected: %s)", new_state, connected)
    await api.set_device_state(new_state)


def _on_player_connected(device_id: str) -> None:
    """Handle player connected event."""
    _LOG.info("Player connected: %s", device_id)
    # Log current player states for debugging
    for pid, player in _configured_players.items():
        _LOG.debug("Player %s available: %s", pid, player.available)
    # Update integration device state
    _LOOP.create_task(_update_device_state())
    if device_id in _entities:
        # Trigger initial status poll
        _LOOP.create_task(_poll_player(device_id))


def _on_player_disconnected(device_id: str) -> None:
    """Handle player disconnected event."""
    _LOG.info("Player disconnected: %s", device_id)
    # Update integration device state
    _LOOP.create_task(_update_device_state())
    if device_id in _entities:
        entity = _entities[device_id]
        changed = entity.set_unavailable()
        if changed:
            api.configured_entities.update_attributes(entity.id, changed)


def _on_player_update(device_id: str, attributes: dict[str, Any]) -> None:
    """Handle player update event."""
    if device_id in _entities:
        entity = _entities[device_id]
        changed = entity.update_attributes(attributes)
        if changed:
            api.configured_entities.update_attributes(entity.id, changed)


async def _poll_player(device_id: str) -> None:
    """Poll a single player for status."""
    if device_id not in _configured_players:
        return

    player = _configured_players[device_id]
    await player.poll_status(use_etag=False)  # Initial poll without etag


async def _status_poller(interval: float = 10.0) -> None:
    """Background task to poll player status."""
    while True:
        if _REMOTE_IN_STANDBY:
            await asyncio.sleep(interval)
            continue

        for device_id, player in list(_configured_players.items()):
            if player.available:
                try:
                    await player.poll_status(use_etag=True)
                except Exception as e:
                    _LOG.error("Error polling %s: %s", device_id, e)
            else:
                # Try to reconnect unavailable players
                _LOG.debug("Player %s unavailable, attempting reconnect", device_id)
                try:
                    connected = await player.connect()
                    if connected:
                        _LOG.info("Reconnected to player %s via poller", device_id)
                except Exception as e:
                    _LOG.debug("Reconnect attempt failed for %s: %s", device_id, e)

        await asyncio.sleep(interval)


# UC API Event Handlers


@api.listens_to(ucapi.Events.CONNECT)
async def _on_connect() -> None:
    """Handle UC Remote connect event."""
    _LOG.info("UC Remote connected")

    # Set connecting state if we have players to connect
    players_to_connect = [p for p in _configured_players.values() if not p.available]
    if players_to_connect:
        await api.set_device_state(ucapi.DeviceStates.CONNECTING)

    for player in players_to_connect:
        await player.connect()

    # Update device state after connection attempts
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
    # Report disconnected state during standby
    await api.set_device_state(ucapi.DeviceStates.DISCONNECTED)


@api.listens_to(ucapi.Events.EXIT_STANDBY)
async def _on_exit_standby() -> None:
    """Handle UC Remote exiting standby."""
    global _REMOTE_IN_STANDBY
    _LOG.info("UC Remote exiting standby")
    _REMOTE_IN_STANDBY = False

    # Set connecting state if we have players to reconnect
    players_to_connect = [p for p in _configured_players.values() if not p.available]
    if players_to_connect:
        await api.set_device_state(ucapi.DeviceStates.CONNECTING)

    # Reconnect players
    for player in players_to_connect:
        await player.connect()

    # Update device state after reconnection attempts
    await _update_device_state()


@api.listens_to(ucapi.Events.SUBSCRIBE_ENTITIES)
async def _on_subscribe_entities(entity_ids: list[str]) -> None:
    """Handle entity subscription."""
    _LOG.info("Subscribed to entities: %s", entity_ids)

    for entity_id in entity_ids:
        # Find the device ID from entity ID
        for device_id, entity in _entities.items():
            if entity.id == entity_id:
                if device_id in _configured_players:
                    player = _configured_players[device_id]
                    if not player.available:
                        await player.connect()
                    else:
                        await player.poll_status(use_etag=False)
                break


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
            # Check if this is a new device before adding
            is_new = not _devices.contains(device.id)

            # Add device to config (don't use callback for new devices - we'll await it)
            _devices._devices[device.id] = device
            _devices.store()

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

    # Find the entity
    for device_id, entity in _entities.items():
        if entity.id == entity_id:
            return await entity.command(cmd_id, params)

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

    # Register existing devices
    if _devices.all():
        await api.set_device_state(ucapi.DeviceStates.CONNECTING)
    for device in _devices.all():
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
