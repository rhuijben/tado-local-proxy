# Tado Local Proxy Plugin for Domoticz

This Domoticz plugin connects to the Tado Local Proxy API to monitor and control your Tado heating zones.

## Features

- **Automatic Zone Discovery**: Automatically creates Domoticz devices for each Tado zone
- **Real-time Updates**: Uses Server-Sent Events (SSE) to receive instant state changes
- **Temperature & Humidity Monitoring**: Displays current temperature and humidity for each zone
- **Thermostat Control**: Set target temperature and heating mode (On/Off)
- **Auto-reconnect**: Automatically reconnects if connection is lost
- **Configurable Retry**: Set custom retry interval for connection attempts

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
   - Click **Add** and select **Tado Local Proxy** from the Type dropdown
   - Configure the plugin parameters (see below)
   - Click **Add**

## Configuration

### Parameters

- **API URL** (required): The URL to your Tado Local Proxy API
  - Default: `http://localhost:8000`
  - Example: `http://192.168.1.100:8000`

- **Retry Interval** (required): How long to wait (in seconds) before retrying connection after failure
  - Default: `30`
  - Range: 10-300 seconds recommended

- **Debug**: Enable debug logging
  - Options: True / False
  - Default: False

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
2. Check that Tado Local Proxy is running: `curl http://your-api-url/status`
3. Enable Debug mode to see detailed logs
4. Check Domoticz logs: **Setup → Log**

### Devices not updating
1. Verify SSE connection in debug logs
2. Test the SSE endpoint manually: `curl http://your-api-url/events/zones`
3. Check for network/firewall issues

### Connection keeps dropping
1. Increase retry interval
2. Check network stability
3. Verify Tado Local Proxy is stable
4. Check Domoticz logs for error messages

## API Endpoints Used

- `GET /events/zones`: SSE stream for real-time zone updates
- `POST /zones/{zone_id}/control`: Control zone settings

## Requirements

- Domoticz 2020.2 or newer
- Tado Local Proxy running and accessible
- Python 3.x (included with Domoticz)

## Support

For issues related to:
- **The plugin**: Open an issue on the Tado Local Proxy GitHub repository
- **Domoticz**: Visit the Domoticz forum
- **Tado Local Proxy API**: Check the main project documentation

## License

This plugin is part of the Tado Local Proxy project and follows the same license.
