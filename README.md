# BluOS Integration for Unfolded Circle Remote

A custom integration for the [Unfolded Circle Remote](https://www.unfoldedcircle.com/) that enables control of BluOS-enabled streaming players including Bluesound, NAD, and DALI devices.

## Features

- **Media Player Control**: Play, pause, stop, next/previous track, seek
- **Volume Control**: Set volume, volume up/down, mute/unmute
- **Source Selection**: Switch between inputs and presets
- **Shuffle & Repeat**: Toggle shuffle mode, cycle through repeat modes (off/all/one)
- **Sleep Timer**: Toggle through preset sleep timer values (15/30/45/60/90 minutes)
- **Preset Management**: Quick access to saved presets via simple commands
- **Multi-room Support**: Group/ungroup players (API available)
- **Auto-discovery**: Automatic discovery of BluOS devices on the local network via mDNS
- **Fast Status Updates**: Long-polling for real-time playback state updates

## Installation

### From Release Package

1. Download the latest release package from the releases page
2. Upload to your Unfolded Circle Remote via the web interface
3. Configure devices through the setup flow

### From Source (Development)

```bash
# Clone the repository
git clone https://github.com/your-repo/uc-integration-bluos.git
cd uc-integration-bluos

# Install dependencies (using devenv or pip)
pip install -e ".[test]"

# Run the integration
python intg-bluos/driver.py
```

## Configuration

### Device Configuration

Devices can be configured via:
1. **Auto-discovery**: The integration will automatically discover BluOS devices on your network
2. **Manual entry**: Enter the IP address of your BluOS device manually

### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `address` | - | IP address of the BluOS device |
| `port` | 11000 | Port number (default BluOS API port) |
| `volume_step` | 5 | Volume increment/decrement step (1-20) |
| `timeout` | 5.0 | Connection timeout in seconds |
| `standby_timeout` | 60 | Long-poll timeout during standby |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `UC_CONFIG_HOME` | `./data` | Directory for configuration storage |
| `UC_LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |

## Supported Devices

This integration supports any BluOS-enabled device, including:

- **Bluesound**: NODE, POWERNODE, PULSE, VAULT
- **NAD**: M10, M33, C 658, C 700, T 758 V3i
- **DALI**: CALLISTO, SOUND HUB, OBERON C
- Other BluOS-enabled devices

## Simple Commands

The integration exposes several simple commands for use with macros:

| Command | Description |
|---------|-------------|
| `PRESET_1`, `PRESET_2`, ... | Load preset by number |
| `REFRESH_PRESETS` | Refresh the list of available presets |
| `SHUFFLE_TOGGLE` | Toggle shuffle mode |
| `REPEAT_TOGGLE` | Cycle repeat mode (OFF -> ALL -> ONE -> OFF) |
| `SLEEP_TIMER` | Toggle sleep timer (15 -> 30 -> 45 -> 60 -> 90 -> OFF) |

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=intg-bluos

# Run specific test file
pytest tests/test_bluos.py
```

### Code Style

The project uses:
- **Black** for code formatting (line length: 120)
- **isort** for import sorting
- **Pylint** for linting

```bash
# Format code
black intg-bluos tests
isort intg-bluos tests
```

### Building

```bash
# Build with PyInstaller
pyinstaller --onefile intg-bluos/driver.py
```

## Architecture

```
intg-bluos/
├── driver.py          # Main entry point and UC API integration
├── bluos.py           # BluOS player wrapper with event emission
├── media_player.py    # UC media player entity implementation
├── select_entity.py   # UC select entity for preset selection
├── config.py          # Device configuration management
├── discover.py        # mDNS discovery for BluOS devices
└── setup_flow.py      # Device setup flow handler
```

## Troubleshooting

### Device Not Discovered

- Ensure your BluOS device and Remote are on the same network
- Check that mDNS/Bonjour is not blocked by your router
- Try manual IP entry as a workaround

### Connection Issues

- Verify the device is powered on and connected to the network
- Check the device's IP address hasn't changed
- Increase the timeout value if you have a slow network

### Playback State Not Updating

- The integration uses long-polling; updates should appear within seconds
- Check the UC_LOG_LEVEL for error messages
- Restart the integration if issues persist

## License

This project is licensed under the Mozilla Public License 2.0 (MPL-2.0).

## Acknowledgments

- [pyblu](https://github.com/LouisChrist/pyblu) - Python library for BluOS API
- [Unfolded Circle](https://www.unfoldedcircle.com/) - For the Remote and integration SDK
