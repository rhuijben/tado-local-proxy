# Tado Local - Demo Scripts

This directory contains example scripts demonstrating how to use the Tado Local REST API.

## zone-manager.py

A command-line utility for viewing and controlling Tado heating zones.

### Features

- List all zones with current temperature, humidity, and heating status
- Set target temperature for specific zones
- Turn zones on/off
- Display home name from the API
- Verbose mode for debugging

### Usage

```bash
# List all zones with their current status (default action)
python zone-manager.py

# List zones with verbose output
python zone-manager.py -l -v

# Set zone 1 to 21°C
python zone-manager.py -z 1 -t 21

# Set multiple zones to 20°C
python zone-manager.py -z 1 -z 3 -t 20

# Disable (turn off) zone 2
python zone-manager.py -z 2 -d

# Reset zone 1 to schedule (re-enable at current target temperature)
python zone-manager.py -z 1 -r
```

### Options

- `-h` - Show help message
- `-l` - List zone status (default if no other options given)
- `-v` - Increase verbosity (can be specified multiple times)
- `-z <zone-id>` - Select specific zone by ID (can be specified multiple times)
- `-t <celsius>` - Set temperature (≥5 = heating enabled)
- `-d` - Disable heating (turn off)
- `-r` - Reset to schedule (re-enable heating at current target temp)

### Environment Variables

- `TADO_LOCAL_API` - API base URL (default: `http://localhost:4407`)

### Example Output

```
== My Home ==

ID   Zone Name     Heat  Temp   Mode      Current   Humidity
----------------------------------------------------------------------
1    Living Room   ON   21.0°C HEAT      20.5°C    58.0%
2    Bedroom            OFF    OFF       19.0°C    62.0%
3    Kitchen       ON   20.0°C HEAT      19.5°C    55.0%
```

## Creating Your Own Scripts

These demos show how easy it is to integrate with Tado Local. The API is simple HTTP/JSON:

```python
import requests

# Get all zones
response = requests.get('http://localhost:4407/zones')
zones = response.json()

# Set temperature for a zone
requests.post(
    'http://localhost:4407/zones/1/set',
    json={"temperature": 21.0}
)
```

See the [main README](../README.md) for full API documentation and the interactive Swagger UI at `http://localhost:4407/docs`.
