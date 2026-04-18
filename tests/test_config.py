"""Tests for config module."""

import json
import os
import tempfile
from unittest.mock import MagicMock

import pytest
from config import BluOSDevice, Devices


class TestBluOSDevice:
    """Tests for BluOSDevice dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        device = BluOSDevice(id="test", name="Test Device", address="192.168.1.100")
        assert device.port == 11000
        assert device.volume_step == 5
        assert device.timeout == 5.0
        assert device.model is None

    def test_custom_values(self):
        """Test custom values are set correctly."""
        device = BluOSDevice(
            id="test",
            name="Test Device",
            address="192.168.1.100",
            port=12000,
            volume_step=10,
            timeout=10.0,
            model="Node 2i",
        )
        assert device.port == 12000
        assert device.volume_step == 10
        assert device.timeout == 10.0
        assert device.model == "Node 2i"

    def test_to_dict(self):
        """Test conversion to dictionary."""
        device = BluOSDevice(
            id="test",
            name="Test Device",
            address="192.168.1.100",
            model="Node 2i",
        )
        data = device.to_dict()
        assert data["id"] == "test"
        assert data["name"] == "Test Device"
        assert data["address"] == "192.168.1.100"
        assert data["port"] == 11000
        assert data["model"] == "Node 2i"

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "id": "test",
            "name": "Test Device",
            "address": "192.168.1.100",
            "port": 12000,
            "volume_step": 10,
            "timeout": 10.0,
            "model": "Node 2i",
        }
        device = BluOSDevice.from_dict(data)
        assert device.id == "test"
        assert device.name == "Test Device"
        assert device.port == 12000
        assert device.model == "Node 2i"

    def test_from_dict_minimal(self):
        """Test creation from minimal dictionary."""
        data = {
            "id": "test",
            "name": "Test Device",
            "address": "192.168.1.100",
        }
        device = BluOSDevice.from_dict(data)
        assert device.id == "test"
        assert device.port == 11000
        assert device.volume_step == 5


class TestDevices:
    """Tests for Devices manager."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def devices(self, temp_dir):
        """Create a Devices instance for tests."""
        return Devices(temp_dir)

    def test_empty_load(self, devices):
        """Test loading when no config file exists."""
        result = devices.load()
        assert result is False
        assert len(devices) == 0

    def test_add_and_store(self, devices, temp_dir):
        """Test adding a device and storing."""
        device = BluOSDevice(id="test", name="Test Device", address="192.168.1.100")
        devices.add_or_update(device)

        assert len(devices) == 1
        assert devices.contains("test")

        # Verify file was created
        config_file = os.path.join(temp_dir, "config.json")
        assert os.path.exists(config_file)

        # Verify content
        with open(config_file) as f:
            data = json.load(f)
        assert len(data["devices"]) == 1
        assert data["devices"][0]["id"] == "test"

    def test_load_existing(self, temp_dir):
        """Test loading existing configuration."""
        config_file = os.path.join(temp_dir, "config.json")
        data = {
            "devices": [
                {
                    "id": "test1",
                    "name": "Device 1",
                    "address": "192.168.1.100",
                },
                {
                    "id": "test2",
                    "name": "Device 2",
                    "address": "192.168.1.101",
                    "port": 12000,
                },
            ]
        }
        with open(config_file, "w") as f:
            json.dump(data, f)

        devices = Devices(temp_dir)
        result = devices.load()

        assert result is True
        assert len(devices) == 2
        assert devices.contains("test1")
        assert devices.contains("test2")

    def test_get_device(self, devices):
        """Test getting a device by ID."""
        device = BluOSDevice(id="test", name="Test Device", address="192.168.1.100")
        devices.add_or_update(device)

        retrieved = devices.get("test")
        assert retrieved is not None
        assert retrieved.id == "test"
        assert retrieved.name == "Test Device"

    def test_get_nonexistent(self, devices):
        """Test getting a nonexistent device."""
        retrieved = devices.get("nonexistent")
        assert retrieved is None

    def test_remove_device(self, devices):
        """Test removing a device."""
        device = BluOSDevice(id="test", name="Test Device", address="192.168.1.100")
        devices.add_or_update(device)
        assert len(devices) == 1

        result = devices.remove("test")
        assert result is True
        assert len(devices) == 0
        assert not devices.contains("test")

    def test_remove_nonexistent(self, devices):
        """Test removing a nonexistent device."""
        result = devices.remove("nonexistent")
        assert result is False

    def test_update_device(self, devices):
        """Test updating an existing device."""
        device1 = BluOSDevice(id="test", name="Test Device", address="192.168.1.100")
        devices.add_or_update(device1)

        device2 = BluOSDevice(id="test", name="Updated Name", address="192.168.1.100")
        devices.add_or_update(device2)

        assert len(devices) == 1
        retrieved = devices.get("test")
        assert retrieved.name == "Updated Name"

    def test_all_devices(self, devices):
        """Test getting all devices."""
        device1 = BluOSDevice(id="test1", name="Device 1", address="192.168.1.100")
        device2 = BluOSDevice(id="test2", name="Device 2", address="192.168.1.101")
        devices.add_or_update(device1)
        devices.add_or_update(device2)

        all_devices = devices.all()
        assert len(all_devices) == 2

    def test_clear_devices(self, devices):
        """Test clearing all devices."""
        device1 = BluOSDevice(id="test1", name="Device 1", address="192.168.1.100")
        device2 = BluOSDevice(id="test2", name="Device 2", address="192.168.1.101")
        devices.add_or_update(device1)
        devices.add_or_update(device2)

        devices.clear()
        assert len(devices) == 0

    def test_add_handler_callback(self, temp_dir):
        """Test add handler callback is called."""
        add_handler = MagicMock()
        devices = Devices(temp_dir, add_handler=add_handler)

        device = BluOSDevice(id="test", name="Test Device", address="192.168.1.100")
        devices.add_or_update(device)

        add_handler.assert_called_once()

    def test_remove_handler_callback(self, temp_dir):
        """Test remove handler callback is called."""
        remove_handler = MagicMock()
        devices = Devices(temp_dir, remove_handler=remove_handler)

        device = BluOSDevice(id="test", name="Test Device", address="192.168.1.100")
        devices.add_or_update(device)
        devices.remove("test")

        remove_handler.assert_called_once_with("test")

    def test_iterator(self, devices):
        """Test iterating over devices."""
        device1 = BluOSDevice(id="test1", name="Device 1", address="192.168.1.100")
        device2 = BluOSDevice(id="test2", name="Device 2", address="192.168.1.101")
        devices.add_or_update(device1)
        devices.add_or_update(device2)

        ids = [d.id for d in devices]
        assert "test1" in ids
        assert "test2" in ids

    def test_export_empty(self, devices):
        """Test export with no devices produces valid JSON."""
        result = devices.export()
        data = json.loads(result)
        assert data == {"devices": []}

    def test_export_roundtrip(self, devices):
        """Test that export followed by import_config restores identical devices."""
        device1 = BluOSDevice(id="test1", name="Device 1", address="192.168.1.100", port=11000)
        device2 = BluOSDevice(id="test2", name="Device 2", address="192.168.1.101", model="Node")
        devices.add_or_update(device1)
        devices.add_or_update(device2)

        exported = devices.export()

        fresh = Devices(devices.data_path)
        assert fresh.import_config(exported) is True
        assert len(fresh) == 2
        assert fresh.contains("test1")
        assert fresh.contains("test2")
        assert fresh.get("test2").model == "Node"

    def test_import_config_replaces_existing(self, temp_dir):
        """Test that import_config replaces all existing devices and fires callbacks."""
        add_handler = MagicMock()
        remove_handler = MagicMock()
        devices = Devices(temp_dir, add_handler=add_handler, remove_handler=remove_handler)

        old_device = BluOSDevice(id="old", name="Old Device", address="10.0.0.1")
        devices.add_or_update(old_device)
        add_handler.reset_mock()

        new_config = json.dumps(
            {
                "devices": [
                    {"id": "new1", "name": "New Device 1", "address": "10.0.0.2"},
                    {"id": "new2", "name": "New Device 2", "address": "10.0.0.3"},
                ]
            }
        )

        result = devices.import_config(new_config)

        assert result is True
        assert len(devices) == 2
        assert not devices.contains("old")
        assert devices.contains("new1")
        assert devices.contains("new2")
        remove_handler.assert_called_once_with("old")
        assert add_handler.call_count == 2

    def test_import_config_invalid_json(self, devices):
        """Test that import_config returns False and leaves state unchanged on invalid JSON."""
        device = BluOSDevice(id="test", name="Test Device", address="192.168.1.100")
        devices.add_or_update(device)

        result = devices.import_config("not valid json {{{")

        assert result is False
        assert len(devices) == 1
        assert devices.contains("test")

    def test_import_config_missing_required_field(self, devices):
        """Test that import_config returns False when a device entry is missing a required field."""
        device = BluOSDevice(id="existing", name="Existing", address="192.168.1.1")
        devices.add_or_update(device)

        bad_config = json.dumps(
            {
                "devices": [
                    # Missing required "id" field
                    {"name": "No ID Device", "address": "192.168.1.200"}
                ]
            }
        )

        result = devices.import_config(bad_config)

        assert result is False
        assert len(devices) == 1
        assert devices.contains("existing")
