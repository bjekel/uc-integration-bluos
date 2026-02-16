"""UC Media Player entity for BluOS devices."""

import logging
from typing import Any

import ucapi
from bluos import BluOSPlayer
from bluos import RepeatMode as BluOSRepeatMode
from bluos import States as BluOSStates
from config import BluOSDevice
from ucapi.media_player import Attributes, Commands, DeviceClasses, Features, Options, RepeatMode, States

_LOG = logging.getLogger(__name__)

# Features supported by BluOS players
BLUOS_FEATURES = [
    Features.ON_OFF,
    Features.TOGGLE,
    Features.VOLUME,
    Features.VOLUME_UP_DOWN,
    Features.MUTE_TOGGLE,
    Features.MUTE,
    Features.UNMUTE,
    Features.PLAY_PAUSE,
    Features.STOP,
    Features.NEXT,
    Features.PREVIOUS,
    Features.FAST_FORWARD,
    Features.REWIND,
    Features.SHUFFLE,
    Features.REPEAT,
    Features.SEEK,
    Features.SELECT_SOURCE,
    Features.MEDIA_TITLE,
    Features.MEDIA_ARTIST,
    Features.MEDIA_ALBUM,
    Features.MEDIA_IMAGE_URL,
    Features.MEDIA_DURATION,
    Features.MEDIA_POSITION,
]

# Seek step for fast forward/rewind in seconds
SEEK_STEP = 10


class BluOSMediaPlayer(ucapi.MediaPlayer):
    """Media player entity for BluOS devices."""

    def __init__(
        self,
        device: BluOSDevice,
        player: BluOSPlayer,
    ):
        """
        Initialize BluOS media player entity.

        Args:
            device: Device configuration
            player: BluOS player wrapper
        """
        entity_id = f"bluos_{device.id}"
        name = device.name

        # Build options with simple commands for presets
        options = {Options.SIMPLE_COMMANDS: player.get_simple_commands()}

        super().__init__(
            entity_id,
            name,
            features=BLUOS_FEATURES,
            attributes={
                Attributes.STATE: States.UNAVAILABLE,
                Attributes.VOLUME: 0,
                Attributes.MUTED: False,
                Attributes.MEDIA_TITLE: "",
                Attributes.MEDIA_ARTIST: "",
                Attributes.MEDIA_ALBUM: "",
                Attributes.MEDIA_IMAGE_URL: "",
                Attributes.MEDIA_DURATION: 0,
                Attributes.MEDIA_POSITION: 0,
                Attributes.SHUFFLE: False,
                Attributes.REPEAT: RepeatMode.OFF,
                Attributes.SOURCE: "",
                Attributes.SOURCE_LIST: [],
            },
            device_class=DeviceClasses.SPEAKER,
            options=options,
        )

        self._device = device
        self._player = player
        self._last_attributes: dict[str, Any] = {}

    @property
    def player(self) -> BluOSPlayer:
        """Return the BluOS player wrapper."""
        return self._player

    def update_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """
        Update entity attributes and return only changed ones.

        Args:
            attributes: New attributes from BluOS player

        Returns:
            Dictionary of changed attributes
        """
        changed = {}

        # Map BluOS state to UC state
        state = self._map_state(attributes.get("state", BluOSStates.UNKNOWN))
        if state != self._last_attributes.get(Attributes.STATE):
            changed[Attributes.STATE] = state
            self._last_attributes[Attributes.STATE] = state

        # Update source list
        source_list = self._player.get_source_list()
        if source_list != self._last_attributes.get(Attributes.SOURCE_LIST):
            changed[Attributes.SOURCE_LIST] = source_list
            self._last_attributes[Attributes.SOURCE_LIST] = source_list

        # Track if media_title changed (indicates new track)
        track_changed = False

        # Map other attributes
        attr_mapping = {
            "volume": Attributes.VOLUME,
            "muted": Attributes.MUTED,
            "media_title": Attributes.MEDIA_TITLE,
            "media_artist": Attributes.MEDIA_ARTIST,
            "media_album": Attributes.MEDIA_ALBUM,
            "media_image_url": Attributes.MEDIA_IMAGE_URL,
            "media_duration": Attributes.MEDIA_DURATION,
            "media_position": Attributes.MEDIA_POSITION,
            "shuffle": Attributes.SHUFFLE,
            "source": Attributes.SOURCE,
        }

        for bluos_attr, uc_attr in attr_mapping.items():
            value = attributes.get(bluos_attr)
            last_value = self._last_attributes.get(uc_attr)
            if value is not None and value != last_value:
                changed[uc_attr] = value
                self._last_attributes[uc_attr] = value
                # Detect track change
                if uc_attr == Attributes.MEDIA_TITLE:
                    track_changed = True

        # Force position update on track change (even if value is same)
        if track_changed:
            position = attributes.get("media_position")
            if position is not None:
                changed[Attributes.MEDIA_POSITION] = position
                self._last_attributes[Attributes.MEDIA_POSITION] = position

        # Handle repeat mode separately (needs mapping from BluOS to UC)
        repeat = attributes.get("repeat")
        if repeat is not None:
            uc_repeat = self._map_repeat_mode(repeat)
            if uc_repeat != self._last_attributes.get(Attributes.REPEAT):
                changed[Attributes.REPEAT] = uc_repeat
                self._last_attributes[Attributes.REPEAT] = uc_repeat

        # Clear media info when not playing
        if state in (States.OFF, States.STANDBY, States.UNAVAILABLE):
            for attr in [
                Attributes.MEDIA_TITLE,
                Attributes.MEDIA_ARTIST,
                Attributes.MEDIA_ALBUM,
                Attributes.MEDIA_IMAGE_URL,
            ]:
                if self._last_attributes.get(attr):
                    changed[attr] = ""
                    self._last_attributes[attr] = ""

        return changed

    def set_unavailable(self) -> dict[str, Any]:
        """Mark entity as unavailable and return changed attributes."""
        if self._last_attributes.get(Attributes.STATE) != States.UNAVAILABLE:
            self._last_attributes[Attributes.STATE] = States.UNAVAILABLE
            return {Attributes.STATE: States.UNAVAILABLE}
        return {}

    def clear_cached_attributes(self) -> None:
        """Clear cached attributes to force full update on next poll."""
        self._last_attributes.clear()

    def update_options(self) -> dict[str, Any]:
        """Update and return entity options with current simple commands."""
        self.options = {Options.SIMPLE_COMMANDS: self._player.get_simple_commands()}
        return self.options

    @staticmethod
    def _map_state(bluos_state: BluOSStates) -> States:
        """Map BluOS state to UC state."""
        state_map = {
            BluOSStates.UNKNOWN: States.UNKNOWN,
            BluOSStates.UNAVAILABLE: States.UNAVAILABLE,
            BluOSStates.OFF: States.OFF,
            BluOSStates.ON: States.ON,
            BluOSStates.PLAYING: States.PLAYING,
            BluOSStates.PAUSED: States.PAUSED,
            BluOSStates.STOPPED: States.ON,
            BluOSStates.BUFFERING: States.BUFFERING,
        }
        return state_map.get(bluos_state, States.UNKNOWN)

    @staticmethod
    def _map_repeat_mode(bluos_repeat: BluOSRepeatMode) -> RepeatMode:
        """Map BluOS repeat mode to UC repeat mode."""
        repeat_map = {
            BluOSRepeatMode.OFF: RepeatMode.OFF,
            BluOSRepeatMode.ALL: RepeatMode.ALL,
            BluOSRepeatMode.ONE: RepeatMode.ONE,
        }
        return repeat_map.get(bluos_repeat, RepeatMode.OFF)

    async def command(
        self, cmd_id: str, params: dict[str, Any] | None = None, *, websocket: Any = None
    ) -> ucapi.StatusCodes:
        """
        Handle media player commands.

        Args:
            cmd_id: Command identifier
            params: Command parameters

        Returns:
            Status code indicating success or failure
        """
        params = params or {}
        _LOG.debug("Command %s with params %s for %s", cmd_id, params, self._device.name)

        if not self._player.available:
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        # Check if it's a simple preset command
        if cmd_id.startswith("PRESET_"):
            result = await self._player.load_preset_by_command(cmd_id)
            return ucapi.StatusCodes.OK if result else ucapi.StatusCodes.SERVER_ERROR

        # Handle refresh presets command
        if cmd_id == "REFRESH_PRESETS":
            result = await self._player.refresh_presets()
            if result:
                # Update options with new preset commands
                self.update_options()
                # Clear cached source list so next poll sends update to UC Remote
                self._last_attributes.pop(Attributes.SOURCE_LIST, None)
                return ucapi.StatusCodes.OK
            return ucapi.StatusCodes.SERVER_ERROR

        # Handle shuffle toggle command
        if cmd_id == "SHUFFLE_TOGGLE":
            current = self._last_attributes.get(Attributes.SHUFFLE, False)
            result = await self._player.set_shuffle(not current)
            return ucapi.StatusCodes.OK if result else ucapi.StatusCodes.SERVER_ERROR

        # Handle repeat toggle command (cycles: OFF -> ALL -> ONE -> OFF)
        if cmd_id == "REPEAT_TOGGLE":
            result = await self._player.toggle_repeat()
            return ucapi.StatusCodes.OK if result else ucapi.StatusCodes.SERVER_ERROR

        # Handle sleep timer command (cycles: 15 -> 30 -> 45 -> 60 -> 90 -> off)
        if cmd_id == "SLEEP_TIMER":
            new_timer = await self._player.toggle_sleep_timer()
            _LOG.info("Sleep timer set to %d minutes", new_timer)
            return ucapi.StatusCodes.OK

        result = False

        match cmd_id:
            case Commands.ON:
                # BluOS has no power control, start playback instead
                result = await self._player.play()

            case Commands.OFF:
                # BluOS has no power control, stop playback instead
                result = await self._player.stop()

            case Commands.TOGGLE:
                if self._last_attributes.get(Attributes.STATE) == States.PLAYING:
                    result = await self._player.pause()
                else:
                    result = await self._player.play()

            case Commands.PLAY_PAUSE:
                if self._last_attributes.get(Attributes.STATE) == States.PLAYING:
                    result = await self._player.pause()
                else:
                    result = await self._player.play()

            case Commands.STOP:
                result = await self._player.stop()

            case Commands.NEXT:
                result = await self._player.next_track()
                # Clear position cache to force update on next poll
                self._last_attributes.pop(Attributes.MEDIA_POSITION, None)

            case Commands.PREVIOUS:
                result = await self._player.previous_track()
                # Clear position cache to force update on next poll
                self._last_attributes.pop(Attributes.MEDIA_POSITION, None)

            case Commands.FAST_FORWARD:
                # Seek forward by SEEK_STEP seconds
                current_pos = self._last_attributes.get(Attributes.MEDIA_POSITION, 0)
                duration = self._last_attributes.get(Attributes.MEDIA_DURATION, 0)
                new_pos = min(current_pos + SEEK_STEP, duration) if duration else current_pos + SEEK_STEP
                result = await self._player.seek(int(new_pos))
                # Clear position cache to force update on next poll
                self._last_attributes.pop(Attributes.MEDIA_POSITION, None)

            case Commands.REWIND:
                # Seek backward by SEEK_STEP seconds
                current_pos = self._last_attributes.get(Attributes.MEDIA_POSITION, 0)
                new_pos = max(current_pos - SEEK_STEP, 0)
                result = await self._player.seek(int(new_pos))
                # Clear position cache to force update on next poll
                self._last_attributes.pop(Attributes.MEDIA_POSITION, None)

            case Commands.VOLUME:
                volume = params.get("volume")
                if volume is not None:
                    result = await self._player.set_volume(int(volume))
                else:
                    result = False

            case Commands.VOLUME_UP:
                result = await self._player.volume_up()

            case Commands.VOLUME_DOWN:
                result = await self._player.volume_down()

            case Commands.MUTE_TOGGLE:
                result = await self._player.toggle_mute()

            case Commands.MUTE:
                result = await self._player.mute(True)

            case Commands.UNMUTE:
                result = await self._player.mute(False)

            case Commands.SHUFFLE:
                # Set shuffle mode from parameter
                shuffle = params.get("shuffle", False)
                result = await self._player.set_shuffle(bool(shuffle))

            case Commands.REPEAT:
                # Set repeat mode from parameter
                repeat_param = params.get("repeat", "OFF")
                # Map UC RepeatMode to BluOS RepeatMode
                mode_map = {
                    RepeatMode.OFF: BluOSRepeatMode.OFF,
                    RepeatMode.ALL: BluOSRepeatMode.ALL,
                    RepeatMode.ONE: BluOSRepeatMode.ONE,
                    # Also accept string values
                    "OFF": BluOSRepeatMode.OFF,
                    "ALL": BluOSRepeatMode.ALL,
                    "ONE": BluOSRepeatMode.ONE,
                }
                bluos_mode = mode_map.get(repeat_param, BluOSRepeatMode.OFF)
                result = await self._player.set_repeat(bluos_mode)

            case Commands.SEEK:
                position = params.get("media_position")
                if position is not None:
                    result = await self._player.seek(int(position))
                    # Clear position cache to force update on next poll
                    self._last_attributes.pop(Attributes.MEDIA_POSITION, None)
                else:
                    result = False

            case Commands.SELECT_SOURCE:
                source = params.get("source")
                if source:
                    result = await self._player.select_source(source)
                else:
                    result = False

            case _:
                _LOG.warning("Unsupported command: %s", cmd_id)
                return ucapi.StatusCodes.NOT_IMPLEMENTED

        return ucapi.StatusCodes.OK if result else ucapi.StatusCodes.SERVER_ERROR
