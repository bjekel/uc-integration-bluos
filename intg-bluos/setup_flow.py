"""Setup flow for BluOS integration."""

import ipaddress
import logging
from enum import IntEnum
from typing import Any

from config import BluOSDevice
from discover import DiscoveredDevice, discover_bluos_players
from ucapi import (
    AbortDriverSetup,
    DriverSetupRequest,
    RequestUserInput,
    SetupAction,
    SetupComplete,
    SetupDriver,
    SetupError,
    UserDataResponse,
)

# Validation constants
MIN_PORT = 1
MAX_PORT = 65535
DEFAULT_BLUOS_PORT = 11000

_LOG = logging.getLogger(__name__)


def _is_valid_ip_address(address: str) -> bool:
    """Validate IP address format (IPv4 or IPv6)."""
    try:
        ipaddress.ip_address(address)
        return True
    except ValueError:
        return False


def _is_valid_port(port: int) -> bool:
    """Validate port is within valid range."""
    return MIN_PORT <= port <= MAX_PORT


class SetupSteps(IntEnum):
    """Setup wizard steps."""

    INIT = 0
    CONFIGURATION_MODE = 1
    DISCOVER = 2
    DEVICE_CHOICE = 3
    DEVICE_CONFIGURE = 4


# Global setup state
_setup_step = SetupSteps.INIT
_discovered_devices: list[DiscoveredDevice] = []
_selected_device: DiscoveredDevice | None = None
_configured_device: BluOSDevice | None = None


def get_configured_device() -> BluOSDevice | None:
    """Get the device configured during setup and clear it."""
    global _configured_device
    device = _configured_device
    _configured_device = None
    return device


async def driver_setup_handler(msg: SetupDriver) -> SetupAction:
    """
    Handle setup driver messages.

    Args:
        msg: Setup message from UC Remote

    Returns:
        Setup action response
    """
    global _setup_step, _discovered_devices, _selected_device

    if isinstance(msg, DriverSetupRequest):
        _setup_step = SetupSteps.INIT
        _discovered_devices = []
        _selected_device = None
        return await _handle_setup_request(msg)

    if isinstance(msg, UserDataResponse):
        return await _handle_user_data(msg)

    if isinstance(msg, AbortDriverSetup):
        _LOG.info("Setup aborted")
        _setup_step = SetupSteps.INIT
        return SetupComplete()

    _LOG.error("Unknown setup message: %s", type(msg))
    return SetupError()


async def _handle_setup_request(msg: DriverSetupRequest) -> SetupAction:
    """Handle initial setup request."""
    global _setup_step

    # Check if this is a reconfiguration with existing devices
    if msg.reconfigure:
        _setup_step = SetupSteps.CONFIGURATION_MODE
        return _show_configuration_mode()

    # Check setup mode from initial form
    setup_mode = msg.setup_data.get("setup_mode", "discover") if msg.setup_data else "discover"
    _LOG.info("Setup mode: %s", setup_mode)

    if setup_mode == "manual":
        # Show manual IP entry form
        _setup_step = SetupSteps.DISCOVER
        return _show_manual_entry()

    # Start auto discovery
    _setup_step = SetupSteps.DISCOVER
    return await _start_discovery()


async def _handle_user_data(msg: UserDataResponse) -> SetupAction:
    """Handle user data response."""
    global _setup_step

    match _setup_step:
        case SetupSteps.CONFIGURATION_MODE:
            return await _handle_configuration_mode(msg)
        case SetupSteps.DISCOVER:
            return await _handle_discovery(msg)
        case SetupSteps.DEVICE_CHOICE:
            return await _handle_device_choice(msg)
        case SetupSteps.DEVICE_CONFIGURE:
            return await _handle_device_configure(msg)
        case _:
            _LOG.error("Unexpected setup step: %s", _setup_step)
            return SetupError()


def _show_configuration_mode() -> SetupAction:
    """Show configuration mode selection."""
    return RequestUserInput(
        {"en": "BluOS Configuration", "de": "BluOS Konfiguration"},
        [
            {
                "id": "action",
                "label": {"en": "Action", "de": "Aktion"},
                "field": {
                    "dropdown": {
                        "value": "add",
                        "items": [
                            {
                                "id": "add",
                                "label": {
                                    "en": "Add new device",
                                    "de": "Neues Gerät hinzufügen",
                                },
                            },
                            {
                                "id": "reset",
                                "label": {
                                    "en": "Reset configuration",
                                    "de": "Konfiguration zurücksetzen",
                                },
                            },
                        ],
                    }
                },
            }
        ],
    )


async def _handle_configuration_mode(msg: UserDataResponse) -> SetupAction:
    """Handle configuration mode selection."""
    global _setup_step

    action = msg.input_values.get("action", "add")

    if action == "reset":
        # Return to let driver.py handle the reset
        return SetupComplete()

    # Add new device - start discovery
    _setup_step = SetupSteps.DISCOVER
    return _show_discovery_options()


def _show_discovery_options() -> SetupAction:
    """Show discovery options."""
    return RequestUserInput(
        {"en": "Device Discovery", "de": "Geräteerkennung"},
        [
            {
                "id": "discovery_mode",
                "label": {"en": "Discovery Method", "de": "Erkennungsmethode"},
                "field": {
                    "dropdown": {
                        "value": "auto",
                        "items": [
                            {
                                "id": "auto",
                                "label": {
                                    "en": "Auto-discover (recommended)",
                                    "de": "Automatisch erkennen (empfohlen)",
                                },
                            },
                            {
                                "id": "manual",
                                "label": {
                                    "en": "Manual IP entry",
                                    "de": "Manuelle IP-Eingabe",
                                },
                            },
                        ],
                    }
                },
            },
            {
                "id": "manual_address",
                "label": {
                    "en": "IP Address (for manual entry)",
                    "de": "IP-Adresse (für manuelle Eingabe)",
                },
                "field": {"text": {"value": ""}},
            },
        ],
    )


def _show_manual_entry() -> SetupAction:
    """Show manual IP/port entry form."""
    return RequestUserInput(
        {"en": "Manual Device Entry", "de": "Manuelle Geräteeingabe"},
        [
            {
                "id": "manual_address",
                "label": {
                    "en": "IP Address",
                    "de": "IP-Adresse",
                },
                "field": {"text": {"value": ""}},
            },
            {
                "id": "manual_port",
                "label": {
                    "en": "Port",
                    "de": "Port",
                },
                "field": {
                    "number": {
                        "value": DEFAULT_BLUOS_PORT,
                        "min": MIN_PORT,
                        "max": MAX_PORT,
                        "steps": 1,
                    }
                },
            },
        ],
    )


async def _start_discovery() -> SetupAction:
    """Start auto discovery and show results."""
    global _setup_step, _discovered_devices

    _LOG.info("Starting BluOS device discovery...")
    _discovered_devices = await discover_bluos_players(timeout=5.0)

    if not _discovered_devices:
        return RequestUserInput(
            {"en": "No Devices Found", "de": "Keine Geräte gefunden"},
            [
                {
                    "id": "retry",
                    "label": {
                        "en": "No BluOS devices found. Try again?",
                        "de": "Keine BluOS-Geräte gefunden. Erneut versuchen?",
                    },
                    "field": {
                        "dropdown": {
                            "value": "yes",
                            "items": [
                                {
                                    "id": "yes",
                                    "label": {
                                        "en": "Yes, try again",
                                        "de": "Ja, erneut versuchen",
                                    },
                                },
                                {
                                    "id": "manual",
                                    "label": {
                                        "en": "Enter IP manually",
                                        "de": "IP manuell eingeben",
                                    },
                                },
                                {
                                    "id": "no",
                                    "label": {"en": "Cancel", "de": "Abbrechen"},
                                },
                            ],
                        }
                    },
                }
            ],
        )

    _setup_step = SetupSteps.DEVICE_CHOICE
    return _show_device_choice()


async def _handle_discovery(msg: UserDataResponse) -> SetupAction:
    """Handle discovery step responses."""
    global _setup_step, _discovered_devices

    # Check if this is a retry response
    retry = msg.input_values.get("retry")
    if retry:
        if retry == "yes":
            return await _start_discovery()
        elif retry == "manual":
            return _show_manual_entry()
        else:  # "no" - cancel
            return SetupComplete()

    # Check if this is a manual entry response
    manual_address = msg.input_values.get("manual_address", "").strip()
    if manual_address:
        # Validate IP address format
        if not _is_valid_ip_address(manual_address):
            _LOG.warning("Invalid IP address format: %s", manual_address)
            return RequestUserInput(
                {"en": "Invalid IP Address", "de": "Ungültige IP-Adresse"},
                [
                    {
                        "id": "manual_address",
                        "label": {
                            "en": "Please enter a valid IP address",
                            "de": "Bitte geben Sie eine gültige IP-Adresse ein",
                        },
                        "field": {"text": {"value": manual_address}},
                    },
                    {
                        "id": "manual_port",
                        "label": {"en": "Port", "de": "Port"},
                        "field": {
                            "number": {
                                "value": DEFAULT_BLUOS_PORT,
                                "min": MIN_PORT,
                                "max": MAX_PORT,
                                "steps": 1,
                            }
                        },
                    },
                ],
            )

        # Parse and validate port
        try:
            manual_port = int(msg.input_values.get("manual_port", DEFAULT_BLUOS_PORT))
        except (ValueError, TypeError):
            manual_port = DEFAULT_BLUOS_PORT

        if not _is_valid_port(manual_port):
            _LOG.warning("Invalid port: %s", manual_port)
            manual_port = DEFAULT_BLUOS_PORT

        _discovered_devices = [
            DiscoveredDevice(
                host=manual_address,
                port=manual_port,
                name=f"BluOS Player ({manual_address}:{manual_port})",
            )
        ]
        _setup_step = SetupSteps.DEVICE_CHOICE
        return _show_device_choice()

    # Check if this is from discovery options (legacy flow)
    discovery_mode = msg.input_values.get("discovery_mode", "auto")
    if discovery_mode == "manual":
        return _show_manual_entry()

    # Auto discovery
    return await _start_discovery()


def _show_device_choice() -> SetupAction:
    """Show discovered device selection."""
    items = []
    for i, device in enumerate(_discovered_devices):
        label = device.name
        if device.model:
            label = f"{device.name} ({device.model})"
        items.append(
            {
                "id": str(i),
                "label": {"en": f"{label} - {device.host}"},
            }
        )

    return RequestUserInput(
        {"en": "Select Device", "de": "Gerät auswählen"},
        [
            {
                "id": "device",
                "label": {"en": "BluOS Player", "de": "BluOS-Player"},
                "field": {
                    "dropdown": {
                        "value": "0",
                        "items": items,
                    }
                },
            }
        ],
    )


async def _handle_device_choice(msg: UserDataResponse) -> SetupAction:
    """Handle device selection."""
    global _setup_step, _selected_device

    try:
        device_index = int(msg.input_values.get("device", "0"))
    except (ValueError, TypeError):
        return SetupError()

    if 0 <= device_index < len(_discovered_devices):
        _selected_device = _discovered_devices[device_index]
        _setup_step = SetupSteps.DEVICE_CONFIGURE
        return _show_device_configure()
    else:
        return SetupError()


def _show_device_configure() -> SetupAction:
    """Show device configuration options."""
    default_name = _selected_device.name if _selected_device else "BluOS Player"

    return RequestUserInput(
        {"en": "Device Configuration", "de": "Gerätekonfiguration"},
        [
            {
                "id": "name",
                "label": {"en": "Device Name", "de": "Gerätename"},
                "field": {"text": {"value": default_name}},
            },
            {
                "id": "volume_step",
                "label": {
                    "en": "Volume Step (1-10)",
                    "de": "Lautstärkeschritt (1-10)",
                },
                "field": {
                    "number": {
                        "value": 5,
                        "min": 1,
                        "max": 10,
                        "steps": 1,
                    }
                },
            },
            {
                "id": "timeout",
                "label": {
                    "en": "Connection Timeout (seconds)",
                    "de": "Verbindungs-Timeout (Sekunden)",
                },
                "field": {
                    "number": {
                        "value": 5,
                        "min": 1,
                        "max": 30,
                        "steps": 1,
                    }
                },
            },
            {
                "id": "standby_timeout",
                "label": {
                    "en": "Standby Timeout (seconds)",
                    "de": "Standby-Timeout (Sekunden)",
                },
                "field": {
                    "number": {
                        "value": 60,
                        "min": 15,
                        "max": 300,
                        "steps": 15,
                    }
                },
            },
            {
                "id": "active_poll_timeout",
                "label": {
                    "en": "Active Poll Timeout (seconds)",
                    "de": "Aktiv-Poll-Timeout (Sekunden)",
                },
                "field": {
                    "number": {
                        "value": 30,
                        "min": 10,
                        "max": 120,
                        "steps": 5,
                    }
                },
            },
        ],
    )


async def _handle_device_configure(msg: UserDataResponse) -> SetupAction:
    """Handle device configuration."""
    global _setup_step, _selected_device, _configured_device

    if not _selected_device:
        return SetupError()

    name = msg.input_values.get("name", _selected_device.name)
    try:
        volume_step = int(msg.input_values.get("volume_step", 5))
    except (ValueError, TypeError):
        volume_step = 5
    try:
        timeout = float(msg.input_values.get("timeout", 5))
    except (ValueError, TypeError):
        timeout = 5.0
    try:
        standby_timeout = int(msg.input_values.get("standby_timeout", 60))
    except (ValueError, TypeError):
        standby_timeout = 60
    try:
        active_poll_timeout = int(msg.input_values.get("active_poll_timeout", 30))
    except (ValueError, TypeError):
        active_poll_timeout = 30

    # Create device configuration
    # Use MAC if available, otherwise generate from IP
    device_id = _selected_device.mac or _selected_device.host.replace(".", "_")

    _configured_device = BluOSDevice(
        id=device_id,
        name=name,
        address=_selected_device.host,
        port=_selected_device.port,
        volume_step=volume_step,
        timeout=timeout,
        standby_timeout=standby_timeout,
        active_poll_timeout=active_poll_timeout,
        model=_selected_device.model,
    )

    _LOG.info("Device configured: %s (%s)", _configured_device.name, _configured_device.address)

    # Reset state
    _setup_step = SetupSteps.INIT
    _selected_device = None

    return SetupComplete()


def get_setup_data_schema() -> dict[str, Any]:
    """Get the setup data schema for driver.json."""
    return {
        "title": {"en": "BluOS Integration", "de": "BluOS Integration"},
        "settings": [
            {
                "id": "info",
                "label": {"en": "Information", "de": "Information"},
                "field": {
                    "label": {
                        "value": {
                            "en": "This integration allows you to control BluOS-enabled "
                            "streaming players including Bluesound, NAD, and DALI devices."
                            "\n\nClick 'Next' to start device discovery.",
                            "de": "Diese Integration ermöglicht die Steuerung von BluOS-fähigen "
                            "Streaming-Playern einschließlich Bluesound-, NAD- und DALI-Geräten."
                            "\n\nKlicken Sie auf 'Weiter', um die Geräteerkennung zu starten.",
                        }
                    }
                },
            }
        ],
    }
