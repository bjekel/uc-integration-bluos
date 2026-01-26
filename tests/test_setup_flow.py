"""Tests for setup_flow module."""

from unittest.mock import AsyncMock, patch

import pytest
import setup_flow
from config import BluOSDevice
from discover import DiscoveredDevice
from setup_flow import (
    SetupSteps,
    driver_setup_handler,
    get_configured_device,
    get_setup_data_schema,
)
from ucapi import (
    AbortDriverSetup,
    DriverSetupRequest,
    IntegrationSetupError,
    RequestUserInput,
    SetupComplete,
    SetupError,
    UserDataResponse,
)


class TestSetupSteps:
    """Tests for SetupSteps enum."""

    def test_all_steps_defined(self):
        """Test all setup steps are defined."""
        assert SetupSteps.INIT == 0
        assert SetupSteps.CONFIGURATION_MODE == 1
        assert SetupSteps.DISCOVER == 2
        assert SetupSteps.DEVICE_CHOICE == 3
        assert SetupSteps.DEVICE_CONFIGURE == 4


class TestGetSetupDataSchema:
    """Tests for get_setup_data_schema function."""

    def test_schema_structure(self):
        """Test schema has required structure."""
        schema = get_setup_data_schema()

        assert "title" in schema
        assert "en" in schema["title"]
        assert "settings" in schema
        assert isinstance(schema["settings"], list)


class TestDriverSetupHandler:
    """Tests for driver_setup_handler function."""

    @pytest.fixture(autouse=True)
    def reset_global_state(self):
        """Reset global state before each test."""
        setup_flow._setup_step = SetupSteps.INIT
        setup_flow._discovered_devices = []
        setup_flow._selected_device = None
        setup_flow._configured_device = None
        yield

    @pytest.mark.asyncio
    async def test_initial_setup_request(self):
        """Test handling initial setup request."""
        msg = DriverSetupRequest(reconfigure=False, setup_data={})

        result = await driver_setup_handler(msg)

        assert isinstance(result, RequestUserInput)
        assert setup_flow._setup_step == SetupSteps.DISCOVER

    @pytest.mark.asyncio
    async def test_reconfigure_request(self):
        """Test handling reconfigure request."""
        msg = DriverSetupRequest(reconfigure=True, setup_data={})

        result = await driver_setup_handler(msg)

        assert isinstance(result, RequestUserInput)
        assert setup_flow._setup_step == SetupSteps.CONFIGURATION_MODE

    @pytest.mark.asyncio
    async def test_abort_setup(self):
        """Test handling abort setup."""
        msg = AbortDriverSetup(error=IntegrationSetupError.NONE)

        result = await driver_setup_handler(msg)

        assert isinstance(result, SetupComplete)
        assert setup_flow._setup_step == SetupSteps.INIT

    @pytest.mark.asyncio
    async def test_configuration_mode_add_device(self):
        """Test configuration mode - add new device."""
        setup_flow._setup_step = SetupSteps.CONFIGURATION_MODE
        msg = UserDataResponse(input_values={"action": "add"})

        result = await driver_setup_handler(msg)

        assert isinstance(result, RequestUserInput)
        assert setup_flow._setup_step == SetupSteps.DISCOVER

    @pytest.mark.asyncio
    async def test_configuration_mode_reset(self):
        """Test configuration mode - reset."""
        setup_flow._setup_step = SetupSteps.CONFIGURATION_MODE
        msg = UserDataResponse(input_values={"action": "reset"})

        result = await driver_setup_handler(msg)

        assert isinstance(result, SetupComplete)

    @pytest.mark.asyncio
    async def test_discovery_auto_with_devices(self):
        """Test auto discovery finding devices."""
        setup_flow._setup_step = SetupSteps.DISCOVER

        discovered = [
            DiscoveredDevice(host="192.168.1.100", port=11000, name="Player 1"),
            DiscoveredDevice(host="192.168.1.101", port=11000, name="Player 2"),
        ]

        msg = UserDataResponse(input_values={"discovery_mode": "auto"})

        with patch("setup_flow.discover_bluos_players", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = discovered
            result = await driver_setup_handler(msg)

        assert isinstance(result, RequestUserInput)
        assert setup_flow._setup_step == SetupSteps.DEVICE_CHOICE
        assert len(setup_flow._discovered_devices) == 2

    @pytest.mark.asyncio
    async def test_discovery_auto_no_devices(self):
        """Test auto discovery finding no devices."""
        setup_flow._setup_step = SetupSteps.DISCOVER
        msg = UserDataResponse(input_values={"discovery_mode": "auto"})

        with patch("setup_flow.discover_bluos_players", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = []
            result = await driver_setup_handler(msg)

        assert isinstance(result, RequestUserInput)
        # Check settings list contains retry option
        setting_ids = [s["id"] for s in result.settings]
        assert "retry" in setting_ids

    @pytest.mark.asyncio
    async def test_discovery_manual(self):
        """Test manual IP entry."""
        setup_flow._setup_step = SetupSteps.DISCOVER
        msg = UserDataResponse(input_values={"discovery_mode": "manual", "manual_address": "192.168.1.200"})

        result = await driver_setup_handler(msg)

        assert isinstance(result, RequestUserInput)
        assert setup_flow._setup_step == SetupSteps.DEVICE_CHOICE
        assert len(setup_flow._discovered_devices) == 1
        assert setup_flow._discovered_devices[0].host == "192.168.1.200"

    @pytest.mark.asyncio
    async def test_device_choice(self):
        """Test device selection."""
        setup_flow._setup_step = SetupSteps.DEVICE_CHOICE
        setup_flow._discovered_devices = [
            DiscoveredDevice(host="192.168.1.100", port=11000, name="Player 1"),
            DiscoveredDevice(host="192.168.1.101", port=11000, name="Player 2"),
        ]

        msg = UserDataResponse(input_values={"device": "1"})

        result = await driver_setup_handler(msg)

        assert isinstance(result, RequestUserInput)
        assert setup_flow._setup_step == SetupSteps.DEVICE_CONFIGURE
        assert setup_flow._selected_device.host == "192.168.1.101"

    @pytest.mark.asyncio
    async def test_device_choice_invalid_index(self):
        """Test device selection with invalid index."""
        setup_flow._setup_step = SetupSteps.DEVICE_CHOICE
        setup_flow._discovered_devices = [
            DiscoveredDevice(host="192.168.1.100", port=11000, name="Player 1"),
        ]

        msg = UserDataResponse(input_values={"device": "99"})

        result = await driver_setup_handler(msg)

        assert isinstance(result, SetupError)

    @pytest.mark.asyncio
    async def test_device_configure(self):
        """Test device configuration."""
        setup_flow._setup_step = SetupSteps.DEVICE_CONFIGURE
        setup_flow._selected_device = DiscoveredDevice(
            host="192.168.1.100",
            port=11000,
            name="Original Name",
            model="Node 2i",
            mac="00:11:22:33:44:55",
        )

        msg = UserDataResponse(
            input_values={
                "name": "My BluOS Player",
                "volume_step": "10",
                "timeout": "5",
            }
        )

        result = await driver_setup_handler(msg)

        assert isinstance(result, SetupComplete)

        # Get the configured device from the module
        device = get_configured_device()
        assert device is not None
        assert device.name == "My BluOS Player"
        assert device.address == "192.168.1.100"
        assert device.volume_step == 10
        assert device.id == "00:11:22:33:44:55"

    @pytest.mark.asyncio
    async def test_device_configure_without_mac(self):
        """Test device configuration without MAC address."""
        setup_flow._setup_step = SetupSteps.DEVICE_CONFIGURE
        setup_flow._selected_device = DiscoveredDevice(
            host="192.168.1.100",
            port=11000,
            name="Player",
        )

        msg = UserDataResponse(
            input_values={
                "name": "Player",
                "volume_step": "5",
                "timeout": "5",
            }
        )

        result = await driver_setup_handler(msg)

        assert isinstance(result, SetupComplete)

        device = get_configured_device()
        assert device is not None
        # ID should be generated from IP
        assert device.id == "192_168_1_100"

    @pytest.mark.asyncio
    async def test_device_configure_no_selected_device(self):
        """Test device configure without selected device."""
        setup_flow._setup_step = SetupSteps.DEVICE_CONFIGURE
        setup_flow._selected_device = None

        msg = UserDataResponse(input_values={})

        result = await driver_setup_handler(msg)

        assert isinstance(result, SetupError)

    @pytest.mark.asyncio
    async def test_unexpected_message_type(self):
        """Test handling unexpected message type."""

        class UnknownMessage:
            pass

        result = await driver_setup_handler(UnknownMessage())

        assert isinstance(result, SetupError)

    @pytest.mark.asyncio
    async def test_unexpected_setup_step(self):
        """Test handling unexpected setup step."""
        setup_flow._setup_step = 99  # Invalid step
        msg = UserDataResponse(input_values={})

        result = await driver_setup_handler(msg)

        assert isinstance(result, SetupError)


class TestGetConfiguredDevice:
    """Tests for get_configured_device function."""

    def test_get_and_clear(self):
        """Test getting configured device clears it."""
        device = BluOSDevice(
            id="test",
            name="Test",
            address="192.168.1.100",
        )
        setup_flow._configured_device = device

        result = get_configured_device()
        assert result == device

        # Should be cleared after retrieval
        result2 = get_configured_device()
        assert result2 is None

    def test_get_when_none(self):
        """Test getting configured device when none set."""
        setup_flow._configured_device = None

        result = get_configured_device()
        assert result is None
