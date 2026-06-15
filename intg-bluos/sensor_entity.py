"""UC Sensor entity exposing BluOS multi-room group state."""

import logging
from typing import Any, Callable

import ucapi
from bluos import BluOSPlayer
from config import BluOSDevice
from entity_mixin import DiffPushMixin
from ucapi.sensor import Attributes, DeviceClasses, States

_LOG = logging.getLogger(__name__)


class BluOSGroupSensor(DiffPushMixin, ucapi.Sensor):
    """Sensor entity reflecting a player's current multi-room group membership."""

    def __init__(
        self,
        device: BluOSDevice,
        player: BluOSPlayer,
        group_targets: Callable[[], list[BluOSPlayer]] | None = None,
    ):
        """
        Initialize the group sensor.

        Args:
            device: Device configuration
            player: BluOS player wrapper
            group_targets: Callable returning the other configured players, used
                to map group endpoints back to friendly room names.
        """
        entity_id = f"bluos_{device.id}_group"
        name = f"{device.name} Group"

        super().__init__(
            entity_id,
            name,
            features=[],
            attributes={
                Attributes.STATE: States.UNAVAILABLE,
                Attributes.VALUE: "",
            },
            device_class=DeviceClasses.CUSTOM,
        )

        self._device = device
        self._player = player
        self._group_targets = group_targets

    def _get_targets(self) -> list[BluOSPlayer]:
        """Other configured players, for endpoint -> name resolution."""
        return list(self._group_targets()) if self._group_targets else []

    def _resolve_name(self, endpoint: tuple[str, int] | None) -> str:
        """Map an (ip, port) endpoint to a friendly room name, or the ip."""
        if not endpoint:
            return ""
        ip, port = endpoint
        for target in self._get_targets():
            if target.device.address == ip and target.device.port == port:
                return target.device.name
        return ip

    def _compute(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """Compute STATE and VALUE from a BluOS status update."""
        if not self._player.available:
            return {Attributes.STATE: States.UNAVAILABLE, Attributes.VALUE: ""}

        role = attributes.get("group_role", "standalone")
        if role == "follower":
            leader_name = self._resolve_name(attributes.get("group_leader"))
            value = f"Following {leader_name}" if leader_name else "Following"
        elif role == "leader":
            names = [self._resolve_name(f) for f in (attributes.get("group_followers") or [])]
            value = f"Leader ({', '.join(names)})" if names else "Leader"
        else:
            value = "Not grouped"

        return {Attributes.STATE: States.ON, Attributes.VALUE: value}

    def update_attributes(self, attributes: dict[str, Any]) -> dict[str, Any]:
        """
        Compute sensor attributes from a BluOS status update and return only
        those that differ from the current entity state.

        Args:
            attributes: New attributes from BluOS player (includes group_role etc.)

        Returns:
            Dictionary of changed attributes
        """
        return self._diff_attributes(self._compute(attributes))

    def set_unavailable(self) -> dict[str, Any]:
        """Mark entity as unavailable and return changed attributes."""
        if self.attributes.get(Attributes.STATE) != States.UNAVAILABLE:
            changed = {Attributes.STATE: States.UNAVAILABLE, Attributes.VALUE: ""}
            self.attributes.update(changed)
            return changed
        return {}
