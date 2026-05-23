"""mDNS discovery for BluOS devices."""

import asyncio
import ipaddress
import logging
from dataclasses import dataclass

from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

_LOG = logging.getLogger(__name__)

# BluOS uses _musc._tcp for music control
BLUOS_SERVICE_TYPE = "_musc._tcp.local."
DEFAULT_PORT = 11000


@dataclass
class DiscoveredDevice:
    """Discovered BluOS device information."""

    host: str
    port: int
    name: str
    model: str | None = None
    mac: str | None = None


class BluOSDiscovery:
    """BluOS device discovery using mDNS/Zeroconf."""

    def __init__(self):
        """Initialize discovery."""
        self._devices: dict[str, DiscoveredDevice] = {}
        self._azc: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None

    async def discover(self, timeout: float = 5.0) -> list[DiscoveredDevice]:
        """
        Discover BluOS devices on the local network.

        Args:
            timeout: Discovery timeout in seconds

        Returns:
            List of discovered devices
        """
        self._devices.clear()

        try:
            self._azc = AsyncZeroconf()

            self._browser = AsyncServiceBrowser(
                self._azc.zeroconf,
                [BLUOS_SERVICE_TYPE],
                handlers=[self._on_service_state_change],
            )

            _LOG.debug("Starting BluOS discovery for %.1f seconds", timeout)
            await asyncio.sleep(timeout)

            if self._browser:
                self._browser.cancel()
                self._browser = None

        except Exception as e:
            _LOG.error("Discovery error: %s", e)
        finally:
            if self._azc:
                await self._azc.async_close()
                self._azc = None

        devices = list(self._devices.values())
        _LOG.info("Discovered %d BluOS devices", len(devices))
        return devices

    def _on_service_state_change(
        self,
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        """Handle service state changes."""
        if state_change == ServiceStateChange.Added:
            asyncio.create_task(self._resolve_service(zeroconf, service_type, name))
        elif state_change == ServiceStateChange.Removed:
            if name in self._devices:
                _LOG.debug("Device removed: %s", name)
                del self._devices[name]

    async def _resolve_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        """Resolve service information."""
        try:
            info = AsyncServiceInfo(service_type, name)
            if await info.async_request(zeroconf, 3000):
                await self._process_service_info(name, info)
        except Exception as e:
            _LOG.warning("Failed to resolve service %s: %s", name, e)

    async def _process_service_info(self, name: str, info: AsyncServiceInfo) -> None:
        """Process resolved service information."""
        addresses = info.parsed_addresses()
        if not addresses:
            _LOG.warning("No addresses for service %s", name)
            return

        # Prefer IPv4 addresses
        host = next(
            (addr for addr in addresses if isinstance(ipaddress.ip_address(addr), ipaddress.IPv4Address)),
            addresses[0],
        )

        port = info.port or DEFAULT_PORT

        # Extract properties
        properties = info.properties or {}
        model = self._get_property(properties, "model")
        mac = self._get_property(properties, "mac")

        # Use the service name or property for device name
        device_name = self._get_property(properties, "name")
        if not device_name:
            # Extract name from service name (format: "DeviceName._musc._tcp.local.")
            device_name = name.replace(f".{BLUOS_SERVICE_TYPE}", "")

        device = DiscoveredDevice(
            host=host,
            port=port,
            name=device_name,
            model=model,
            mac=mac,
        )

        self._devices[name] = device
        _LOG.info("Discovered: %s (%s) at %s:%d", device.name, device.model, host, port)

    @staticmethod
    def _get_property(properties: dict, key: str) -> str | None:
        """Get string property from service properties."""
        value = properties.get(key.encode()) or properties.get(key)
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)


async def discover_bluos_players(timeout: float = 5.0) -> list[DiscoveredDevice]:
    """
    Discover BluOS players on the local network.

    Args:
        timeout: Discovery timeout in seconds

    Returns:
        List of discovered devices
    """
    discovery = BluOSDiscovery()
    return await discovery.discover(timeout)
