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
        # Strong references: the event loop only keeps weak ones, and we must
        # finish or cancel them before closing zeroconf.
        self._resolve_tasks: set[asyncio.Task] = set()

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

        except Exception as e:
            _LOG.error("Discovery error: %s", e)
        finally:
            await self._shutdown()

        devices = list(self._devices.values())
        _LOG.info("Discovered %d BluOS devices", len(devices))
        return devices

    async def _shutdown(self) -> None:
        """Stop browsing, let in-flight resolves finish, then close zeroconf."""
        if self._browser:
            try:
                # AsyncServiceBrowser only has async_cancel(); cancel() is the
                # threaded ServiceBrowser API and raises AttributeError here.
                await self._browser.async_cancel()
            except Exception as e:
                _LOG.debug("Stopping discovery browser failed: %s", e)
            self._browser = None

        if self._resolve_tasks:
            # A resolve takes at most 3 s (async_request timeout); a device that
            # answered just before the deadline is still added.
            _, pending = await asyncio.wait(self._resolve_tasks, timeout=3.5)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self._resolve_tasks.clear()

        if self._azc:
            await self._azc.async_close()
            self._azc = None

    def _on_service_state_change(
        self,
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        """Handle service state changes."""
        if state_change == ServiceStateChange.Added:
            task = asyncio.create_task(self._resolve_service(zeroconf, service_type, name))
            self._resolve_tasks.add(task)
            task.add_done_callback(self._resolve_tasks.discard)
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
