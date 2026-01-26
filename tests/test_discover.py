"""Tests for discover module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from discover import (
    BLUOS_SERVICE_TYPE,
    DEFAULT_PORT,
    BluOSDiscovery,
    DiscoveredDevice,
    discover_bluos_players,
)


class TestDiscoveredDevice:
    """Tests for DiscoveredDevice dataclass."""

    def test_basic_creation(self):
        """Test basic device creation."""
        device = DiscoveredDevice(
            host="192.168.1.100",
            port=11000,
            name="Test Player",
        )
        assert device.host == "192.168.1.100"
        assert device.port == 11000
        assert device.name == "Test Player"
        assert device.model is None
        assert device.mac is None

    def test_full_creation(self):
        """Test device creation with all fields."""
        device = DiscoveredDevice(
            host="192.168.1.100",
            port=11000,
            name="Test Player",
            model="Node 2i",
            mac="00:11:22:33:44:55",
        )
        assert device.model == "Node 2i"
        assert device.mac == "00:11:22:33:44:55"


class TestBluOSDiscovery:
    """Tests for BluOSDiscovery class."""

    @pytest.fixture
    def discovery(self):
        """Create a BluOSDiscovery instance."""
        return BluOSDiscovery()

    def test_service_type(self):
        """Test the service type constant."""
        assert BLUOS_SERVICE_TYPE == "_musc._tcp.local."
        assert DEFAULT_PORT == 11000

    def test_get_property_string(self):
        """Test getting a string property."""
        result = BluOSDiscovery._get_property({"key": "value"}, "key")
        assert result == "value"

    def test_get_property_bytes(self):
        """Test getting a bytes property."""
        result = BluOSDiscovery._get_property({b"key": b"value"}, "key")
        assert result == "value"

    def test_get_property_missing(self):
        """Test getting a missing property."""
        result = BluOSDiscovery._get_property({}, "key")
        assert result is None

    @pytest.mark.asyncio
    async def test_discover_no_devices(self, discovery):
        """Test discovery when no devices are found."""
        with patch.object(discovery, "_azc", None):
            with patch("discover.AsyncZeroconf") as mock_zc:
                mock_zc.return_value.async_close = AsyncMock()
                with patch("discover.AsyncServiceBrowser") as mock_browser:
                    mock_browser.return_value.cancel = MagicMock()
                    devices = await discovery.discover(timeout=0.1)

        assert devices == []


class TestDiscoverBluosPlayers:
    """Tests for discover_bluos_players function."""

    @pytest.mark.asyncio
    async def test_discover_creates_discovery_instance(self):
        """Test that discover function creates discovery instance."""
        with patch.object(BluOSDiscovery, "discover", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = []
            result = await discover_bluos_players(timeout=1.0)

            mock_discover.assert_called_once_with(1.0)
            assert result == []

    @pytest.mark.asyncio
    async def test_discover_returns_devices(self):
        """Test that discover function returns discovered devices."""
        expected_devices = [
            DiscoveredDevice(host="192.168.1.100", port=11000, name="Player 1"),
            DiscoveredDevice(host="192.168.1.101", port=11000, name="Player 2"),
        ]

        with patch.object(BluOSDiscovery, "discover", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = expected_devices
            result = await discover_bluos_players(timeout=5.0)

            assert len(result) == 2
            assert result[0].name == "Player 1"
            assert result[1].name == "Player 2"
