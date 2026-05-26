"""BluOS device wrapper using pyblu library."""

import asyncio
import logging
import time
import xml.etree.ElementTree as ET
from enum import StrEnum
from typing import Any
from urllib.parse import unquote

import aiohttp
from config import BluOSDevice
from pyblu import Input, Player, Preset, Status, SyncStatus
from pyblu.errors import PlayerError, PlayerUnreachableError
from pyee.asyncio import AsyncIOEventEmitter
from yarl import URL as YarlURL

_LOG = logging.getLogger(__name__)

# Retry configuration
MIN_RECONNECT_DELAY = 1.0
MAX_RECONNECT_DELAY = 30.0
BACKOFF_FACTOR = 2.0

# Preset command prefixes
PRESET_LEGACY_PREFIX = "preset:"
PRESET_COMMAND_PREFIX = "PRESET_"


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


class RepeatMode(StrEnum):
    """Repeat modes for BluOS playback."""

    OFF = "OFF"
    ALL = "ALL"
    ONE = "ONE"


_BLUOS_STATE_MAP: dict[str, States] = {
    "play": States.PLAYING,
    "stream": States.PLAYING,
    "pause": States.PAUSED,
    "stop": States.ON,
    "connecting": States.BUFFERING,
}

_REPEAT_API_MAP: dict[RepeatMode, str] = {
    RepeatMode.ALL: "0",
    RepeatMode.ONE: "1",
    RepeatMode.OFF: "2",
}

_REPEAT_NEXT_MAP: dict[RepeatMode, RepeatMode] = {
    RepeatMode.OFF: RepeatMode.ALL,
    RepeatMode.ALL: RepeatMode.ONE,
    RepeatMode.ONE: RepeatMode.OFF,
}


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
        self._player: Player | None = None
        self._events = AsyncIOEventEmitter()
        self._available = False
        self._connecting = False
        self._state = States.UNKNOWN
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_delay = MIN_RECONNECT_DELAY
        self._last_etag: str | None = None
        self._inputs: list[Input] = []
        self._presets: list[Preset] = []
        self._repeat_mode = RepeatMode.OFF
        self._sleep_timer = 0
        self._current_preset_name: str | None = None

        # Volume handling - worker queues for sequential processing
        self._volume_queue: asyncio.Queue[int | None] = asyncio.Queue()
        self._mute_queue: asyncio.Queue[bool | None] = asyncio.Queue()
        self._volume_worker_task: asyncio.Task | None = None
        self._mute_worker_task: asyncio.Task | None = None

        # Target state tracking - requested vs actual volume/mute state
        self._target_volume: int | None = None
        self._target_mute: bool | None = None

        # Volume debouncing - prevents UI jitter by ignoring duplicate updates
        self._last_volume_update: float | None = None  # timestamp of last volume update
        self._volume_debounce_ms: int = 100

        # Last known device state — avoids extra status() calls in commands
        self._last_known_volume: int | None = None
        self._last_known_mute: bool | None = None

        # Source list cache — invalidated when inputs/presets are reloaded
        self._source_list_cache: list[str] | None = None

        # Pending poll task — used to coalesce rapid _schedule_poll calls
        self._pending_poll_task: asyncio.Task | None = None

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

    @property
    def repeat_mode(self) -> RepeatMode:
        """Current repeat mode."""
        return self._repeat_mode

    @property
    def sleep_timer(self) -> int:
        """Current sleep timer in minutes (0 = off)."""
        return self._sleep_timer

    @property
    def current_preset_name(self) -> str | None:
        """Name of currently selected preset, or None if not playing a preset."""
        return self._current_preset_name

    @property
    def is_reconnecting(self) -> bool:
        """Whether a reconnect attempt is currently scheduled or in progress."""
        return self._reconnect_task is not None and not self._reconnect_task.done()

    def _is_available(self) -> bool:
        """Check if player is connected and available for commands."""
        return self._player is not None and self._available

    async def _raw_get(self, path: str, params: dict[str, str] | None = None, timeout: float = 10) -> str:
        """
        Send a GET request to {base_url}{path} and return the response body.

        This is the single point of contact with pyblu's private attributes
        (_session, base_url). If pyblu's internals change, only this method
        and _raw_get_play_url need updating.

        Raises:
            aiohttp.ClientError: On network or HTTP error.
        """
        url = f"{self._player.base_url}{path}"
        async with self._player._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            resp.raise_for_status()
            return await resp.text()

    async def _raw_get_play_url(self, play_url: str, timeout: float = 10) -> None:
        """
        Resolve and GET a BluOS playURL (may be relative or absolute).

        The URL is treated as already-encoded to avoid yarl re-encoding
        percent-escaped characters in the query string.

        Raises:
            aiohttp.ClientError: On network or HTTP error.
        """
        if play_url.startswith(("http://", "https://")):
            full_url = play_url
        else:
            base = self._player.base_url
            full_url = f"{base}{play_url}" if play_url.startswith("/") else f"{base}/{play_url}"
        async with self._player._session.get(
            YarlURL(full_url, encoded=True), timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            resp.raise_for_status()

    async def _volume_worker(self) -> None:
        """Process volume commands from queue sequentially, collapsing rapid changes."""
        while True:
            volume = await self._volume_queue.get()
            if volume is None:  # Shutdown signal
                break
            # Drain any intermediate values queued while we were waiting — only
            # the most recent target matters for the device.
            while not self._volume_queue.empty():
                next_val = self._volume_queue.get_nowait()
                self._volume_queue.task_done()
                if next_val is None:  # Shutdown signal in drain
                    volume = None
                    break
                volume = next_val
            if volume is None:
                break
            try:
                await self._player.volume(level=volume)
                await asyncio.sleep(0.1)  # Device processing delay
            except PlayerError as e:
                _LOG.error("Volume worker error: %s", e)
            finally:
                self._volume_queue.task_done()

    async def _mute_worker(self) -> None:
        """Process mute commands from queue sequentially."""
        while True:
            muted = await self._mute_queue.get()
            if muted is None:  # Shutdown signal
                break
            try:
                await self._player.volume(mute=muted)
                await asyncio.sleep(0.1)  # Device processing delay
            except PlayerError as e:
                _LOG.error("Mute worker error: %s", e)
            finally:
                self._mute_queue.task_done()

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

            # Start volume/mute worker tasks
            self._volume_worker_task = asyncio.create_task(self._volume_worker())
            self._mute_worker_task = asyncio.create_task(self._mute_worker())

            _LOG.info("Connected to %s at %s, emitting CONNECTED event", self._device.name, self._device.address)
            self._events.emit(Events.CONNECTED)
            return True

        except PlayerUnreachableError as e:
            _LOG.warning("Cannot reach %s: %s", self._device.name, e)
            self._available = False
            self._state = States.UNAVAILABLE
            self._events.emit(Events.DISCONNECTED)
            self._schedule_reconnect()
            return False

        except PlayerError as e:
            _LOG.error("Error connecting to %s: %s", self._device.name, e)
            self._available = False
            self._events.emit(Events.DISCONNECTED)
            self._schedule_reconnect()
            return False

        except Exception as e:
            _LOG.error("Unexpected error connecting to %s: %s", self._device.name, e)
            self._available = False
            self._events.emit(Events.DISCONNECTED)
            self._schedule_reconnect()
            return False

        finally:
            # Always reset _connecting, even if the task was cancelled mid-connect.
            # Without this, cancel_reconnect() during an in-progress connect() leaves
            # _connecting=True permanently, blocking all future reconnect attempts.
            self._connecting = False

    async def disconnect(self) -> None:
        """Disconnect from the BluOS player."""
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None

        # Stop volume/mute workers
        if self._volume_worker_task:
            await self._volume_queue.put(None)  # Shutdown signal
            try:
                await self._volume_worker_task
            except Exception:
                pass
            self._volume_worker_task = None
        if self._mute_worker_task:
            await self._mute_queue.put(None)  # Shutdown signal
            try:
                await self._mute_worker_task
            except Exception:
                pass
            self._mute_worker_task = None

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

    def cancel_reconnect(self) -> None:
        """Cancel any pending reconnect task and reset the backoff delay."""
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            self._reconnect_task = None
        self._reconnect_delay = MIN_RECONNECT_DELAY

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

        self._source_list_cache = None

    async def poll_status(self, use_etag: bool = True) -> dict[str, Any] | None:
        """
        Poll for status updates using long-polling.

        Args:
            use_etag: Whether to use etag for long-polling

        Returns:
            Dictionary of current attributes or None if unavailable
        """
        if not self._is_available():
            return None

        try:
            etag = self._last_etag if use_etag else None
            # Use a shorter timeout when actively playing/paused so state changes
            # (track change, pause) are reflected sooner. Fall back to the longer
            # standby_timeout when the player is idle/stopped.
            active_states = {States.PLAYING, States.PAUSED, States.BUFFERING}
            poll_timeout = (
                self._device.active_poll_timeout if self._state in active_states else self._device.standby_timeout
            )
            status = await self._player.status(etag=etag, poll_timeout=poll_timeout, timeout=poll_timeout + 5)
            self._last_etag = status.etag

            attributes = self._status_to_attributes(status)
            new_state = attributes["state"]

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

    def get_absolute_image_url(self, image: str | None) -> str:
        """Convert relative image URL to absolute URL."""
        if not image:
            return ""
        if image.startswith(("http://", "https://")):
            return image
        base = f"http://{self._device.address}:{self._device.port}"
        return f"{base}{image}" if image.startswith("/") else f"{base}/{image}"

    def _status_to_attributes(self, status: Status) -> dict[str, Any]:
        """Convert pyblu Status to UC attributes."""
        image_url = self.get_absolute_image_url(status.image)
        # Update sleep timer from status
        self._sleep_timer = status.sleep or 0

        # Cache raw device values so commands can use them without an extra status() call
        if status.volume is not None:
            self._last_known_volume = status.volume
        if status.mute is not None:
            self._last_known_mute = status.mute

        # Volume debouncing - use target if recently set to prevent UI jitter
        volume = status.volume
        if self._target_volume is not None:
            if self._last_volume_update is not None:
                if (time.time() - self._last_volume_update) * 1000 < self._volume_debounce_ms:
                    volume = self._target_volume
            # Clear target if device caught up
            if status.volume == self._target_volume:
                self._target_volume = None
                self._last_volume_update = None

        # Mute state tracking - use target if set
        muted = status.mute
        if self._target_mute is not None:
            muted = self._target_mute
            # Clear target if device caught up
            if status.mute == self._target_mute:
                self._target_mute = None

        return {
            "state": self._map_state(status.state),
            "volume": volume,
            "muted": muted,
            "media_title": status.name or "",
            "media_artist": status.artist or "",
            "media_album": status.album or "",
            "media_image_url": image_url,
            "media_duration": status.total_seconds or 0,
            "media_position": status.seconds or 0,
            "shuffle": status.shuffle or False,
            "repeat": self._repeat_mode,
            "source": status.input_id or "",
            "current_preset": self._current_preset_name,
        }

    @staticmethod
    def _map_state(bluos_state: str | None) -> States:
        """Map BluOS state to UC state."""
        if not bluos_state:
            return States.UNKNOWN
        return _BLUOS_STATE_MAP.get(bluos_state.lower(), States.ON)

    # Playback control methods

    def _schedule_poll(self) -> None:
        """Schedule a debounced status poll; cancels any pending poll from rapid commands."""
        if self._pending_poll_task and not self._pending_poll_task.done():
            self._pending_poll_task.cancel()
        self._pending_poll_task = asyncio.create_task(self._debounced_poll())
        self._pending_poll_task.add_done_callback(self._poll_task_done)

    async def _debounced_poll(self) -> None:
        await asyncio.sleep(0.15)
        await self.poll_status(use_etag=False)

    @staticmethod
    def _poll_task_done(task: asyncio.Task) -> None:
        """Log exceptions from fire-and-forget poll tasks."""
        if not task.cancelled() and task.exception():
            _LOG.debug("Scheduled poll task failed: %s", task.exception())

    async def play(self) -> bool:
        """Start playback."""
        if not self._is_available():
            return False
        try:
            await self._player.play()
            self._schedule_poll()
            return True
        except PlayerError as e:
            _LOG.error("Play failed: %s", e)
            return False

    async def pause(self) -> bool:
        """Pause playback."""
        if not self._is_available():
            return False
        try:
            await self._player.pause()
            self._schedule_poll()
            return True
        except PlayerError as e:
            _LOG.error("Pause failed: %s", e)
            return False

    async def stop(self) -> bool:
        """Stop playback."""
        if not self._is_available():
            return False
        try:
            await self._player.stop()
            self._schedule_poll()
            return True
        except PlayerError as e:
            _LOG.error("Stop failed: %s", e)
            return False

    async def next_track(self) -> bool:
        """Skip to next track."""
        if not self._is_available():
            return False
        try:
            await self._player.skip()
            self._schedule_poll()
            return True
        except PlayerError as e:
            _LOG.error("Skip failed: %s", e)
            return False

    async def previous_track(self) -> bool:
        """Go to previous track."""
        if not self._is_available():
            return False
        try:
            await self._player.back()
            self._schedule_poll()
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
        if not self._is_available():
            return False
        level = max(0, min(100, level))
        self._target_volume = level
        self._last_volume_update = time.time()
        await self._volume_queue.put(level)
        self._schedule_poll()
        return True

    async def _adjust_volume(self, delta: int) -> bool:
        if not self._is_available():
            return False
        current = self._target_volume if self._target_volume is not None else (self._last_known_volume or 0)
        new_level = max(0, min(100, current + delta))
        self._target_volume = new_level
        self._last_volume_update = time.time()
        await self._volume_queue.put(new_level)
        self._schedule_poll()
        return True

    async def volume_up(self) -> bool:
        """Increase volume by configured step."""
        return await self._adjust_volume(self._device.volume_step)

    async def volume_down(self) -> bool:
        """Decrease volume by configured step."""
        return await self._adjust_volume(-self._device.volume_step)

    async def mute(self, muted: bool) -> bool:
        """
        Set mute state.

        Args:
            muted: True to mute, False to unmute
        """
        if not self._is_available():
            return False
        self._target_mute = muted
        await self._mute_queue.put(muted)
        self._schedule_poll()
        return True

    async def toggle_mute(self) -> bool:
        """Toggle mute state."""
        if not self._is_available():
            return False
        # Prefer pending target state; fall back to last known device state
        current_mute = self._target_mute if self._target_mute is not None else (self._last_known_mute or False)
        return await self.mute(not current_mute)

    async def set_shuffle(self, enabled: bool) -> bool:
        """
        Set shuffle mode.

        Args:
            enabled: True to enable shuffle
        """
        if not self._is_available():
            _LOG.warning("set_shuffle called but player not available")
            return False
        try:
            # Work around pyblu bug: it sends 'shuffle' param but BluOS API expects 'state'
            # See: https://github.com/superfell/BluShepherd/blob/master/api.md
            await self._raw_get("/Shuffle", params={"state": "1" if enabled else "0"})
            self._schedule_poll()
            return True
        except (PlayerError, aiohttp.ClientError) as e:
            _LOG.error("Set shuffle failed: %s", e)
            return False

    async def select_source(self, source_id: str) -> bool:
        """
        Select input source or preset.

        Args:
            source_id: Source identifier (input ID, preset name, or legacy 'preset:N')
        """
        if not self._is_available():
            return False

        try:
            # Legacy format: preset:N
            if source_id.startswith(PRESET_LEGACY_PREFIX):
                preset_id_str = source_id[len(PRESET_LEGACY_PREFIX) :]
                preset_id = int(preset_id_str)
                await self._player.load_preset(preset_id)
                # Find preset name for tracking
                for preset in self._presets:
                    if preset.id == preset_id:
                        self._current_preset_name = preset.name
                        break
                self._schedule_poll()
                return True

            # Check if it's a preset name
            for preset in self._presets:
                if preset.name == source_id:
                    await self._player.load_preset(preset.id)
                    self._current_preset_name = preset.name
                    self._schedule_poll()
                    return True

            # Find input by ID or name (not a preset)
            for inp in self._inputs:
                if inp.id == source_id or inp.text == source_id:
                    await self._player.play_url(inp.url)
                    self._current_preset_name = None
                    self._schedule_poll()
                    return True

            _LOG.warning("Source not found: %s", source_id)
            return False

        except PlayerError as e:
            _LOG.error("Select source failed: %s", e)
            return False

    async def load_preset_by_command(self, command: str) -> bool:
        """
        Load preset by simple command ID.

        Args:
            command: Simple command ID (e.g., "PRESET_1")
        """
        if not self._is_available():
            return False

        if not command.startswith(PRESET_COMMAND_PREFIX):
            _LOG.warning("Invalid preset command: %s", command)
            return False

        try:
            preset_id = int(command[len(PRESET_COMMAND_PREFIX) :])
            await self._player.load_preset(preset_id)
            # Track preset name for select entity
            for preset in self._presets:
                if preset.id == preset_id:
                    self._current_preset_name = preset.name
                    break
            self._schedule_poll()
            return True
        except (ValueError, PlayerError) as e:
            _LOG.error("Load preset by command failed: %s", e)
            return False

    def get_source_list(self) -> list[str]:
        """Get list of available sources (inputs + presets)."""
        if self._source_list_cache is None:
            self._source_list_cache = [inp.id or inp.text for inp in self._inputs] + [
                preset.name for preset in self._presets
            ]
        return self._source_list_cache

    def get_simple_commands(self) -> list[str]:
        """Get list of simple commands for presets and utilities."""
        commands = [f"{PRESET_COMMAND_PREFIX}{preset.id}" for preset in self._presets]
        commands.append("REFRESH_PRESETS")
        commands.append("SHUFFLE_TOGGLE")
        commands.append("REPEAT_TOGGLE")
        commands.append("SLEEP_TIMER")
        return commands

    async def refresh_presets(self) -> bool:
        """Refresh the list of available presets from the device."""
        if not self._is_available():
            return False

        try:
            self._presets = await self._player.presets()
            self._source_list_cache = None
            _LOG.info("Refreshed %d presets for %s", len(self._presets), self._device.name)
            return True
        except PlayerError as e:
            _LOG.error("Failed to refresh presets: %s", e)
            return False

    # Multi-room grouping methods

    async def get_sync_status(self) -> SyncStatus | None:
        """Get multi-room synchronization status."""
        if not self._is_available():
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
        if not self._is_available():
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
        if not self._is_available():
            return False
        try:
            await self._player.remove_follower(ip, port)
            return True
        except PlayerError as e:
            _LOG.error("Remove follower failed: %s", e)
            return False

    async def set_repeat(self, mode: RepeatMode) -> bool:
        """
        Set repeat mode.

        Args:
            mode: Repeat mode (OFF, ALL, ONE)
        """
        if not self._is_available():
            return False
        try:
            await self._raw_get("/Repeat", params={"state": _REPEAT_API_MAP[mode]})
            self._repeat_mode = mode
            _LOG.debug("Repeat mode set to %s", mode)
            self._schedule_poll()
            return True
        except (PlayerError, aiohttp.ClientError) as e:
            _LOG.error("Set repeat failed: %s", e)
            return False

    async def toggle_repeat(self) -> bool:
        """Toggle repeat mode: OFF -> ALL -> ONE -> OFF."""
        return await self.set_repeat(_REPEAT_NEXT_MAP[self._repeat_mode])

    async def seek(self, position: int) -> bool:
        """
        Seek to a position in the current track.

        Args:
            position: Position in seconds
        """
        if not self._is_available():
            return False
        try:
            await self._raw_get("/Play", params={"seek": str(position)})
            _LOG.debug("Seeked to position %d", position)
            self._schedule_poll()
            return True
        except (PlayerError, aiohttp.ClientError) as e:
            _LOG.error("Seek failed: %s", e)
            return False

    async def toggle_sleep_timer(self) -> int:
        """
        Toggle sleep timer through preset values.

        Returns:
            New sleep timer value in minutes (0 = off)
        """
        if not self._is_available():
            return 0
        try:
            # Use pyblu's sleep_timer which cycles: 15 -> 30 -> 45 -> 60 -> 90 -> 0
            new_value = await self._player.sleep_timer()
            self._sleep_timer = new_value
            _LOG.debug("Sleep timer set to %d minutes", new_value)
            self._schedule_poll()
            return new_value
        except PlayerError as e:
            _LOG.error("Toggle sleep timer failed: %s", e)
            return self._sleep_timer

    # Browse and search methods

    def _parse_browse_xml(self, xml_text: str) -> dict[str, Any]:
        """
        Parse BluOS /Browse XML response into a structured dict.

        Returns:
            Dict with keys: items, next_key, search_key, parent_key, service_name,
            service_icon, browse_type
        """
        root = ET.fromstring(xml_text)

        # Check for error response
        if root.tag == "error":
            message = root.findtext("message", "Unknown error")
            _LOG.warning("Browse error: %s", message)
            return {"items": [], "error": message}

        result: dict[str, Any] = {
            "items": [],
            "next_key": root.get("nextKey"),
            "search_key": root.get("searchKey"),
            "parent_key": root.get("parentKey"),
            "service_name": root.get("serviceName"),
            "service_icon": root.get("serviceIcon"),
            "browse_type": root.get("type", "menu"),
        }

        def parse_item(elem: ET.Element) -> dict[str, Any]:
            item: dict[str, Any] = {
                "text": elem.get("text", ""),
                "text2": elem.get("text2"),
                "image": elem.get("image"),
                "type": elem.get("type", "link"),
                "browse_key": elem.get("browseKey"),
                "play_url": elem.get("playURL"),
                "autoplay_url": elem.get("autoplayURL"),
                "context_menu_key": elem.get("contextMenuKey"),
                "action_url": elem.get("actionURL"),
                "input_type": elem.get("inputType"),
            }
            return item

        # Items can be directly under <browse> or inside <category> elements
        for category in root.findall("category"):
            cat_items = []
            for item_elem in category.findall("item"):
                cat_items.append(parse_item(item_elem))
            if cat_items:
                # Add category as a directory-like item containing sub-items
                result["items"].append(
                    {
                        "text": category.get("text", ""),
                        "type": "category",
                        "browse_key": None,
                        "play_url": None,
                        "autoplay_url": None,
                        "image": None,
                        "text2": None,
                        "context_menu_key": None,
                        "action_url": None,
                        "input_type": None,
                        "items": cat_items,
                    }
                )

        # Direct items under <browse>
        for item_elem in root.findall("item"):
            result["items"].append(parse_item(item_elem))

        return result

    async def browse(self, key: str | None = None) -> dict[str, Any]:
        """
        Browse music content on the BluOS player.

        Args:
            key: Browse key for navigation. None for top-level browse.

        Returns:
            Parsed browse results dict.
        """
        if not self._is_available():
            return {"items": [], "error": "Player not available"}

        try:
            # Unquote the key first to normalize any pre-encoded sequences (yarl
            # would otherwise decode %2F/%3F before sending, producing malformed URLs
            # like "key=foo//bar?baz"), then let aiohttp params properly re-encode.
            params = {"key": unquote(key)} if key else None
            _LOG.debug("Browse request: /Browse key=%s", params.get("key") if params else None)
            xml_text = await self._raw_get("/Browse", params=params, timeout=15)
            _LOG.debug("Browse response length: %d", len(xml_text))
            return self._parse_browse_xml(xml_text)

        except (aiohttp.ClientError, ET.ParseError) as e:
            _LOG.error("Browse failed: %s", e)
            return {"items": [], "error": str(e)}

    async def search(self, search_key: str, query: str) -> dict[str, Any]:
        """
        Search music content on the BluOS player.

        Args:
            search_key: The searchKey from a previous browse response.
            query: Search text.

        Returns:
            Parsed search results dict.
        """
        if not self._is_available():
            return {"items": [], "error": "Player not available"}

        try:
            params = {"key": unquote(search_key), "q": query}
            _LOG.debug("Search request: /Browse key=%s q=%s", params["key"], query)
            xml_text = await self._raw_get("/Browse", params=params, timeout=15)
            return self._parse_browse_xml(xml_text)

        except (aiohttp.ClientError, ET.ParseError) as e:
            _LOG.error("Search failed: %s", e)
            return {"items": [], "error": str(e)}

    async def play_browse_item(self, play_url: str) -> bool:
        """
        Play an item from browse results using its playURL.

        Args:
            play_url: The playURL or autoplayURL from a browse item.
        """
        if not self._is_available():
            return False

        try:
            # playURL is typically a relative URI like /Play?url=... with an already-encoded
            # query string. _raw_get_play_url handles both relative and absolute URLs and
            # prevents yarl from re-encoding percent-encoded characters.
            _LOG.debug("Playing browse item: %s", play_url)
            await self._raw_get_play_url(play_url)
            self._schedule_poll()
            return True
        except (aiohttp.ClientError, PlayerError) as e:
            _LOG.error("Play browse item failed: %s", e)
            return False

    async def clear_queue(self) -> bool:
        """Clear the play queue."""
        if not self._is_available():
            return False

        try:
            await self._raw_get("/Clear")
            self._schedule_poll()
            return True
        except (aiohttp.ClientError, PlayerError) as e:
            _LOG.error("Clear queue failed: %s", e)
            return False
