"""UC Media Player entity for BluOS devices."""

import logging
from typing import Any

import ucapi
from ucapi.media_player import Attributes, Commands, DeviceClasses, Features, States

from bluos import BluOSPlayer, States as BluOSStates
from config import BluOSDevice

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
    Features.SHUFFLE,
    Features.SELECT_SOURCE,
    Features.MEDIA_TITLE,
    Features.MEDIA_ARTIST,
    Features.MEDIA_ALBUM,
    Features.MEDIA_IMAGE_URL,
    Features.MEDIA_DURATION,
    Features.MEDIA_POSITION,
]


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
                Attributes.SOURCE: "",
                Attributes.SOURCE_LIST: [],
            },
            device_class=DeviceClasses.SPEAKER,
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
            if value is not None and value != self._last_attributes.get(uc_attr):
                changed[uc_attr] = value
                self._last_attributes[uc_attr] = value

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

    async def command(
        self, cmd_id: str, params: dict[str, Any] | None = None
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

            case Commands.PREVIOUS:
                result = await self._player.previous_track()

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
                shuffle = params.get("shuffle", False)
                result = await self._player.set_shuffle(shuffle)

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
