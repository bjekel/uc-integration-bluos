"""Configuration persistence for BluOS integration."""

import dataclasses
import json
import logging
import os
from dataclasses import dataclass
from typing import Callable

_LOG = logging.getLogger(__name__)


@dataclass
class BluOSDevice:
    """Configuration for a BluOS device."""

    id: str
    name: str
    address: str
    port: int = 11000
    volume_step: int = 5
    timeout: float = 5.0
    standby_timeout: int = 60
    active_poll_timeout: int = 30
    model: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BluOSDevice":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            address=data["address"],
            port=data.get("port", 11000),
            volume_step=data.get("volume_step", 5),
            timeout=data.get("timeout", 5.0),
            standby_timeout=data.get("standby_timeout", 60),
            active_poll_timeout=data.get("active_poll_timeout", 30),
            model=data.get("model"),
        )


class Devices:
    """Manager for configured BluOS devices."""

    def __init__(
        self,
        data_path: str,
        add_handler: Callable[[BluOSDevice], None] | None = None,
        remove_handler: Callable[[str], None] | None = None,
    ):
        """
        Initialize device manager.

        Args:
            data_path: Directory for storing configuration
            add_handler: Callback when device is added
            remove_handler: Callback when device is removed
        """
        self._data_path = data_path
        self._config_file = os.path.join(data_path, "config.json")
        self._devices: dict[str, BluOSDevice] = {}
        self._add_handler = add_handler
        self._remove_handler = remove_handler

    @property
    def data_path(self) -> str:
        """Return data path."""
        return self._data_path

    def load(self) -> bool:
        """
        Load configuration from disk.

        Returns:
            True if configuration was loaded, False if file doesn't exist
        """
        try:
            with open(self._config_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for device_data in data.get("devices", []):
                device = BluOSDevice.from_dict(device_data)
                self._devices[device.id] = device

            _LOG.info("Loaded %d devices from configuration", len(self._devices))
            return True
        except FileNotFoundError:
            _LOG.debug("No configuration file found at %s", self._config_file)
            return False
        except (json.JSONDecodeError, KeyError) as e:
            _LOG.error("Failed to load configuration: %s", e)
            return False

    def store(self) -> bool:
        """
        Persist configuration to disk.

        Returns:
            True if successful, False otherwise
        """
        try:
            os.makedirs(self._data_path, exist_ok=True)
            data = {"devices": [d.to_dict() for d in self._devices.values()]}
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            _LOG.debug("Configuration saved to %s", self._config_file)
            return True
        except OSError as e:
            _LOG.error("Failed to save configuration: %s", e)
            return False

    def add_or_update(self, device: BluOSDevice, trigger_callbacks: bool = True) -> bool:
        """
        Add or update a device configuration.

        Args:
            device: Device configuration to add/update
            trigger_callbacks: Whether to trigger add/update callbacks

        Returns:
            True if device was newly added, False if updated
        """
        is_new = device.id not in self._devices
        self._devices[device.id] = device
        self.store()

        if is_new:
            _LOG.info("Device added: %s (%s)", device.name, device.id)
            if trigger_callbacks and self._add_handler:
                self._add_handler(device)
        else:
            _LOG.info("Device updated: %s (%s)", device.name, device.id)

        return is_new

    def remove(self, device_id: str) -> bool:
        """
        Remove a device configuration.

        Args:
            device_id: ID of device to remove

        Returns:
            True if device was removed, False if not found
        """
        if device_id not in self._devices:
            return False

        device = self._devices.pop(device_id)
        self.store()

        if self._remove_handler:
            _LOG.info("Device removed: %s (%s)", device.name, device_id)
            self._remove_handler(device_id)

        return True

    def get(self, device_id: str) -> BluOSDevice | None:
        """
        Get device configuration by ID.

        Args:
            device_id: Device ID

        Returns:
            Device configuration or None if not found
        """
        return self._devices.get(device_id)

    def all(self) -> list[BluOSDevice]:
        """Get all configured devices."""
        return list(self._devices.values())

    def contains(self, device_id: str) -> bool:
        """Check if device is configured."""
        return device_id in self._devices

    def clear(self) -> None:
        """Remove all devices."""
        device_ids = list(self._devices.keys())
        for device_id in device_ids:
            device = self._devices.pop(device_id)
            if self._remove_handler:
                _LOG.info("Device removed: %s (%s)", device.name, device_id)
                self._remove_handler(device_id)
        self.store()

    def export(self) -> str:
        """
        Export configuration as a JSON string.

        Returns:
            JSON string with all device configurations
        """
        data = {"devices": [d.to_dict() for d in self._devices.values()]}
        return json.dumps(data, indent=2)

    def import_config(self, json_str: str) -> bool:
        """
        Import configuration from a JSON string.

        Parses and validates all devices before touching the current state,
        then replaces all existing devices with the imported ones.

        Args:
            json_str: JSON string in the same format as exported by export()

        Returns:
            True if import succeeded, False on parse/validation error
        """
        try:
            data = json.loads(json_str)
            new_devices = [BluOSDevice.from_dict(d) for d in data.get("devices", [])]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            _LOG.error("Invalid configuration format: %s", e)
            return False

        snapshot = dict(self._devices)
        try:
            self.clear()
            for device in new_devices:
                self.add_or_update(device)
            _LOG.info("Imported %d device(s)", len(new_devices))
            return True
        except Exception as e:  # pylint: disable=broad-except
            _LOG.error("Failed to apply imported configuration: %s", e)
            self._devices = snapshot
            self.store()
            return False

    def __len__(self) -> int:
        """Return number of configured devices."""
        return len(self._devices)

    def __iter__(self):
        """Iterate over configured devices."""
        return iter(self._devices.values())
