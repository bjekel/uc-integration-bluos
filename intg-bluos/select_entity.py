"""UC Select entity for BluOS presets."""

import logging
from typing import Any

import ucapi
from bluos import BluOSPlayer
from config import BluOSDevice
from ucapi.select import Attributes, Commands, States

_LOG = logging.getLogger(__name__)


class BluOSPresetSelect(ucapi.Select):
    """Select entity for BluOS presets."""

    def __init__(
        self,
        device: BluOSDevice,
        player: BluOSPlayer,
    ):
        """
        Initialize BluOS preset select entity.

        Args:
            device: Device configuration
            player: BluOS player wrapper
        """
        entity_id = f"bluos_{device.id}_presets"
        name = f"{device.name} Presets"

        # Get initial options from player presets
        options = [preset.name for preset in player.presets]

        super().__init__(
            entity_id,
            name,
            attributes={
                Attributes.STATE: States.UNAVAILABLE,
                Attributes.CURRENT_OPTION: "",
                Attributes.OPTIONS: options,
            },
        )

        self._device = device
        self._player = player
        self._last_current_option: str = ""

    def _compute_state_and_options_changes(self) -> dict[str, Any]:
        """Compute and apply state + options changes; return a dict of what changed."""
        changed: dict[str, Any] = {}
        new_state = States.ON if self._player.available else States.UNAVAILABLE
        if self.attributes.get(Attributes.STATE) != new_state:
            changed[Attributes.STATE] = new_state
            self.attributes[Attributes.STATE] = new_state
        options = [preset.name for preset in self._player.presets]
        if self.attributes.get(Attributes.OPTIONS) != options:
            changed[Attributes.OPTIONS] = options
            self.attributes[Attributes.OPTIONS] = options
        return changed

    def update_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """
        Update entity attributes and return only changed ones.

        Args:
            attributes: New attributes from BluOS player (includes 'source')

        Returns:
            Dictionary of changed attributes
        """
        changed = self._compute_state_and_options_changes()

        # Determine current option from tracked preset name
        current_preset = attributes.get("current_preset")
        current_option = current_preset if current_preset else ""

        if current_option != self._last_current_option:
            changed[Attributes.CURRENT_OPTION] = current_option
            self.attributes[Attributes.CURRENT_OPTION] = current_option
            self._last_current_option = current_option

        return changed

    def refresh_options(self) -> dict[str, Any]:
        """
        Refresh options from player presets.

        Returns:
            Dictionary of changed attributes
        """
        return self._compute_state_and_options_changes()

    def set_unavailable(self) -> dict[str, Any]:
        """
        Mark entity as unavailable.

        Returns:
            Dictionary of changed attributes
        """
        if self.attributes.get(Attributes.STATE) != States.UNAVAILABLE:
            self.attributes[Attributes.STATE] = States.UNAVAILABLE
            return {Attributes.STATE: States.UNAVAILABLE}
        return {}

    def clear_cached_attributes(self) -> None:
        """Clear cached attributes to force full update on next poll."""
        self._last_current_option = ""

    async def command(self, cmd_id: str, params: dict[str, Any] | None = None, **_kwargs: Any) -> ucapi.StatusCodes:
        """
        Handle select entity commands.

        Args:
            cmd_id: Command identifier
            params: Command parameters

        Returns:
            Status code indicating success or failure
        """
        params = params or {}
        _LOG.debug("Select command %s with params %s for %s", cmd_id, params, self._device.name)

        if not self._player.available:
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        presets = self._player.presets
        if not presets:
            _LOG.warning("No presets available for %s", self._device.name)
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        result = False

        match cmd_id:
            case Commands.SELECT_OPTION:
                option = params.get("option")
                if option:
                    result = await self._player.select_source(option)
                else:
                    _LOG.warning("SELECT_OPTION missing 'option' parameter")
                    return ucapi.StatusCodes.BAD_REQUEST

            case Commands.SELECT_FIRST:
                first_preset = presets[0]
                result = await self._player.select_source(first_preset.name)

            case Commands.SELECT_LAST:
                last_preset = presets[-1]
                result = await self._player.select_source(last_preset.name)

            case Commands.SELECT_NEXT:
                current_idx = self._get_current_preset_index()
                if current_idx is not None:
                    next_idx = (current_idx + 1) % len(presets)
                    result = await self._player.select_source(presets[next_idx].name)
                else:
                    # No current preset, select first
                    result = await self._player.select_source(presets[0].name)

            case Commands.SELECT_PREVIOUS:
                current_idx = self._get_current_preset_index()
                if current_idx is not None:
                    prev_idx = (current_idx - 1) % len(presets)
                    result = await self._player.select_source(presets[prev_idx].name)
                else:
                    # No current preset, select last
                    result = await self._player.select_source(presets[-1].name)

            case _:
                _LOG.warning("Unsupported select command: %s", cmd_id)
                return ucapi.StatusCodes.NOT_IMPLEMENTED

        return ucapi.StatusCodes.OK if result else ucapi.StatusCodes.SERVER_ERROR

    def _get_current_preset_index(self) -> int | None:
        """
        Get the index of the currently selected preset.

        Returns:
            Index of current preset, or None if not found
        """
        current = self._last_current_option
        if not current:
            return None

        for idx, preset in enumerate(self._player.presets):
            if preset.name == current:
                return idx
        return None
