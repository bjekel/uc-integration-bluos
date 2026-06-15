"""UC Select entity for BluOS presets."""

import logging
from typing import Any

import ucapi
from bluos import BluOSPlayer
from config import BluOSDevice
from entity_mixin import DiffPushMixin
from ucapi.select import Attributes, Commands, States

_LOG = logging.getLogger(__name__)


class BluOSPresetSelect(DiffPushMixin, ucapi.Select):
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

    def _compute_state_and_options(self) -> dict[str, Any]:
        """Compute the current STATE and OPTIONS attribute values."""
        return {
            Attributes.STATE: States.ON if self._player.available else States.UNAVAILABLE,
            Attributes.OPTIONS: [preset.name for preset in self._player.presets],
        }

    def update_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """
        Compute entity attributes from a BluOS status update and return only
        those that differ from the current entity state.

        Args:
            attributes: New attributes from BluOS player (includes 'current_preset')

        Returns:
            Dictionary of changed attributes
        """
        computed = self._compute_state_and_options()
        current_preset = attributes.get("current_preset")
        computed[Attributes.CURRENT_OPTION] = current_preset if current_preset else ""
        return self._diff_attributes(computed)

    def refresh_options(self) -> dict[str, Any]:
        """
        Refresh state and options from player presets.

        Returns:
            Dictionary of changed attributes
        """
        return self._diff_attributes(self._compute_state_and_options())

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
        current = self.attributes.get(Attributes.CURRENT_OPTION, "")
        if not current:
            return None

        for idx, preset in enumerate(self._player.presets):
            if preset.name == current:
                return idx
        return None
