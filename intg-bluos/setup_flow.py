"""
Setup flow for BluOS integration.

Reconfiguration follows the convention shared by the official Unfolded Circle
integrations and ``ucapi-framework``: a configuration-mode screen with a
``choice`` dropdown (configured devices) and an ``action`` dropdown (add,
configure, remove, reset, backup, restore).

Backup/restore uses the field ids the community Integration Manager
(JackJPowell/uc-intg-manager) automates: it starts a reconfigure, reads the
``choice`` dropdown value, submits ``{"choice": ..., "action": "backup"}`` in a
single step, reads the ``backup_data`` textarea and then aborts the setup. The
backup screen must therefore never modify the configuration. Restore reads the
``restore_data`` textarea (ucapi-framework naming).
"""

import ipaddress
import logging
from enum import IntEnum
from typing import Any

from config import BluOSDevice
from discover import DiscoveredDevice, discover_bluos_players
from i18n import _a, _am
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

# Device configuration defaults (mirrors BluOSDevice)
DEFAULT_VOLUME_STEP = 5
DEFAULT_TIMEOUT = 5.0
DEFAULT_STANDBY_TIMEOUT = 60
DEFAULT_ACTIVE_POLL_TIMEOUT = 30

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
    BACKUP = 5
    RESTORE = 6


# Global setup state
_setup_step = SetupSteps.INIT
_discovered_devices: list[DiscoveredDevice] = []
_selected_device: DiscoveredDevice | None = None
# Existing device being edited via the "configure" action (None when adding).
_editing_device: BluOSDevice | None = None
_configured_device: BluOSDevice | None = None
_devices_ref = None  # set by driver.py via set_devices()


def set_devices(devices) -> None:
    """Set the Devices instance for configure/remove/backup/restore access."""
    global _devices_ref
    _devices_ref = devices


def get_configured_device() -> BluOSDevice | None:
    """Get the device configured during setup and clear it."""
    global _configured_device
    device = _configured_device
    _configured_device = None
    return device


def _reset_state() -> None:
    global _setup_step, _discovered_devices, _selected_device, _editing_device
    _setup_step = SetupSteps.INIT
    _discovered_devices = []
    _selected_device = None
    _editing_device = None


async def driver_setup_handler(msg: SetupDriver) -> SetupAction:
    """
    Handle setup driver messages.

    Args:
        msg: Setup message from UC Remote

    Returns:
        Setup action response
    """
    if isinstance(msg, DriverSetupRequest):
        _reset_state()
        return await _handle_setup_request(msg)

    if isinstance(msg, UserDataResponse):
        return await _handle_user_data(msg)

    if isinstance(msg, AbortDriverSetup):
        _LOG.info("Setup aborted")
        _reset_state()
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
    match _setup_step:
        case SetupSteps.CONFIGURATION_MODE:
            return await _handle_configuration_mode(msg)
        case SetupSteps.DISCOVER:
            return await _handle_discovery(msg)
        case SetupSteps.DEVICE_CHOICE:
            return await _handle_device_choice(msg)
        case SetupSteps.DEVICE_CONFIGURE:
            return await _handle_device_configure(msg)
        case SetupSteps.BACKUP:
            # The user has seen (and copied) the backup; nothing to change.
            _reset_state()
            return SetupComplete()
        case SetupSteps.RESTORE:
            return await _handle_restore(msg)
        case _:
            _LOG.error("Unexpected setup step: %s", _setup_step)
            return SetupError()


# ---------------------------------------------------------------------------
# Configuration mode (reconfigure)
# ---------------------------------------------------------------------------


def _configured_devices() -> list[BluOSDevice]:
    return _devices_ref.all() if _devices_ref is not None else []


def _show_configuration_mode() -> SetupAction:
    """Show the configured-device list and the available actions."""
    devices = _configured_devices()

    device_items = [{"id": d.id, "label": {"en": f"{d.name} ({d.address})"}} for d in devices]
    actions = [{"id": "add", "label": _a("Add a new device")}]
    if devices:
        actions += [
            {"id": "configure", "label": _a("Configure selected device")},
            {"id": "remove", "label": _a("Remove selected device")},
            {"id": "reset", "label": _a("Reset configuration")},
            {"id": "backup", "label": _a("Backup configuration")},
        ]
    else:
        # Dummy entry: the dropdown must not be empty.
        device_items.append({"id": "", "label": {"en": "---"}})
    actions.append({"id": "restore", "label": _a("Restore configuration from backup")})

    return RequestUserInput(
        _a("BluOS Configuration"),
        [
            {
                "id": "choice",
                "label": _a("Configured devices"),
                "field": {"dropdown": {"value": device_items[0]["id"], "items": device_items}},
            },
            {
                "id": "action",
                "label": _a("Action"),
                "field": {"dropdown": {"value": actions[0]["id"], "items": actions}},
            },
        ],
    )


async def _handle_configuration_mode(msg: UserDataResponse) -> SetupAction:
    """Handle configuration mode selection."""
    global _setup_step, _editing_device

    action = msg.input_values.get("action", "add")
    choice = msg.input_values.get("choice", "")

    match action:
        case "add":
            _setup_step = SetupSteps.DISCOVER
            return _show_discovery_options()

        case "configure":
            device = _devices_ref.get(choice) if _devices_ref is not None and choice else None
            if device is None:
                _LOG.warning("Configure: device '%s' not found", choice)
                return SetupError()
            _editing_device = device
            _setup_step = SetupSteps.DEVICE_CONFIGURE
            return _show_device_configure()

        case "remove":
            if _devices_ref is None or not choice or not _devices_ref.remove(choice):
                _LOG.warning("Remove: device '%s' not found", choice)
                return SetupError()
            _LOG.info("Device removed via setup: %s", choice)
            _reset_state()
            return SetupComplete()

        case "reset":
            if _devices_ref is not None:
                _devices_ref.clear()
            _reset_state()
            return SetupComplete()

        case "backup":
            _setup_step = SetupSteps.BACKUP
            return _show_backup()

        case "restore":
            _setup_step = SetupSteps.RESTORE
            return _show_restore()

    _LOG.warning("Unknown configuration action: %s", action)
    return SetupError()


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------


def _show_backup() -> SetupAction:
    """Show the current configuration as JSON in the ``backup_data`` textarea."""
    current = _devices_ref.export() if _devices_ref is not None else '{"devices": []}'
    return RequestUserInput(
        _a("Backup configuration"),
        [
            {
                "id": "info",
                "label": _a("Backup"),
                "field": {
                    "label": {
                        "value": _a(
                            "Copy the configuration below and keep it somewhere safe. "
                            "Use 'Restore configuration from backup' to load it again."
                        )
                    }
                },
            },
            {
                "id": "backup_data",
                "label": _a("Configuration"),
                "field": {"textarea": {"value": current}},
            },
        ],
    )


def _show_restore(error: bool = False) -> SetupAction:
    """Show the ``restore_data`` textarea, optionally with a parse error notice."""
    settings: list[dict[str, Any]] = []
    if error:
        settings.append(
            {
                "id": "error",
                "label": _a("Error"),
                "field": {"label": {"value": _a("The configuration could not be read. Please paste a valid backup.")}},
            }
        )
    settings += [
        {
            "id": "info",
            "label": _a("Restore"),
            "field": {
                "label": {"value": _a("Paste a previously saved backup. All configured devices will be replaced.")}
            },
        },
        {
            "id": "restore_data",
            "label": _a("Configuration"),
            "field": {"textarea": {"value": ""}},
        },
    ]
    return RequestUserInput(_a("Restore configuration"), settings)


async def _handle_restore(msg: UserDataResponse) -> SetupAction:
    """Validate and apply a pasted backup."""
    if _devices_ref is None:
        _LOG.error("Devices not initialised during restore")
        return SetupError()

    json_str = (msg.input_values.get("restore_data") or "").strip()
    if not json_str or not _devices_ref.import_config(json_str):
        _LOG.warning("Restore failed: invalid configuration data")
        return _show_restore(error=True)

    _LOG.info("Configuration restored successfully")
    _reset_state()
    return SetupComplete()


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _manual_port_field(value: int = DEFAULT_BLUOS_PORT) -> dict[str, Any]:
    return {
        "id": "manual_port",
        "label": _a("Port"),
        "field": {"number": {"value": value, "min": MIN_PORT, "max": MAX_PORT, "steps": 1}},
    }


def _show_discovery_options() -> SetupAction:
    """Show discovery options."""
    return RequestUserInput(
        _a("Device Discovery"),
        [
            {
                "id": "discovery_mode",
                "label": _a("Discovery Method"),
                "field": {
                    "dropdown": {
                        "value": "auto",
                        "items": [
                            {"id": "auto", "label": _a("Auto-discover (recommended)")},
                            {"id": "manual", "label": _a("Manual IP entry")},
                        ],
                    }
                },
            },
            {
                "id": "manual_address",
                "label": _a("IP Address (for manual entry)"),
                "field": {"text": {"value": ""}},
            },
        ],
    )


def _show_manual_entry() -> SetupAction:
    """Show manual IP/port entry form."""
    return RequestUserInput(
        _a("Manual Device Entry"),
        [
            {
                "id": "manual_address",
                "label": _a("IP Address"),
                "field": {"text": {"value": ""}},
            },
            _manual_port_field(),
        ],
    )


async def _start_discovery() -> SetupAction:
    """Start auto discovery and show results."""
    global _setup_step, _discovered_devices

    _LOG.info("Starting BluOS device discovery...")
    _discovered_devices = await discover_bluos_players(timeout=5.0)

    if not _discovered_devices:
        return RequestUserInput(
            _a("No Devices Found"),
            [
                {
                    "id": "retry",
                    "label": _a("No BluOS devices found. Try again?"),
                    "field": {
                        "dropdown": {
                            "value": "yes",
                            "items": [
                                {"id": "yes", "label": _a("Yes, try again")},
                                {"id": "manual", "label": _a("Enter IP manually")},
                                {"id": "no", "label": _a("Cancel")},
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
        if retry == "manual":
            return _show_manual_entry()
        if retry == "no":
            return SetupComplete()
        _LOG.warning("Unexpected retry value: %s", retry)
        return SetupError()

    # Check if this is a manual entry response
    manual_address = msg.input_values.get("manual_address", "").strip()
    if manual_address:
        # Validate IP address format
        if not _is_valid_ip_address(manual_address):
            _LOG.warning("Invalid IP address format: %s", manual_address)
            return RequestUserInput(
                _a("Invalid IP Address"),
                [
                    {
                        "id": "manual_address",
                        "label": _a("Please enter a valid IP address"),
                        "field": {"text": {"value": manual_address}},
                    },
                    _manual_port_field(),
                ],
            )

        manual_port = _parse_port(msg.input_values.get("manual_port"))

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


def _parse_port(value: Any) -> int:
    """Parse a port input, falling back to the BluOS default when invalid."""
    try:
        port = int(value if value is not None else DEFAULT_BLUOS_PORT)
    except (ValueError, TypeError):
        return DEFAULT_BLUOS_PORT
    if not _is_valid_port(port):
        _LOG.warning("Invalid port: %s", port)
        return DEFAULT_BLUOS_PORT
    return port


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
        _a("Select Device"),
        [
            {
                "id": "device",
                "label": _a("BluOS Player"),
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
    return SetupError()


# ---------------------------------------------------------------------------
# Device settings (new device or "configure" of an existing one)
# ---------------------------------------------------------------------------


def _number_field(field_id: str, label: str, value: float, *, low: int, high: int, step: int) -> dict[str, Any]:
    return {
        "id": field_id,
        "label": _a(label),
        "field": {"number": {"value": value, "min": low, "max": high, "steps": step}},
    }


def _show_device_configure(address_error: bool = False) -> SetupAction:
    """
    Show device settings.

    For a new device the values are defaults; when editing an existing device
    they are pre-filled and the address/port can be changed too (e.g. after the
    player got a new IP address).
    """
    editing = _editing_device
    if editing is not None:
        name = editing.name
        volume_step = editing.volume_step
        timeout = editing.timeout
        standby_timeout = editing.standby_timeout
        active_poll_timeout = editing.active_poll_timeout
    else:
        name = _selected_device.name if _selected_device else "BluOS Player"
        volume_step = DEFAULT_VOLUME_STEP
        timeout = DEFAULT_TIMEOUT
        standby_timeout = DEFAULT_STANDBY_TIMEOUT
        active_poll_timeout = DEFAULT_ACTIVE_POLL_TIMEOUT

    settings: list[dict[str, Any]] = []
    if address_error:
        settings.append(
            {
                "id": "error",
                "label": _a("Error"),
                "field": {"label": {"value": _a("Please enter a valid IP address")}},
            }
        )
    settings.append({"id": "name", "label": _a("Device Name"), "field": {"text": {"value": name}}})
    if editing is not None:
        settings.append({"id": "address", "label": _a("IP Address"), "field": {"text": {"value": editing.address}}})
        port_field = _manual_port_field(editing.port)
        port_field["id"] = "port"
        settings.append(port_field)
    settings += [
        _number_field("volume_step", "Volume Step (1-10)", volume_step, low=1, high=10, step=1),
        _number_field("timeout", "Connection Timeout (seconds)", timeout, low=1, high=30, step=1),
        _number_field("standby_timeout", "Standby Timeout (seconds)", standby_timeout, low=15, high=300, step=15),
        _number_field(
            "active_poll_timeout", "Active Poll Timeout (seconds)", active_poll_timeout, low=10, high=120, step=5
        ),
    ]

    title = _a("Configure Device") if editing is not None else _a("Device Configuration")
    return RequestUserInput(title, settings)


def _int_input(msg: UserDataResponse, key: str, default: int) -> int:
    try:
        return int(msg.input_values.get(key, default))
    except (ValueError, TypeError):
        return default


async def _handle_device_configure(msg: UserDataResponse) -> SetupAction:
    """Handle device settings for a new or an edited device."""
    global _configured_device

    editing = _editing_device
    if editing is None and not _selected_device:
        return SetupError()

    try:
        timeout = float(msg.input_values.get("timeout", DEFAULT_TIMEOUT))
    except (ValueError, TypeError):
        timeout = DEFAULT_TIMEOUT
    volume_step = _int_input(msg, "volume_step", DEFAULT_VOLUME_STEP)
    standby_timeout = _int_input(msg, "standby_timeout", DEFAULT_STANDBY_TIMEOUT)
    active_poll_timeout = _int_input(msg, "active_poll_timeout", DEFAULT_ACTIVE_POLL_TIMEOUT)

    if editing is not None:
        address = (msg.input_values.get("address") or editing.address).strip()
        if not _is_valid_ip_address(address):
            _LOG.warning("Invalid IP address format: %s", address)
            return _show_device_configure(address_error=True)
        port = _parse_port(msg.input_values.get("port", editing.port))
        # Keep the id so entity ids — and the user's activities — stay intact.
        _configured_device = BluOSDevice(
            id=editing.id,
            name=msg.input_values.get("name") or editing.name,
            address=address,
            port=port,
            volume_step=volume_step,
            timeout=timeout,
            standby_timeout=standby_timeout,
            active_poll_timeout=active_poll_timeout,
            model=editing.model,
        )
    else:
        assert _selected_device is not None
        # Use MAC if available, otherwise generate from IP
        device_id = _selected_device.mac or _selected_device.host.replace(".", "_")
        _configured_device = BluOSDevice(
            id=device_id,
            name=msg.input_values.get("name", _selected_device.name),
            address=_selected_device.host,
            port=_selected_device.port,
            volume_step=volume_step,
            timeout=timeout,
            standby_timeout=standby_timeout,
            active_poll_timeout=active_poll_timeout,
            model=_selected_device.model,
        )

    _LOG.info("Device configured: %s (%s)", _configured_device.name, _configured_device.address)
    _reset_state()
    return SetupComplete()


def get_setup_data_schema() -> dict[str, Any]:
    """
    Get the initial setup page schema.

    driver.json holds a static copy (it cannot load translations); keep the
    texts in sync when changing them here.
    """
    return {
        "title": _a("BluOS Integration"),
        "settings": [
            {
                "id": "info",
                "label": _a("Information"),
                "field": {
                    "label": {
                        "value": _am(
                            "This integration allows you to control BluOS-enabled "
                            "streaming players including Bluesound, NAD, and DALI devices.",
                            "\n\n",
                            "Click 'Next' to start device discovery.",
                        )
                    }
                },
            }
        ],
    }
