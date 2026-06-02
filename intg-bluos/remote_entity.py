"""UC Remote entity for BluOS devices.

The Remote entity is a thin control surface over the existing media player: it
exposes a flat list of simple commands (transport, power, presets, grouping)
that can be bound to physical buttons, on-screen UI pages, activities and
macros. Command execution is delegated to the BluOSMediaPlayer entity so there
is a single source of truth for command handling.
"""

import dataclasses
import logging
from typing import Any

import ucapi
from bluos import BluOSPlayer
from config import BluOSDevice
from media_player import BluOSMediaPlayer
from ucapi.remote import Attributes, Commands, Features, Options, States
from ucapi.ui import Buttons, Size, UiPage, create_btn_mapping, create_ui_text

_LOG = logging.getLogger(__name__)

# Friendly remote command names -> media player command ids. Anything not in
# this map (PRESET_*, SHUFFLE_TOGGLE, GROUP_*, ...) is passed through unchanged,
# since the media player already understands those identifiers.
_SEND_CMD_MAP: dict[str, str] = {
    "PLAY_PAUSE": "play_pause",
    "STOP": "stop",
    "NEXT": "next",
    "PREVIOUS": "previous",
    "FAST_FORWARD": "fast_forward",
    "REWIND": "rewind",
    "VOLUME_UP": "volume_up",
    "VOLUME_DOWN": "volume_down",
    "MUTE_TOGGLE": "mute_toggle",
    "POWER_ON": "on",
    "POWER_OFF": "off",
    "POWER_TOGGLE": "toggle",
}

# Transport/power simple commands contributed by the remote itself.
_TRANSPORT_COMMANDS = list(_SEND_CMD_MAP.keys())


class BluOSRemote(ucapi.Remote):
    """Remote entity providing a bindable command surface for a BluOS player."""

    def __init__(
        self,
        device: BluOSDevice,
        player: BluOSPlayer,
        media_entity: BluOSMediaPlayer,
    ):
        """
        Initialize the remote entity.

        Args:
            device: Device configuration
            player: BluOS player wrapper
            media_entity: The device's media player entity, to which all command
                execution is delegated.
        """
        self._device = device
        self._player = player
        self._media_entity = media_entity
        # When True, the next update pushes STATE regardless of the diff.
        self._force_update: bool = False

        super().__init__(
            f"bluos_{device.id}_remote",
            f"{device.name} Remote",
            features=[Features.ON_OFF, Features.TOGGLE, Features.SEND_CMD],
            attributes={Attributes.STATE: States.UNAVAILABLE},
            simple_commands=self._build_simple_commands(),
            button_mapping=self._button_mapping(),
            ui_pages=self._ui_pages(),
        )

    def _build_simple_commands(self) -> list[str]:
        """Transport/power commands plus the media player's dynamic commands."""
        return _TRANSPORT_COMMANDS + self._media_entity.simple_commands

    @staticmethod
    def _button_mapping() -> list:
        """Default physical-button layout for a BluOS player."""
        return [
            create_btn_mapping(Buttons.PLAY, "PLAY_PAUSE"),
            create_btn_mapping(Buttons.PREV, "PREVIOUS"),
            create_btn_mapping(Buttons.NEXT, "NEXT"),
            create_btn_mapping(Buttons.STOP, "STOP"),
            create_btn_mapping(Buttons.VOLUME_UP, "VOLUME_UP"),
            create_btn_mapping(Buttons.VOLUME_DOWN, "VOLUME_DOWN"),
            create_btn_mapping(Buttons.MUTE, "MUTE_TOGGLE"),
            create_btn_mapping(Buttons.POWER, "POWER_TOGGLE"),
        ]

    def _ui_pages(self) -> list[UiPage]:
        """Build the transport page and a presets page from current presets."""
        transport = UiPage("transport", "Transport", grid=Size(4, 6))
        transport.add(create_ui_text("Play/Pause", 0, 0, size=Size(2, 1), cmd="PLAY_PAUSE"))
        transport.add(create_ui_text("Stop", 2, 0, cmd="STOP"))
        transport.add(create_ui_text("Mute", 3, 0, cmd="MUTE_TOGGLE"))
        transport.add(create_ui_text("Prev", 0, 1, cmd="PREVIOUS"))
        transport.add(create_ui_text("Next", 1, 1, cmd="NEXT"))
        transport.add(create_ui_text("Vol -", 2, 1, cmd="VOLUME_DOWN"))
        transport.add(create_ui_text("Vol +", 3, 1, cmd="VOLUME_UP"))

        presets = UiPage("presets", "Presets", grid=Size(4, 6))
        for index, preset in enumerate(self._player.presets[:24]):
            presets.add(create_ui_text(preset.name, index % 4, index // 4, cmd=f"PRESET_{preset.id}"))

        return [transport, presets]

    def update_options(self) -> dict[str, Any]:
        """Rebuild options (simple commands + UI) after presets/devices change.

        Serializes button mappings and UI pages to plain dicts the same way the
        ucapi Remote constructor does, so the refreshed options match the
        Integration-API wire format.
        """
        self.options = {
            Options.SIMPLE_COMMANDS: self._build_simple_commands(),
            Options.BUTTON_MAPPING: [dataclasses.asdict(b) for b in self._button_mapping()],
            Options.USER_INTERFACE: {"pages": [dataclasses.asdict(p) for p in self._ui_pages()]},
        }
        return self.options

    async def command(
        self, cmd_id: str, params: dict[str, Any] | None = None, *, websocket: Any = None
    ) -> ucapi.StatusCodes:
        """Handle remote commands by delegating to the media player entity."""
        params = params or {}
        _LOG.debug("Remote command %s with params %s for %s", cmd_id, params, self._device.name)

        if not self._player.available:
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        if cmd_id in (Commands.ON, Commands.OFF, Commands.TOGGLE):
            power_map = {Commands.ON: "on", Commands.OFF: "off", Commands.TOGGLE: "toggle"}
            return await self._media_entity.command(power_map[cmd_id])

        if cmd_id == Commands.SEND_CMD:
            command = params.get("command")
            if not command:
                return ucapi.StatusCodes.BAD_REQUEST
            return await self._dispatch(command)

        if cmd_id == Commands.SEND_CMD_SEQUENCE:
            sequence = params.get("sequence") or []
            result = ucapi.StatusCodes.OK
            for command in sequence:
                step = await self._dispatch(command)
                if step != ucapi.StatusCodes.OK:
                    result = step
            return result

        _LOG.warning("Unsupported remote command: %s", cmd_id)
        return ucapi.StatusCodes.NOT_IMPLEMENTED

    async def _dispatch(self, command: str) -> ucapi.StatusCodes:
        """Translate a remote command and delegate it to the media player."""
        mp_cmd = _SEND_CMD_MAP.get(command, command)
        return await self._media_entity.command(mp_cmd)

    def _compute_state(self) -> dict[str, Any]:
        """Compute the remote STATE from player availability."""
        return {Attributes.STATE: States.ON if self._player.available else States.UNAVAILABLE}

    def _diff_attributes(self, computed: dict[str, Any]) -> dict[str, Any]:
        """Return changed attributes, honouring a forced full resync."""
        if self._force_update:
            self._force_update = False
            changed = dict(computed)
        else:
            changed = {key: value for key, value in computed.items() if self.attributes.get(key) != value}
        self.attributes.update(changed)
        return changed

    def update_attributes(self, _attributes: dict[str, Any]) -> dict[str, Any]:
        """Recompute STATE from availability and return the diff."""
        return self._diff_attributes(self._compute_state())

    def set_unavailable(self) -> dict[str, Any]:
        """Mark entity as unavailable and return changed attributes."""
        if self.attributes.get(Attributes.STATE) != States.UNAVAILABLE:
            self.attributes[Attributes.STATE] = States.UNAVAILABLE
            return {Attributes.STATE: States.UNAVAILABLE}
        return {}

    def clear_cached_attributes(self) -> None:
        """Force the next update to push STATE (after standby exit/resubscribe)."""
        self._force_update = True
