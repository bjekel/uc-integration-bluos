"""BluOS device wrapper using pyblu library."""

import asyncio
import logging
from enum import StrEnum
from typing import Any, Optional

from config import BluOSDevice
from pyblu import Input, Player, Preset, Status, SyncStatus
from pyblu.errors import PlayerError, PlayerUnreachableError
from pyee.asyncio import AsyncIOEventEmitter

_LOG = logging.getLogger(__name__)

# Retry configuration
MIN_RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 30.0
BACKOFF_FACTOR = 2.0


class Events(StrEnum):
    """Events emitted by BluOSPlayer."""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    UPDATE = "update"


class States(StrEnum):
    """Player states."""

    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"
    OFF = "OFF"
    ON = "ON"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    BUFFERING = "BUFFERING"


class BluOSPlayer:
    """Wrapper for pyblu Player with event emission and connection management."""

    def __init__(self, device: BluOSDevice, loop: asyncio.AbstractEventLoop):
        """
        Initialize BluOS player wrapper.

        Args:
            device: Device configuration
            loop: Event loop for async operations
        """
        self._device = device
        self._loop = loop
        self._player: Optional[Player] = None
        self._events = AsyncIOEventEmitter()
        self._available = False
        self._connecting = False
        self._state = States.UNKNOWN
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reconnect_delay = MIN_RECONNECT_DELAY
        self._last_etag: Optional[str] = None
        self._inputs: list[Input] = []
        self._presets: list[Preset] = []

    @property
    def id(self) -> str:
        """Device ID."""
        return self._device.id

    @property
    def name(self) -> str:
        """Device name."""
        return self._device.name

    @property
    def device(self) -> BluOSDevice:
        """Device configuration."""
        return self._device

    @property
    def events(self) -> AsyncIOEventEmitter:
        """Event emitter for state changes."""
        return self._events

    @property
    def available(self) -> bool:
        """Whether the device is available."""
        return self._available

    @property
    def state(self) -> States:
        """Current player state."""
        return self._state

    @property
    def inputs(self) -> list[Input]:
        """Available inputs."""
        return self._inputs

    @property
    def presets(self) -> list[Preset]:
        """Available presets."""
        return self._presets

    async def connect(self) -> bool:
        """
        Connect to the BluOS player.

        Returns:
            True if connected successfully, False otherwise
        """
        if self._connecting:
            return False

        self._connecting = True
        self._events.emit(Events.CONNECTING)

        try:
            self._player = Player(
                self._device.address,
                self._device.port,
                default_timeout=self._device.timeout,
            )

            # Validate connection
            await self._player.sync_status()

            # Load inputs and presets
            await self._load_sources()

            self._available = True
            self._reconnect_delay = MIN_RECONNECT_DELAY
            self._connecting = False
            _LOG.info("Connected to %s at %s, emitting CONNECTED event", self._device.name, self._device.address)
            self._events.emit(Events.CONNECTED)
            return True

        except PlayerUnreachableError as e:
            _LOG.warning("Cannot reach %s: %s", self._device.name, e)
            self._available = False
            self._connecting = False
            self._state = States.UNAVAILABLE
            self._events.emit(Events.ERROR, str(e))
            self._schedule_reconnect()
            return False

        except PlayerError as e:
            _LOG.error("Error connecting to %s: %s", self._device.name, e)
            self._available = False
            self._connecting = False
            self._events.emit(Events.ERROR, str(e))
            self._schedule_reconnect()
            return False

    async def disconnect(self) -> None:
        """Disconnect from the BluOS player."""
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None

        if self._player:
            try:
                await self._player.close()
            except Exception as e:
                _LOG.debug("Error closing player connection: %s", e)
            self._player = None

        self._available = False
        self._connecting = False
        self._state = States.UNAVAILABLE
        self._events.emit(Events.DISCONNECTED)
        _LOG.info("Disconnected from %s", self._device.name)

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt with exponential backoff."""
        if self._reconnect_task and not self._reconnect_task.done():
            return

        async def reconnect() -> None:
            _LOG.debug(
                "Scheduling reconnect to %s in %.1f seconds",
                self._device.name,
                self._reconnect_delay,
            )
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * BACKOFF_FACTOR, MAX_RECONNECT_DELAY)
            try:
                await self.connect()
            except Exception as e:
                # Catch-all to ensure reconnect keeps trying
                _LOG.error("Unexpected error during reconnect to %s: %s", self._device.name, e)
                self._schedule_reconnect()

        self._reconnect_task = self._loop.create_task(reconnect())

    async def _load_sources(self) -> None:
        """Load available inputs and presets."""
        if not self._player:
            return

        try:
            self._inputs = await self._player.inputs()
            _LOG.debug("Loaded %d inputs for %s", len(self._inputs), self._device.name)
        except PlayerError as e:
            _LOG.warning("Failed to load inputs: %s", e)
            self._inputs = []

        try:
            self._presets = await self._player.presets()
            _LOG.debug("Loaded %d presets for %s", len(self._presets), self._device.name)
        except PlayerError as e:
            _LOG.warning("Failed to load presets: %s", e)
            self._presets = []

    async def poll_status(self, use_etag: bool = True) -> Optional[dict[str, Any]]:
        """
        Poll for status updates using long-polling.

        Args:
            use_etag: Whether to use etag for long-polling

        Returns:
            Dictionary of current attributes or None if unavailable
        """
        if not self._player or not self._available:
            return None

        try:
            etag = self._last_etag if use_etag else None
            status = await self._player.status(etag=etag, poll_timeout=10, timeout=15)
            self._last_etag = status.etag

            attributes = self._status_to_attributes(status)
            new_state = self._map_state(status.state)

            if new_state != self._state:
                self._state = new_state
                _LOG.debug("%s state changed to %s", self._device.name, new_state)

            self._events.emit(Events.UPDATE, self.id, attributes)
            return attributes

        except PlayerUnreachableError as e:
            _LOG.warning("Lost connection to %s: %s", self._device.name, e)
            self._available = False
            self._state = States.UNAVAILABLE
            self._events.emit(Events.DISCONNECTED)
            self._schedule_reconnect()
            return None

        except PlayerError as e:
            _LOG.error("Error polling %s: %s", self._device.name, e)
            return None

    def _status_to_attributes(self, status: Status) -> dict[str, Any]:
        """Convert pyblu Status to UC attributes."""
        return {
            "state": self._map_state(status.state),
            "volume": status.volume,
            "muted": status.mute,
            "media_title": status.name or "",
            "media_artist": status.artist or "",
            "media_album": status.album or "",
            "media_image_url": status.image or "",
            "media_duration": status.total_seconds or 0,
            "media_position": status.seconds or 0,
            "shuffle": status.shuffle or False,
            "source": status.input_id or "",
        }

    @staticmethod
    def _map_state(bluos_state: Optional[str]) -> States:
        """Map BluOS state to UC state."""
        if not bluos_state:
            return States.UNKNOWN

        state_map = {
            "play": States.PLAYING,
            "stream": States.PLAYING,
            "pause": States.PAUSED,
            "stop": States.ON,
            "connecting": States.BUFFERING,
        }
        return state_map.get(bluos_state.lower(), States.ON)

    # Playback control methods

    async def play(self) -> bool:
        """Start playback."""
        if not self._player or not self._available:
            return False
        try:
            await self._player.play()
            return True
        except PlayerError as e:
            _LOG.error("Play failed: %s", e)
            return False

    async def pause(self) -> bool:
        """Pause playback."""
        if not self._player or not self._available:
            return False
        try:
            await self._player.pause()
            return True
        except PlayerError as e:
            _LOG.error("Pause failed: %s", e)
            return False

    async def stop(self) -> bool:
        """Stop playback."""
        if not self._player or not self._available:
            return False
        try:
            await self._player.stop()
            return True
        except PlayerError as e:
            _LOG.error("Stop failed: %s", e)
            return False

    async def next_track(self) -> bool:
        """Skip to next track."""
        if not self._player or not self._available:
            return False
        try:
            await self._player.skip()
            return True
        except PlayerError as e:
            _LOG.error("Skip failed: %s", e)
            return False

    async def previous_track(self) -> bool:
        """Go to previous track."""
        if not self._player or not self._available:
            return False
        try:
            await self._player.back()
            return True
        except PlayerError as e:
            _LOG.error("Back failed: %s", e)
            return False

    async def set_volume(self, level: int) -> bool:
        """
        Set volume level.

        Args:
            level: Volume level 0-100
        """
        if not self._player or not self._available:
            return False
        try:
            await self._player.volume(level=max(0, min(100, level)))
            return True
        except PlayerError as e:
            _LOG.error("Set volume failed: %s", e)
            return False

    async def volume_up(self) -> bool:
        """Increase volume by configured step."""
        if not self._player or not self._available:
            return False
        try:
            status = await self._player.status()
            new_level = min(100, (status.volume or 0) + self._device.volume_step)
            await self._player.volume(level=new_level)
            return True
        except PlayerError as e:
            _LOG.error("Volume up failed: %s", e)
            return False

    async def volume_down(self) -> bool:
        """Decrease volume by configured step."""
        if not self._player or not self._available:
            return False
        try:
            status = await self._player.status()
            new_level = max(0, (status.volume or 0) - self._device.volume_step)
            await self._player.volume(level=new_level)
            return True
        except PlayerError as e:
            _LOG.error("Volume down failed: %s", e)
            return False

    async def mute(self, muted: bool) -> bool:
        """
        Set mute state.

        Args:
            muted: True to mute, False to unmute
        """
        if not self._player or not self._available:
            return False
        try:
            await self._player.volume(mute=muted)
            return True
        except PlayerError as e:
            _LOG.error("Mute failed: %s", e)
            return False

    async def toggle_mute(self) -> bool:
        """Toggle mute state."""
        if not self._player or not self._available:
            return False
        try:
            status = await self._player.status()
            await self._player.volume(mute=not status.mute)
            return True
        except PlayerError as e:
            _LOG.error("Toggle mute failed: %s", e)
            return False

    async def set_shuffle(self, enabled: bool) -> bool:
        """
        Set shuffle mode.

        Args:
            enabled: True to enable shuffle
        """
        if not self._player or not self._available:
            return False
        try:
            await self._player.shuffle(enabled)
            return True
        except PlayerError as e:
            _LOG.error("Set shuffle failed: %s", e)
            return False

    async def select_source(self, source_id: str) -> bool:
        """
        Select input source or preset.

        Args:
            source_id: Source identifier (input ID or preset ID prefixed with 'preset:')
        """
        if not self._player or not self._available:
            return False

        try:
            # Check if it's a preset
            if source_id.startswith("preset:"):
                preset_id = source_id[7:]  # Remove 'preset:' prefix
                await self._player.load_preset(int(preset_id))
                return True

            # Find input by ID or name
            for inp in self._inputs:
                if inp.id == source_id or inp.text == source_id:
                    await self._player.play_url(inp.url)
                    return True

            _LOG.warning("Source not found: %s", source_id)
            return False

        except PlayerError as e:
            _LOG.error("Select source failed: %s", e)
            return False

    def get_source_list(self) -> list[str]:
        """Get list of available sources (inputs + presets)."""
        sources = []

        # Add inputs
        for inp in self._inputs:
            sources.append(inp.id or inp.text)

        # Add presets with prefix
        for preset in self._presets:
            sources.append(f"preset:{preset.id}")

        return sources

    # Multi-room grouping methods

    async def get_sync_status(self) -> Optional[SyncStatus]:
        """Get multi-room synchronization status."""
        if not self._player or not self._available:
            return None
        try:
            return await self._player.sync_status()
        except PlayerError as e:
            _LOG.error("Get sync status failed: %s", e)
            return None

    async def add_follower(self, ip: str, port: int = 11000) -> bool:
        """
        Add a player to this group.

        Args:
            ip: IP address of the player to add
            port: Port of the player
        """
        if not self._player or not self._available:
            return False
        try:
            await self._player.add_follower(ip, port)
            return True
        except PlayerError as e:
            _LOG.error("Add follower failed: %s", e)
            return False

    async def remove_follower(self, ip: str, port: int = 11000) -> bool:
        """
        Remove a player from this group.

        Args:
            ip: IP address of the player to remove
            port: Port of the player
        """
        if not self._player or not self._available:
            return False
        try:
            await self._player.remove_follower(ip, port)
            return True
        except PlayerError as e:
            _LOG.error("Remove follower failed: %s", e)
            return False
