# Tado Local

**Control your Tado smart thermostats locally** - No cloud dependencies, no rate limits, instant response times.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

## 🎯 What is Tado Local?

Tado Local is a lightweight bridge that connects your Tado smart heating system directly to your home automation setup. It provides:

- **🏠 Web UI** - Control interface for managing zones and viewing history
- **📊 Visual History** - Charts showing temperature, humidity, and heating patterns over time
- **⚡ REST API** - Integration endpoint for any smart home platform (Domoticz, Home Assistant, openHAB, etc.)
- **🔄 Real-time Updates** - Server-Sent Events for instant state changes
- **💾 Local Storage** - SQLite database keeping all data on your network

### Why Choose Local Control?

Tado's cloud API has strict rate limits that can break home automation integrations. Traditional solutions require running full Home Assistant instances just to access one device. 

**Tado Local changes that.** Using the HomeKit protocol built into your Tado bridge, this tool provides fast, reliable, local access with a simple REST API that works with any platform.

---

## 📸 See It In Action

### Web Interface - Zone Overview
![Zone Overview](docs/images/zone-overview.png)
*All zones with real-time temperature, humidity, and status.*

### Web Interface - Zone Control
![Zone Control](docs/images/zone-control.png)
*Temperature adjustment and heating mode controls.*

### Web Interface - Visual History
![Visual History](docs/images/history-chart.png)
*Temperature, heating activity, and humidity tracking with interactive charts.*

---

---

## ⚠️ Alpha Status

**This project is currently in active alpha development.** Core features are working and stable, but expect:
- Occasional API changes as we refine the interface
- Some rough edges in the UI
- Ongoing improvements based on community feedback

**We welcome testers!** Please report any issues you encounter on [GitHub Issues](https://github.com/ampscm/TadoLocal/issues).

---

## ✨ Features

- **Local Control**: Direct communication with Tado bridge via HomeKit - no cloud required
- **🎨 Web UI**: HTML interface for controlling heating zones
- **📈 Visual History**: Interactive charts showing temperature, humidity, and heating patterns
- **Clean REST API**: Simple HTTP endpoints for easy integration
- **Real-time Events**: Server-Sent Events (SSE) stream for live updates
- **State History**: SQLite-backed storage with 10-second resolution
- **Zone Management**: Organize devices by room/zone
- **Automatic Reconnection**: Handles network interruptions gracefully
- **Interactive API Docs**: Built-in Swagger UI at `/docs`

---

## 🚀 Quick Start

### For Casual Users

**Step 1: Install Python**

If you don't have Python 3.11 or newer, download it from [python.org](https://www.python.org/downloads/).

**Step 2: Install Tado Local**

```bash
# Download the project
git clone https://github.com/ampscm/TadoLocal.git
cd TadoLocal

# Install
pip install -e .
```

**Step 3: Find Your Bridge Information**

You'll need:
- **Bridge IP Address**: Check your router's connected devices list for "Tado"
- **HomeKit PIN**: Found on a sticker on your Tado bridge (format: XXX-XX-XXX)

**Step 4: Start Tado Local**

```bash
# First time setup
tado-local --bridge-ip 192.168.1.100 --pin 123-45-678
```

**Step 5: Complete Cloud Authentication**

1. Open your browser to `http://localhost:4407`
2. Check the **Cloud** status in the web interface
3. If authentication is needed, click the "Authenticate" link
4. Log in with your Tado account credentials
5. Done! The interface will update automatically

**Step 6: Use It!**

- **Web Interface**: Visit `http://localhost:4407`
- **API Documentation**: Visit `http://localhost:4407/docs`

**Next Time**: Just run `tado-local` - no arguments needed!

---

## 🏗️ For Developers & Integrators

### REST API Integration

Tado Local provides a comprehensive REST API for integrating with any smart home platform.

**Base URL**: `http://localhost:4407`

**Key Endpoints**:

```bash
# Get all zones with current state
GET /zones

# Get all thermostats
GET /thermostats

# Get history for a zone (last 24 hours by default)
GET /zones/{zone_id}/history?start_time={unix_timestamp}&limit=1000

# Set target temperature
POST /thermostats/{thermostat_id}/set_temperature
Content-Type: application/json
{"temperature": 21.5}

# Real-time event stream
GET /events
```

**Full API Documentation**: `http://localhost:4407/docs` (interactive Swagger UI)

### Domoticz Plugin Installation

**Good news - we have a native Domoticz plugin!** It's included in the `domoticz/` directory of this repository.

#### Installation Steps:

1. **Copy the plugin to Domoticz**:
   ```bash
   cd /path/to/domoticz/plugins
   mkdir TadoLocal
   cp /path/to/tado-local/domoticz/plugin.py TadoLocal/
   ```

2. **Restart Domoticz**:
   ```bash
   sudo systemctl restart domoticz
   ```

3. **Configure in Domoticz Web UI**:
   - Navigate to: **Setup → Hardware**
   - Click **Add** and select **Tado Local** from the Type dropdown
   - Enter your Tado Local API URL (e.g., `http://localhost:4407`)
   - Set retry interval (default: 30 seconds)
   - Click **Add**

4. **Devices auto-created!**
   - The plugin automatically discovers all your zones
   - Each zone appears as a Temperature + Humidity device
   - Real-time updates via Server-Sent Events (SSE)

#### Features:
- ✅ Automatic zone discovery
- ✅ Real-time temperature and humidity updates
- ✅ Thermostat control (set temperature, on/off)
- ✅ Auto-reconnect on connection loss
- ✅ Detailed debug logging

**Full documentation**: See [`domoticz/README.md`](domoticz/README.md) for troubleshooting and advanced configuration.

### Home Assistant Integration

You can integrate Tado Local with Home Assistant using the REST integration:

```yaml
# configuration.yaml

sensor:
  - platform: rest
    name: "Living Room Temperature"
    resource: "http://localhost:4407/zones/1"
    value_template: "{{ value_json.current_temperature }}"
    unit_of_measurement: "°C"
    
climate:
  - platform: generic_thermostat
    name: "Living Room Heating"
    # Configure with REST commands
```

**Note**: A native Home Assistant integration is on our roadmap!

### Other Platforms

The REST API works with any platform that supports HTTP requests:
- **openHAB**: Use HTTP binding
- **Node-RED**: HTTP request nodes
- **HomeBridge**: HTTP webhooks
- **Custom Scripts**: Python, JavaScript, bash + curl

---

## 📋 Installation & Configuration

### Prerequisites

### Prerequisites

- **Python 3.11 or newer** ([Download](https://www.python.org/downloads/))
- **Tado Internet Bridge** with HomeKit support (V3+ models)
- **Network access** to your Tado bridge

### Installation Steps

```bash
# Clone the repository
git clone https://github.com/ampscm/TadoLocal.git
cd TadoLocal

# Install the package
pip install -e .

# Verify installation
tado-local --help
```

### First-Time Setup

1. **Reset HomeKit pairing** on your bridge (see [Important Limitation](#-important-limitation) below)

2. **Start with your bridge credentials**:
   ```bash
   tado-local --bridge-ip 192.168.1.100 --pin 123-45-678
   ```

3. **Complete cloud authentication** in your browser (for device metadata)

4. **Done!** Future runs only need:
   ```bash
   tado-local
   ```

### Configuration Options

### Configuration Options

```bash
tado-local --help

Options:
  --state PATH          Path to state database (default: ~/.tado-local.db)
  --bridge-ip IP        IP address of the Tado bridge
  --pin XXX-XX-XXX      HomeKit PIN for initial pairing
  --port PORT           API server port (default: 4407)
  --clear-pairings      Clear all existing pairings before starting
```

---

## 🔌 Using the Web Interface

Once Tado Local is running, open your browser to `http://localhost:4407`

### Main Features:

- **📊 Zone Dashboard**: See all your rooms with current temperature, humidity, and heating status
- **🎛️ Zone Controls**: Click any zone to adjust target temperature and heating mode
- **📈 History Charts**: View temperature trends, humidity, and heating activity over time (24h, 7d, 30d, 1y)
- **☁️ Status Bar**: Monitor connection status to both the bridge and cloud API
- **🔄 Real-time Updates**: The interface updates automatically as temperatures change

---

## 🛠️ API Usage Examples

### Get Current State

```bash
# All thermostats
curl http://localhost:4407/thermostats

# Specific zone
curl http://localhost:4407/zones/1

# System status
curl http://localhost:4407/status
```

### Control Temperature

```bash
curl -X POST http://localhost:4407/thermostats/1/set_temperature \
  -H "Content-Type: application/json" \
  -d '{"temperature": 21.5}'
```

### Get History Data

```bash
# Last 24 hours
curl http://localhost:4407/zones/1/history

# Custom time range (Unix timestamps)
curl "http://localhost:4407/zones/1/history?start_time=1699000000&end_time=1699086400"

# With pagination
curl "http://localhost:4407/zones/1/history?limit=500&offset=500"
```

### Monitor Real-Time Events

```bash
# Stream live updates (Server-Sent Events)
curl -N http://localhost:4407/events
```

### Python Integration Example

```python
import requests
from datetime import datetime, timedelta

TADO_API = "http://localhost:4407"

# Get all zones
zones = requests.get(f"{TADO_API}/zones").json()

for zone in zones:
    print(f"{zone['name']}: {zone['current_temperature']}°C")
    print(f"  Target: {zone['target_temperature']}°C")
    print(f"  Humidity: {zone['humidity']}%")
    print(f"  Heating: {'ON' if zone['heating_active'] else 'OFF'}")
    print()

# Set temperature for a specific thermostat
requests.post(
    f"{TADO_API}/thermostats/1/set_temperature",
    json={"temperature": 22.0}
)

# Get history for the last 7 days
end_time = int(datetime.now().timestamp())
start_time = int((datetime.now() - timedelta(days=7)).timestamp())

history = requests.get(
    f"{TADO_API}/zones/1/history",
    params={"start_time": start_time, "end_time": end_time, "limit": 1000}
).json()

for record in history['history']:
    print(f"{record['timestamp']}: {record['state']['cur_temp_c']}°C")
```

---

## ⚠️ Important Limitation

### Single HomeKit Connection

The Tado Internet Bridge hardware **only allows ONE HomeKit connection at a time**. 

**Before using Tado Local**, you must:

1. **Remove existing HomeKit pairings** from:
   - iPhone/iPad Home app
   - Home Assistant HomeKit Controller
   - Other HomeKit applications

2. **OR reset the bridge's HomeKit configuration**:
   - Press and hold the small reset button on the back for **10+ seconds**
   - The LED will blink to confirm reset
   - See [Tado's official guide](https://support.tado.com/en/articles/3387334-how-can-i-reset-the-homekit-configuration-of-the-internet-bridge)

**Note**: This limitation is hardware-based. We may eventually expose Tado Local itself as a HomeKit bridge to work around this, but that's future work. Contributions welcome!

---

## 🐛 Troubleshooting

### "Device reports 'Unavailable' for pairing"

The bridge is already paired to another HomeKit controller. See [Important Limitation](#-important-limitation) above.

### Connection drops or "None" values

The proxy automatically reconnects and ignores temporary `None` values. Wait a few seconds for the connection to restore.

### Cloud authentication not working

1. Check that port 4407 is accessible in your browser
2. Visit `http://localhost:4407/status` to see the authentication URL
3. Make sure you're using the correct Tado account credentials

### Starting fresh

```bash
# Remove database and re-pair
rm ~/.tado-local.db

# Run initial setup again
tado-local --bridge-ip 192.168.1.100 --pin 123-45-678
```

---

## 🤝 Contributing

**We'd love your help!** Whether you're a casual user finding bugs or a developer adding features, all contributions are welcome.

### Ways to Contribute

- **🐛 Report bugs**: [Open an issue](https://github.com/ampscm/TadoLocal/issues) with details
- **💡 Suggest features**: Share your ideas in [Discussions](https://github.com/ampscm/TadoLocal/discussions)
- **📝 Improve docs**: Fix typos, add examples, clarify instructions
- **🔧 Write code**: Pick an issue or propose a new feature
- **🧪 Test**: Try the alpha and report what works (or doesn't!)

### For Developers

**Priority areas needing help**:

- [x] **Domoticz plugin** - ✅ Already available in `domoticz/` directory!
- [ ] **Home Assistant HACS integration** - Make installation easier for HA users
- [ ] **Docker container** - Simplify deployment with docker-compose
- [ ] **Web UI enhancements** - Additional controls, better mobile support
- [ ] **Test coverage** - Unit and integration tests
- [ ] **Multi-bridge support** - Handle multiple Tado systems
- [ ] **HomeKit bridge** - Expose Tado Local as a HomeKit accessory (workaround for single connection limit)
- [ ] **Documentation improvements** - More examples, tutorials, video guides

### Development Setup

```bash
# Clone and install in development mode
git clone https://github.com/ampscm/TadoLocal.git
cd TadoLocal
pip install -e .[dev]

# Run with live reloading
python -m tado_local --bridge-ip 192.168.1.100

# Run tests (when available)
pytest

# Check code style
ruff check .
```

### Project Structure

```
tado_local/
├── __init__.py         # Package initialization  
├── __main__.py         # CLI entry point
├── api.py              # Main TadoLocalAPI class
├── routes.py           # FastAPI REST endpoints
├── bridge.py           # HomeKit bridge communication
├── state.py            # Device state & history management
├── cache.py            # Characteristic caching
├── database.py         # SQLite schema
├── homekit_uuids.py    # UUID mappings
└── static/
    └── index.html      # Web UI
```

---

## 🏗️ Architecture

### How It Works

```
┌─────────────────┐
│  Tado Bridge    │ ←─── HomeKit Protocol (local, fast, no rate limits)
│  (HomeKit)      │
└────────┬────────┘
         │
         ↓
┌─────────────────┐
│  Tado Local     │ ←─── You are here
│  (This Project) │
└────────┬────────┘
         │
         ├──→ REST API (port 4407)
         ├──→ Web UI (http://localhost:4407)
         ├──→ Real-time Events (SSE)
         └──→ SQLite Database (history & state)
                │
                ↓
         ┌──────────────────┐
         │  Your Smart Home │
         │  - Domoticz      │
         │  - Home Assistant│
         │  - openHAB       │
         │  - Custom Scripts│
         └──────────────────┘
```

### Technology Stack

- **aiohomekit**: HomeKit protocol implementation
- **FastAPI**: Modern REST API with automatic docs
- **SQLite**: Persistent storage (history, credentials, state)
- **Tado Cloud API**: Device metadata only (battery status, zone names)
- **Python 3.11+**: Async/await for efficient performance

### Data Flow

1. **Real-time updates**: HomeKit bridge sends notifications → Tado Local → Your apps (via SSE or polling)
2. **Control commands**: Your apps → Tado Local REST API → HomeKit bridge → Tado devices
3. **History storage**: All state changes saved to SQLite with 10-second resolution
4. **Metadata sync**: Cloud API refreshed every 4 hours for battery status and zone names

---

## 📄 License

Apache License 2.0 - see [LICENSE](LICENSE) file for details.

Free for personal and commercial use. Attribution appreciated but not required.

---

## 🙏 Acknowledgments

- Built on [aiohomekit](https://github.com/Jc2k/aiohomekit) - Excellent HomeKit protocol library
- Inspired by [Home Assistant](https://www.home-assistant.io/) and [Domoticz](http://domoticz.com) communities
- Thanks to everyone who reverse-engineered the Tado and HomeKit protocols

---

## 💬 Support & Community

- **🐛 Bug Reports**: [GitHub Issues](https://github.com/ampscm/TadoLocal/issues)
- **💭 Discussions**: [GitHub Discussions](https://github.com/ampscm/TadoLocal/discussions)  
- **📖 API Docs**: `http://localhost:4407/docs` (when running)

---

## 🎯 Roadmap

### Current Status: **Alpha**

✅ **Working Now**:
- Local HomeKit control
- REST API with full documentation
- Web UI with zone controls and visual history charts
- **Native Domoticz plugin** (see `domoticz/` directory)
- Real-time event streaming (SSE)
- SQLite persistence and history
- Auto-reconnection
- Hybrid local + cloud architecture

🚧 **Coming Soon**:
- Docker container
- Home Assistant HACS integration
- Improved mobile web UI
- Advanced scheduling features
- Multi-bridge support

---

**Ready to get started?** Jump to [Quick Start](#-quick-start) or check out the [API documentation](http://localhost:4407/docs) once you're running!

**Questions?** Open a [Discussion](https://github.com/ampscm/TadoLocal/discussions) - we're here to help!

