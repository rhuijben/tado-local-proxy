# Tado Local

**Local REST API for Tado devices via HomeKit** - bypassing cloud rate limits with direct bridge communication.

## Why This Exists

With TADO rate limiting their cloud API (used by [libtado](https://github.com/germainlefebvre4/libtado)), we need an alternate reliable way to access TADO data locally. The recommended approach from Tado is using the HomeKit API, but this isn't easily accessible.

Most solutions require setting up [Home Assistant](https://www.home-assistant.io/) and then bridging to your home automation system (like [Domoticz](http://domoticz.com)) via MQTT. That's **a lot of overhead** just for one appliance.

This proxy provides a lightweight, direct solution using the HomeKit protocol with a clean REST API.

## Features

- **Local Control**: Direct communication with Tado bridge via HomeKit - no cloud required
- **Clean REST API**: Simple HTTP endpoints for easy integration
- **Real-time Events**: Server-Sent Events (SSE) stream for live updates
- **State History**: SQLite-backed storage with 10-second resolution
- **Zone Management**: Organize devices by room/zone
- **Automatic Reconnection**: Handles network interruptions gracefully
- **Change Detection**: Only saves when values actually change
- **Interactive Docs**: Built-in Swagger UI at `/docs`

## Installation

### Quick Start (Recommended)

```bash
# Clone the repository
git clone https://github.com/yourusername/tado-local-proxy.git
cd tado-local-proxy

# Install the package
pip install -e .

# Run from anywhere
python -m tado_local --help
# or
tado-local --help
```

### Development Installation

```bash
# Install dependencies manually
pip install -r requirements.txt

# Run directly (backward compatibility)
python local.py --help
```

## Setup & Usage

### Initial Pairing

**First time only** - requires the HomeKit PIN from your Tado bridge label:

```bash
# Using the package (recommended)
python -m tado_local --bridge-ip 192.168.1.100 --pin 123-45-678

# Or using the console script
tado-local --bridge-ip 192.168.1.100 --pin 123-45-678

# Or using local.py (backward compatibility)
python local.py --bridge-ip 192.168.1.100 --pin 123-45-678
```

This performs initial pairing, stores connection credentials in the database, and starts the API server on port 4407.

### Subsequent Runs

After pairing, just provide the bridge IP:

```bash
python -m tado_local --bridge-ip 192.168.1.100

# Or with custom options
tado-local --bridge-ip 192.168.1.100 --port 8080 --state ./my-tado.db
```

Or if you have only one pairing, it auto-discovers:

```bash
python -m tado_local
```

### Configuration Options

```bash
python -m tado_local --help

Options:
  --state PATH          Path to state database (default: ~/.tado-local.db)
  --bridge-ip IP        IP address of the Tado bridge
  --pin XXX-XX-XXX      HomeKit PIN for initial pairing
  --port PORT           API server port (default: 4407)
  --clear-pairings      Clear all existing pairings before starting
```

## API Endpoints

Once running, access the API at `http://localhost:4407`:

### Main Endpoints

- `GET /` - API information and available endpoints
- `GET /status` - System status, statistics, and health
- `GET /accessories` - All HomeKit accessories (raw data)
- `GET /zones` - All Tado zones with associated devices
- `GET /thermostats` - All thermostats with current readings
- `POST /thermostats/{id}/set_temperature` - Set target temperature
- `GET /events` - Server-Sent Events stream for real-time updates
- `POST /refresh` - Manually refresh data from bridge

### Interactive Documentation

- **Swagger UI**: `http://localhost:4407/docs` - Try out API calls directly
- **ReDoc**: `http://localhost:4407/redoc` - Alternative documentation view

## Usage Examples

### Get Current State

```bash
# All thermostats
curl http://localhost:4407/thermostats

# Specific zones
curl http://localhost:4407/zones

# System status
curl http://localhost:4407/status
```

### Control Temperature

```bash
curl -X POST http://localhost:4407/thermostats/1/set_temperature \
  -H "Content-Type: application/json" \
  -d '{"temperature": 21.5}'
```

### Monitor Real-Time Events

```bash
# Stream live updates
curl -N http://localhost:4407/events
```

### Python Integration

```python
import requests

# Get all thermostats
response = requests.get("http://localhost:4407/thermostats")
thermostats = response.json()

for device in thermostats:
    print(f"{device['zone']}: {device['current_temperature']}°C → {device['target_temperature']}°C")

# Set temperature
requests.post(
    "http://localhost:4407/thermostats/1/set_temperature",
    json={"temperature": 22.0}
)
```

## Important Limitation

### Single homekit connection
One limitation of this, is that the TADO internet bridge currenly allows only a single homekit connection. So to use this proxy you will need to give access to this connection. Eventually we may be able to resolve this limitation by exposing the proxy as its own homekit device. But for now that is out of my scope. (PRs very welcome ;-))

Things are currently in the very early stages of development. I'm able to connect to the bridge and expose the current data

## Important Limitation

### Single HomeKit Connection

The Tado Internet Bridge currently allows **only ONE HomeKit connection at a time**. To use this proxy, you must:

1. Remove any existing HomeKit pairings (iPhone Home app, Home Assistant, etc.)
2. **OR** Reset the HomeKit configuration on the bridge

### Resetting HomeKit Pairing

On a Tado V3+ Internet Bridge:
1. Press and hold the small reset button on the back for **10+ seconds**
2. The LED will blink to confirm reset
3. You can now pair with this proxy

See [Tado's official guide](https://support.tado.com/en/articles/3387334-how-can-i-reset-the-homekit-configuration-of-the-internet-bridge) for more details.

## Troubleshooting

### Pairing Issues

**"Device reports 'Unavailable' for pairing"**

This means the bridge is already paired to another controller. Solutions:

1. Check if paired to:
   - iPhone/iPad Home app
   - Home Assistant
   - Other HomeKit applications

2. Remove the pairing from the other controller **OR** reset the bridge (see above)

3. Verify you're using the correct PIN from the bridge label

### Connection Issues

If the connection drops (power cycle, network issue):

- The proxy automatically reconnects
- Temporary `None` values are ignored to prevent false state updates
- Events automatically restore correct state when connection returns

### Database Issues

To start fresh:

```bash
# Remove old database
rm ~/.tado-local.db

# Re-pair
python proxy.py --bridge-ip 192.168.1.100 --pin 123-45-678
```

## Architecture

### Technology Stack

- **aiohomekit**: HomeKit protocol implementation
- **FastAPI**: Modern REST API framework
- **SQLite**: Persistent state and history storage
- **uvicorn**: ASGI web server
- **Python 3.9+**: Async/await for efficient I/O

### Database Schema

SQLite database stores:
- **HomeKit pairings**: Encrypted credentials for bridge authentication  
- **Controller identity**: Persistent pairing identity across restarts
- **Devices & zones**: Device registry with zone organization
- **State history**: Time-series data with 10-second bucketing
- **Characteristic cache**: HomeKit accessory metadata

### State Management

- **Event-driven updates**: Real-time notifications from HomeKit bridge
- **Dual-speed polling**:
  - Fast (60s): Humidity, battery level (characteristics that change slowly)
  - Slow (120s): Temperature, valve position (polled less frequently)
- **Change detection**: Only saves to database when values actually change
- **Network resilience**: Ignores `None` values from connection issues; events restore state

### HomeKit Integration Features

- **Persistent pairing**: Controller identity preserved across restarts
- **Session resumption**: Can resume failed pairing attempts from Part 1
- **Auto-reconnection**: Handles network interruptions transparently
- **Full characteristic support**: Temperature, humidity, battery, valve position, heating state, etc.
- **UUID mapping**: Human-readable names for HomeKit characteristics

## Development

### Project Structure

```
tado-local-proxy/
├── local.py             # Backward compatibility entry point
├── homekit_uuids.py     # HomeKit UUID to name mappings (legacy)
├── requirements.txt     # Python dependencies
├── setup.py            # Package configuration
├── README.md           # This file
└── tado_local/         # Main Python package
    ├── __init__.py     # Package initialization
    ├── __main__.py     # CLI entry point (python -m tado_local)
    ├── api.py          # TadoLocalAPI class
    ├── routes.py       # All FastAPI route handlers
    ├── bridge.py       # HomeKit bridge pairing
    ├── state.py        # Device state management
    ├── cache.py        # Characteristic cache
    ├── database.py     # Database schema definitions
    └── homekit_uuids.py # UUID mappings
```

### Running in Development

```bash
# Install in editable mode
pip install -e .

# Run with logging
python -m tado_local --bridge-ip 192.168.1.100 2>&1 | tee tado.log

# Or use backward compatibility mode
python local.py --bridge-ip 192.168.1.100

# Monitor database
sqlite3 ~/.tado-local.db
sqlite> .tables
sqlite> SELECT * FROM devices;
sqlite> SELECT * FROM zones;
```

### Testing

```bash
# Test connection
curl http://localhost:4407/status

# Test event stream
curl -N http://localhost:4407/events

# Test temperature change
curl -X POST http://localhost:4407/thermostats/1/set_temperature \
  -H "Content-Type: application/json" \
  -d '{"temperature": 20.0}'
```

## Contributing

Contributions welcome! The project is actively developed.

### Ideas

- [x] Complete package refactoring (all code moved to tado_local package)
- [x] Modular structure (api.py, routes.py, bridge.py, state.py, cache.py, database.py)
- [x] Python -m execution support
- [ ] Comprehensive test suite
- [ ] Docker container & docker-compose
- [ ] Home Assistant HACS integration
- [ ] Web UI for configuration and monitoring
- [ ] Advanced scheduling/automation features
- [ ] Multi-bridge support

### Development Setup

```bash
git clone https://github.com/yourusername/tado-local-proxy.git
cd tado-local-proxy
pip install -e .[dev]  # Development dependencies
```

## License

MIT License - see LICENSE file for details.

## Acknowledgments

- Built on [aiohomekit](https://github.com/Jc2k/aiohomekit) for HomeKit protocol
- Inspired by Domoticz and Home Assistant's local integrations
- Thanks to the Tado and HomeKit communities for reverse engineering efforts

## Support & Community

- **Issues**: [GitHub Issues](https://github.com/yourusername/tado-local-proxy/issues)
- **Discussions**: [GitHub Discussions](https://github.com/yourusername/tado-local-proxy/discussions)
- **Documentation**: Visit `/docs` endpoint when proxy is running

## Status

**Status**: Beta - Active development, production-ready for personal use

The proxy successfully manages:
- ✅ Temperature control
- ✅ Humidity monitoring  
- ✅ Battery status
- ✅ Valve positions
- ✅ Zone management
- ✅ Real-time event streams
- ✅ State persistence
- ✅ Network resilience

Known limitations:
- Single HomeKit connection (bridge hardware limitation)
- Package refactoring in progress
- Documentation being expanded

