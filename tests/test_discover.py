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
from zeroconf import ServiceStateChange


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
                with patch("discover.AsyncServiceBrowser", autospec=True) as mock_browser:
                    devices = await discovery.discover(timeout=0.1)

        assert devices == []
        mock_browser.return_value.async_cancel.assert_awaited_once()
        mock_zc.return_value.async_close.assert_awaited_once()

    @staticmethod
    def _patched_zeroconf():
        zc = patch("discover.AsyncZeroconf")
        browser = patch("discover.AsyncServiceBrowser", autospec=True)
        return zc, browser

    @pytest.mark.asyncio
    async def test_browser_cancel_failure_still_closes_zeroconf(self, discovery):
        zc_patch, browser_patch = self._patched_zeroconf()
        with zc_patch as mock_zc, browser_patch as mock_browser:
            mock_zc.return_value.async_close = AsyncMock()
            mock_browser.return_value.async_cancel.side_effect = RuntimeError("boom")
            assert await discovery.discover(timeout=0.01) == []
        mock_zc.return_value.async_close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_in_flight_resolve_finishes_before_close(self, discovery):
        """A device that answers just before the deadline is still returned."""
        zc_patch, browser_patch = self._patched_zeroconf()

        async def slow_resolve(_zc, _type, name):
            await asyncio.sleep(0.2)
            discovery._devices[name] = DiscoveredDevice(host="10.0.0.5", port=11000, name="Late")

        def start_browser(zeroconf, _types, handlers):
            handlers[0](zeroconf, BLUOS_SERVICE_TYPE, "Late._musc._tcp.local.", ServiceStateChange.Added)
            return MagicMock(async_cancel=AsyncMock())

        with (
            zc_patch as mock_zc,
            browser_patch as mock_browser,
            patch.object(discovery, "_resolve_service", slow_resolve),
        ):
            mock_zc.return_value.async_close = AsyncMock()
            mock_browser.side_effect = start_browser
            devices = await discovery.discover(timeout=0.01)

        assert [d.name for d in devices] == ["Late"]
        assert not discovery._resolve_tasks

    @pytest.mark.asyncio
    async def test_hanging_resolve_is_cancelled(self, discovery, monkeypatch):
        zc_patch, browser_patch = self._patched_zeroconf()
        cancelled = asyncio.Event()

        async def hanging_resolve(_zc, _type, _name):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        def start_browser(zeroconf, _types, handlers):
            handlers[0](zeroconf, BLUOS_SERVICE_TYPE, "Hang._musc._tcp.local.", ServiceStateChange.Added)
            return MagicMock(async_cancel=AsyncMock())

        real_wait = asyncio.wait
        monkeypatch.setattr(asyncio, "wait", lambda fs, timeout: real_wait(fs, timeout=0.05))
        with (
            zc_patch as mock_zc,
            browser_patch as mock_browser,
            patch.object(discovery, "_resolve_service", hanging_resolve),
        ):
            mock_zc.return_value.async_close = AsyncMock()
            mock_browser.side_effect = start_browser
            assert await discovery.discover(timeout=0.01) == []

        assert cancelled.is_set()
        mock_zc.return_value.async_close.assert_awaited_once()


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
