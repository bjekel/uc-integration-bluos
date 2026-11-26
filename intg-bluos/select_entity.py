"""UC Select entities for BluOS: presets, physical inputs and the sleep timer."""

import logging
from typing import Any

import ucapi
from bluos import SLEEP_TIMER_STEPS, BluOSPlayer
from config import BluOSDevice
from entity_mixin import DiffPushMixin
from i18n import entity_name
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
        name = entity_name(device.name, "Presets")

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

        if not await self._player.ensure_connected():
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


class _BluOSOptionSelect(DiffPushMixin, ucapi.Select):
    """
    Select with a player-derived option list and a current value from status.

    Subclasses provide ``_options()``, ``_current(attributes)`` and
    ``_select(option)``. Only changed attributes are pushed (``_diff_attributes``),
    and options normally only change after a (re)connect.
    """

    def __init__(self, entity_id: str, name: str | dict[str, str], device: BluOSDevice, player: BluOSPlayer):
        self._device = device
        self._player = player
        super().__init__(
            entity_id,
            name,
            attributes={
                Attributes.STATE: States.UNAVAILABLE,
                Attributes.CURRENT_OPTION: "",
                Attributes.OPTIONS: self._options(),
            },
        )

    def _options(self) -> list[str]:
        raise NotImplementedError

    def _current(self, attributes: dict[str, Any]) -> str:
        raise NotImplementedError

    async def _select(self, option: str) -> bool:
        raise NotImplementedError

    def _compute_state_and_options(self) -> dict[str, Any]:
        return {
            Attributes.STATE: States.ON if self._player.available else States.UNAVAILABLE,
            Attributes.OPTIONS: self._options(),
        }

    def update_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Return the attributes that changed for this status update."""
        computed = self._compute_state_and_options()
        computed[Attributes.CURRENT_OPTION] = self._current(attributes)
        return self._diff_attributes(computed)

    def refresh_options(self) -> dict[str, Any]:
        """Refresh state and options (e.g. after connect); return the changes."""
        return self._diff_attributes(self._compute_state_and_options())

    def set_unavailable(self) -> dict[str, Any]:
        """Mark entity as unavailable and return the changes."""
        if self.attributes.get(Attributes.STATE) != States.UNAVAILABLE:
            self.attributes[Attributes.STATE] = States.UNAVAILABLE
            return {Attributes.STATE: States.UNAVAILABLE}
        return {}

    async def command(self, cmd_id: str, params: dict[str, Any] | None = None, **_kwargs: Any) -> ucapi.StatusCodes:
        """Handle select commands."""
        params = params or {}
        _LOG.debug("%s command %s with params %s", self.id, cmd_id, params)

        if not await self._player.ensure_connected():
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        options = self._options()
        if not options:
            return ucapi.StatusCodes.SERVICE_UNAVAILABLE

        current = self.attributes.get(Attributes.CURRENT_OPTION, "")
        idx = options.index(current) if current in options else None

        match cmd_id:
            case Commands.SELECT_OPTION:
                option = params.get("option")
                if not option or option not in options:
                    _LOG.warning("%s: invalid option %r", self.id, option)
                    return ucapi.StatusCodes.BAD_REQUEST
            case Commands.SELECT_FIRST:
                option = options[0]
            case Commands.SELECT_LAST:
                option = options[-1]
            case Commands.SELECT_NEXT:
                option = options[(idx + 1) % len(options)] if idx is not None else options[0]
            case Commands.SELECT_PREVIOUS:
                option = options[(idx - 1) % len(options)] if idx is not None else options[-1]
            case _:
                _LOG.warning("Unsupported select command: %s", cmd_id)
                return ucapi.StatusCodes.NOT_IMPLEMENTED

        return ucapi.StatusCodes.OK if await self._select(option) else ucapi.StatusCodes.SERVER_ERROR


class BluOSInputSelect(_BluOSOptionSelect):
    """Physical inputs (Bluetooth, optical, HDMI ARC, …); empty while streaming."""

    def __init__(self, device: BluOSDevice, player: BluOSPlayer):
        super().__init__(f"bluos_{device.id}_input", entity_name(device.name, "Input"), device, player)

    def _options(self) -> list[str]:
        return [inp.text or inp.id for inp in self._player.inputs if inp.text or inp.id]

    def _current(self, attributes: dict[str, Any]) -> str:
        return attributes.get("input_name") or ""

    async def _select(self, option: str) -> bool:
        return await self._player.select_input(option)


SLEEP_TIMER_OFF = "Off"


def sleep_timer_label(minutes: int) -> str:
    """Option label for a sleep timer step."""
    return SLEEP_TIMER_OFF if minutes == 0 else f"{minutes} min"


class BluOSSleepTimerSelect(_BluOSOptionSelect):
    """Sleep timer: Off, 15, 30, 45, 60 or 90 minutes."""

    def __init__(self, device: BluOSDevice, player: BluOSPlayer):
        super().__init__(f"bluos_{device.id}_sleep_timer", entity_name(device.name, "Sleep Timer"), device, player)

    def _options(self) -> list[str]:
        return [sleep_timer_label(step) for step in SLEEP_TIMER_STEPS]

    def _current(self, attributes: dict[str, Any]) -> str:
        minutes = attributes.get("sleep_timer") or 0
        # Map to the smallest step that covers the value, so a counting-down
        # timer does not change the option (and push) every minute.
        step = next((s for s in SLEEP_TIMER_STEPS if s >= minutes), SLEEP_TIMER_STEPS[-1])
        return sleep_timer_label(step)

    async def _select(self, option: str) -> bool:
        minutes = next(s for s in SLEEP_TIMER_STEPS if sleep_timer_label(s) == option)
        return await self._player.set_sleep_timer(minutes)
