# Tado Local Plugin for Domoticz

This Domoticz plugin connects to Tado Local to monitor and control your Tado heating zones.

## Features

- **Automatic Zone Discovery**: Automatically creates Domoticz devices for each Tado zone
- **Real-time Updates**: Uses Server-Sent Events (SSE) to receive instant state changes
- **Temperature & Humidity Monitoring**: Displays current temperature and humidity for each zone
- **Thermostat Control**: Set target temperature and heating mode (On/Off)
- **Heating Status Indicator**: Shows when zone is actively heating
- **Battery Status**: All zone devices report battery level from the zone's leader thermostat
- **Extended Framework**: Supports unlimited zones using modern Domoticz APIs with hierarchical device structure
- **Auto-reconnect**: Automatically reconnects if connection is lost
- **Configurable Retry**: Set custom retry interval for connection attempts

## Device Numbering Scheme

The plugin uses a fixed 5-unit block per zone. Each zone uses 5 consecutive Domoticz Unit numbers:

- **Unit 1**: Temperature + Humidity sensor
- **Unit 2**: Thermostat (setpoint control)
- **Unit 3**: Heating status selector (Off/Heat/Cool)
- **Unit 4**: First additional thermostat (non-leader, if present)
- **Unit 5**: Reserved for future use

### Example

- **Zone 1**: Units 1-5
- **Zone 2**: Units 6-10
- **Zone 3**: Units 11-15
- ...and so on (up to 51 zones, 255 units max).

### Heating Status Selector (Unit 3)

The third device for each zone is a selector switch with three states:
- **Off**: Heating is off
- **Heat**: Zone is actively heating
- **Cool**: Zone is actively cooling (as reported by HomeKit accessories)

This matches the Tado `cur_heating` values:
- 0 = Off
- 1 = Heat
- 2 = Cool

### Additional Thermostats

If a zone has more than one thermostat, the first non-leader thermostat is mapped to Unit 4 as a temperature + humidity sensor. Further thermostats are ignored.

### Battery Status

All devices report battery status from the zone's leader thermostat.

## Installation

1. **Copy the plugin to Domoticz**:
   ```bash
   cd domoticz/plugins
   mkdir TadoLocal
   cp /path/to/tado-local/domoticz/plugin.py TadoLocal/
   ```

2. **Restart Domoticz**:
   ```bash
   sudo systemctl restart domoticz
   ```

3. **Enable the plugin**:
   - Go to Domoticz web interface
   - Navigate to: **Setup → Hardware**
   - Click **Add** and select **Tado Local** from the Type dropdown
   - Configure the plugin parameters (see below)
   - Click **Add**

## Configuration

### Parameters

- **API URL** (required): The URL to your Tado Local instance
  - Default: `http://localhost:4407`
  - Example: `http://192.168.1.100:4407`

- **Retry Interval** (required): How long to wait (in seconds) before retrying connection after failure
  - Default: `30`
  - Range: 10-300 seconds recommended

- **Auto Enable Devices**: Automatically enable newly discovered devices
  - Options: Yes / No
  - Default: Yes

- **API Key** (optional): Bearer token for API authentication
  - Leave empty for no authentication (default)
  - If set, will send as `Authorization: Bearer <key>` header
  - Use this if you've configured API key authentication in Tado Local
  - Note: Authentication is optional and primarily prevents accidental access on local networks

- **Debug**: Enable debug logging
  - Options: True / False
  - Default: False

### Authentication

The plugin supports optional API key authentication using Bearer tokens. This is useful for:
- Preventing accidental access from other clients on your local network
- Basic access control (not cryptographically secure over HTTP)

**To enable authentication:**
1. Configure one or more API keys in your Tado Local REST API (see main README)
   ```bash
   export TADO_API_KEYS="key-for-domoticz key-for-other-client"
   ```
2. Enter one of the configured keys in the plugin's "API Key" field
3. The plugin will send `Authorization: Bearer <your-key>` with all requests

**Note:** The server can accept multiple API keys (space-separated in `TADO_API_KEYS`), but each client only needs to know one key. The web UI (`/` and `/static/*`) remains accessible without authentication.

## How It Works

1. **Initialization**: When the plugin starts, it connects to the `/events/zones` SSE endpoint
2. **Zone Discovery**: As zone events arrive, the plugin automatically creates Domoticz devices
3. **Real-time Updates**: Temperature, humidity, and heating status are updated in real-time
4. **Control**: When you change settings in Domoticz, the plugin sends commands to the API
5. **Resilience**: If connection is lost, the plugin automatically retries

## Device Types

Each Tado zone appears as a **Temperature + Humidity** device in Domoticz showing:
- Current temperature (°C)
- Current humidity (%)
- Target temperature
- Heating status (OFF / ON / HEATING)

## Controlling Devices

### Via Domoticz UI
- Click on a zone device to set target temperature
- Use the On/Off switch to enable/disable heating

### Via Scripts/Automation
You can control devices using Domoticz scripts (Lua, dzVents, Python) or HTTP API.

## Troubleshooting

### Plugin not connecting
1. Verify the API URL is correct and reachable
2. Check that Tado Local is running: `curl http://your-api-url/status`
3. Enable Debug mode to see detailed logs
4. Check Domoticz logs: **Setup → Log**

### Devices not updating
1. Verify SSE connection in debug logs
2. Test the SSE endpoint manually: `curl http://your-api-url/events/zones`
3. Check for network/firewall issues

### Connection keeps dropping
1. Increase retry interval
2. Check network stability
3. Verify Tado Local is stable
4. Check Domoticz logs for error messages

## API Endpoints Used

- `GET /events/zones`: SSE stream for real-time zone updates
- `POST /zones/{zone_id}/control`: Control zone settings

## Requirements

- Domoticz 2020.2 or newer
- Tado Local running and accessible
- Python 3.x (included with Domoticz)

## Support

For issues related to:
- **The plugin**: Open an issue on the Tado Local GitHub repository
- **Domoticz**: Visit the Domoticz forum
- **Tado Local**: Check the main project documentation

## License

This plugin is part of the Tado Local project and follows the same license.
