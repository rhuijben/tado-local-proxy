"""
Tado Local Plugin for Domoticz
Connects to Tado Local and creates/updates thermostat devices for each zone.
"""
#
# Copyright 2025 The TadoLocal and AmpScm contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Domoticz plugin XML manifest (ignore Python linter errors on this XML block)
"""
<plugin key="TadoLocal" name="Tado Local" author="ampscm" version="1.0.1" wikilink="https://github.com/ampscm/TadoLocal">
    <description>
        <h2>Tado Local Plugin</h2><br/>
        Connects to Tado Local REST API to monitor and control Tado heating zones.<br/>
        <br/>
        <h3>Device Layout</h3>
        Each zone uses 5 consecutive Unit numbers:<br/>
        - Zone 1: Units 1-5 (temp sensor, thermostat, heating, extra thermostat slots)<br/>
        - Zone 2: Units 6-10<br/>
        - Zone 3: Units 11-15<br/>
        - etc.<br/>
        <br/>
        Within each zone's 5 units:<br/>
        - Unit 1: Temperature + Humidity sensor<br/>
        - Unit 2: Thermostat setpoint control<br/>
        - Unit 3: Heating status indicator<br/>
        - Unit 4: First additional thermostat (if present)<br/>
        - Unit 5: Reserved for future use<br/>
        <br/>
        Supports up to 51 zones (255 unit limit ÷ 5 units per zone).<br/>
        All devices report battery status from their thermostat.<br/>
        <br/>
        <h3>Features</h3>
        - Real-time updates via Server-Sent Events (SSE)<br/>
        - Automatic zone discovery and device creation<br/>
        - Temperature, humidity, and heating status monitoring<br/>
        - Thermostat control (target temperature and on/off)<br/>
        - Battery status reporting<br/>
        - Auto-reconnect with configurable retry interval<br/>
        - Optional DZGA-Flask voicecontrol XML setup<br/>
        <br/>
        <h3>Voice Control Integration</h3>
        Enable "Setup voicecontrol XML" to automatically configure device descriptions for DZGA-Flask integration.<br/>
        This will merge the thermostat with its temperature sensor and heating mode selector for Google Assistant.<br/>
        See <a href="https://github.com/DewGew/DZGA-Flask/wiki/3.-Configuration#device-configuraton">DZGA-Flask Device Configuration</a> for details.<br/>
    </description>
    <params>
        <param field="Address" label="API URL" width="300px" required="true" default="http://localhost:4407"/>
        <param field="Mode1" label="Retry Interval (seconds)" width="100px" required="true" default="30"/>
        <param field="Mode2" label="Auto Enable Devices" width="100px">
            <options>
                <option label="Yes" value="true" default="true"/>
                <option label="No" value="false"/>
            </options>
        </param>
        <param field="Mode3" label="API Key (optional)" width="300px" default="" password="true"/>
        <param field="Mode4" label="Setup voicecontrol XML" width="100px">
            <options>
                <option label="Yes" value="true"/>
                <option label="No" value="false" default="true"/>
            </options>
        </param>
        <param field="Mode6" label="Debug" width="100px">
            <options>
                <option label="True" value="Debug"/>
                <option label="False" value="Normal" default="true"/>
            </options>
        </param>
    </params>
</plugin>
"""

import Domoticz
import json
import time
from typing import Dict, Any, Optional

class BasePlugin:
    """Tado Local plugin for Domoticz"""

    def __init__(self):
        self.api_url = ""
        self.retry_interval = 30
        self.auto_enable_devices = True
        self.setup_voicecontrol = False
        self.api_key = ""
        self.sse_connection = None
        self.zones_fetch_connection = None
        self.thermostats_fetch_connection = None
        self.control_connections = {}  # Track control connections and their pending requests
        self.zones_cache: Dict[int, Dict[str, Any]] = {}
        self.thermostats_cache: Dict[int, Dict[str, Any]] = {}  # device_id -> thermostat info
        self.last_connection_attempt = 0
        self.heartbeat_counter = 0
        self.is_connecting = False
        self.zones_fetched = False
        self.thermostats_fetched = False
        self.sse_buffer = ""  # Buffer for accumulating SSE data
        self.device_creation_attempted = set()  # Track which devices we've tried to create

    def onStart(self):
        """Domoticz calls this when the plugin is started"""
        Domoticz.Debug("onStart called")

        # Get parameters
        self.api_url = Parameters["Address"].rstrip('/')
        self.retry_interval = int(Parameters["Mode1"])
        self.auto_enable_devices = (Parameters.get("Mode2", "true") == "true")
        self.api_key = Parameters.get("Mode3", "").strip()
        self.setup_voicecontrol = (Parameters.get("Mode4", "false") == "true")

        # Set debug mode
        if Parameters["Mode6"] == "Debug":
            Domoticz.Debugging(1)

        Domoticz.Log(f"Tado Local Plugin started - API: {self.api_url}")
        Domoticz.Log(f"Retry interval: {self.retry_interval} seconds")
        Domoticz.Log(f"Auto-enable devices: {'Yes' if self.auto_enable_devices else 'No'}")
        Domoticz.Log(f"Setup voicecontrol XML: {'Yes' if self.setup_voicecontrol else 'No'}")
        Domoticz.Log(f"Retry interval: {self.retry_interval} seconds")
        Domoticz.Log(f"Auto-enable devices: {'Yes' if self.auto_enable_devices else 'No'}")
        if self.api_key:
            Domoticz.Log("API Key configured (authentication enabled)")
        else:
            Domoticz.Log("No API Key configured (authentication disabled)")

        # Clean up any devices with invalid unit numbers (> 255 or corrupt data)
        devices_to_delete = []
        for unit in Devices:
            try:
                device = Devices[unit]
                Domoticz.Debug(f"Found existing device: Unit {unit}, Name: {device.Name}")

                # Mark existing units as created to prevent recreation attempts
                self.device_creation_attempted.add(unit)

                # Check if unit number is valid
                if isinstance(unit, int) and unit > 255:
                    Domoticz.Log(f"Found device with invalid unit {unit}: {device.Name} - marking for deletion")
                    devices_to_delete.append(unit)
                # Try to access device properties to detect corruption
                _ = device.sValue
                _ = device.nValue
            except Exception as e:
                Domoticz.Error(f"Device {unit} is corrupted: {e} - marking for deletion")
                devices_to_delete.append(unit)

        # Delete problematic devices
        for unit in devices_to_delete:
            try:
                Domoticz.Log(f"Deleting corrupted/invalid device: Unit {unit}")
                Devices[unit].Delete()
            except Exception as e:
                Domoticz.Error(f"Failed to delete device {unit}: {e}")

        if devices_to_delete:
            Domoticz.Log(f"Cleaned up {len(devices_to_delete)} invalid devices")

        Domoticz.Log(f"Found {len(self.device_creation_attempted)} existing devices")

        # Set heartbeat to 5 seconds for responsive initial connection
        # After first connection, we rely on SSE for real-time updates
        Domoticz.Heartbeat(5)

        # Don't start connection immediately - wait for first heartbeat
        # This ensures Domoticz completes initialization first
        Domoticz.Log("Will connect to API on first heartbeat...")

    def getAuthHeaders(self) -> str:
        """Build Authorization header string if API key is configured"""
        if self.api_key:
            return f"Authorization: Bearer {self.api_key}\r\n"
        return ""

    def onStop(self):
        """Domoticz calls this when the plugin is stopped"""
        Domoticz.Debug("onStop called")

        # Close SSE connection if open
        if self.sse_connection:
            try:
                self.sse_connection.Disconnect()
            except:
                pass
            self.sse_connection = None

        # Close zones fetch connection if open
        if self.zones_fetch_connection:
            try:
                self.zones_fetch_connection.Disconnect()
            except:
                pass
            self.zones_fetch_connection = None

        Domoticz.Log("Tado Local Plugin stopped")

    def onConnect(self, Connection, Status, Description):
        """Domoticz calls this when a connection is made"""
        Domoticz.Debug(f"onConnect: {Connection.Name}, Status: {Status}, Description: {Description}")

        if Status == 0:
            Domoticz.Debug(f"Connected successfully to {Connection.Name}")

            if Connection == self.zones_fetch_connection:
                # Zones fetch connection established, send GET request
                Domoticz.Debug("Sending GET request for /zones")
                headers = {
                    'Accept': 'application/json',
                    'Connection': 'close'
                }
                if self.api_key:
                    headers['Authorization'] = f'Bearer {self.api_key}'

                sendData = {
                    'Verb': 'GET',
                    'URL': '/zones',
                    'Headers': headers
                }
                Connection.Send(sendData)

            elif Connection == self.thermostats_fetch_connection:
                # Thermostats fetch connection established, send GET request
                Domoticz.Debug("Sending GET request for /thermostats")
                headers = {
                    'Accept': 'application/json',
                    'Connection': 'close'
                }
                if self.api_key:
                    headers['Authorization'] = f'Bearer {self.api_key}'

                sendData = {
                    'Verb': 'GET',
                    'URL': '/thermostats',
                    'Headers': headers
                }
                Connection.Send(sendData)

            elif Connection == self.sse_connection:
                # SSE connection established, send raw HTTP request
                Domoticz.Debug("Sending SSE request for /events with zone+device types and 5-minute refresh")

                # Build raw HTTP GET request with types=zone,device and refresh_interval=300 (5 minutes)
                # This ensures Domoticz gets both zone and device updates for non-leader thermostats
                auth_header = self.getAuthHeaders()
                request = (
                    "GET /events?types=zone,device&refresh_interval=300 HTTP/1.1\r\n"
                    f"Host: {Connection.Address}:{Connection.Port}\r\n"
                    "User-Agent: Domoticz/1.0\r\n"
                    "Accept: text/event-stream\r\n"
                    "Cache-Control: no-cache\r\n"
                    "Connection: keep-alive\r\n"
                    f"{auth_header}"
                    "\r\n"
                )

                Connection.Send(request.encode('utf-8'))
                self.is_connecting = False

            else:
                # Check if this is a control connection
                if Connection in self.control_connections:
                    # Send the pending request
                    sendData = self.control_connections[Connection]
                    Connection.Send(sendData)
                    Domoticz.Debug(f"Sent control request on {Connection.Name}")
                    # Don't delete yet - wait for response
                    # Don't disconnect yet - wait for response
        else:
            Domoticz.Error(f"Failed to connect to {Connection.Name}: {Description} (Status: {Status})")
            self.is_connecting = False
            if Connection == self.sse_connection:
                self.sse_connection = None
            elif Connection == self.zones_fetch_connection:
                self.zones_fetch_connection = None
            elif Connection in self.control_connections:
                # Clean up failed control connection
                del self.control_connections[Connection]

    def onMessage(self, Connection, Data):
        """Domoticz calls this when data is received"""
        Domoticz.Debug(f"onMessage called from {Connection.Name}")

        try:
            # Handle control connection responses
            if Connection in self.control_connections:
                Domoticz.Debug(f"Control response received")
                if 'Status' in Data:
                    status = int(Data['Status'])
                    Domoticz.Log(f"Control request status: {status}")
                    if status != 200:
                        error_data = Data.get('Data', b'').decode('utf-8', errors='ignore')
                        Domoticz.Error(f"Control request failed with status {status}: {error_data}")
                    else:
                        Domoticz.Log("Control request successful")
                # Clean up and disconnect
                del self.control_connections[Connection]
                # Don't explicitly disconnect - let Domoticz handle cleanup
                # The Connection: close header should close it automatically
                return

            # Handle raw SSE data (from raw TCP connection)
            if Connection == self.sse_connection:
                # Decode bytes to string
                if isinstance(Data, dict) and 'Data' in Data:
                    raw_data = Data['Data'].decode('utf-8', errors='ignore')
                elif isinstance(Data, bytes):
                    raw_data = Data.decode('utf-8', errors='ignore')
                else:
                    raw_data = str(Data)

                Domoticz.Debug(f"SSE raw data: {len(raw_data)} bytes")

                # Add to buffer
                self.sse_buffer += raw_data

                # Process complete SSE messages from buffer
                # SSE format: "data: {json}\n\n"
                # Chunked encoding adds: "<hex-size>\r\ndata...\r\n"
                while True:
                    # Simple approach: look for "data: " lines
                    data_start = self.sse_buffer.find('data: ')
                    if data_start == -1:
                        break

                    # Find end of this SSE message (\n\n or \r\n\r\n)
                    data_end = self.sse_buffer.find('\n\n', data_start)
                    if data_end == -1:
                        data_end = self.sse_buffer.find('\r\n\r\n', data_start)
                        if data_end == -1:
                            break  # Wait for more data
                        data_end += 4  # Include \r\n\r\n
                    else:
                        data_end += 2  # Include \n\n

                    # Extract the complete SSE message
                    sse_message = self.sse_buffer[data_start:data_end]
                    self.sse_buffer = self.sse_buffer[data_end:]  # Remove processed part

                    # Parse the SSE message
                    for line in sse_message.split('\n'):
                        line = line.strip()

                        if line.startswith('data: '):
                            json_str = line[6:]  # Remove "data: " prefix
                            try:
                                event_data = json.loads(json_str)
                                self.handleEvent(event_data)
                            except json.JSONDecodeError as e:
                                Domoticz.Debug(f"Failed to parse JSON: {e} - Data: {json_str}")

                # Keep buffer size reasonable (max 10KB)
                if len(self.sse_buffer) > 10240:
                    Domoticz.Debug(f"SSE buffer too large ({len(self.sse_buffer)} bytes), clearing old data")
                    # Keep only last 1KB
                    self.sse_buffer = self.sse_buffer[-1024:]

                return  # Early return for SSE connection

            # Check if this is HTTP response data (for zones fetch)
            if 'Status' in Data:
                status = int(Data['Status'])
                Domoticz.Debug(f"HTTP Status: {status}")

                if status != 200:
                    Domoticz.Error(f"HTTP Error {status}: {Data.get('Data', b'').decode('utf-8', errors='ignore')}")
                    return

            # Handle zones fetch response (JSON)
            if Connection == self.zones_fetch_connection and 'Data' in Data:
                raw_data = Data['Data'].decode('utf-8', errors='ignore')
                Domoticz.Debug(f"Received zones data: {len(raw_data)} bytes")

                try:
                    # Parse JSON response
                    zones_response = json.loads(raw_data)
                    zones = zones_response.get('zones', [])

                    Domoticz.Log(f"Fetched {len(zones)} zones from API")

                    # Create devices for each zone
                    for zone in zones:
                        zone_id = zone.get('zone_id')
                        zone_name = zone.get('name')
                        state = zone.get('state', {})

                        if zone_id and zone_name:
                            self.updateZoneDevice(zone_id, zone_name, state)

                    self.zones_fetched = True

                    # Close zones fetch connection
                    Connection.Disconnect()
                    self.zones_fetch_connection = None

                    # Now fetch thermostats for non-leader devices
                    url_parts = self.api_url.replace('http://', '').replace('https://', '').split(':')
                    host = url_parts[0]
                    port = int(url_parts[1]) if len(url_parts) > 1 else 8000
                    use_ssl = 'https://' in self.api_url

                    Domoticz.Log("Fetching thermostats from API...")
                    self.thermostats_fetch_connection = Domoticz.Connection(
                        Name="Thermostats Fetch",
                        Transport="TCP/IP",
                        Protocol="HTTPS" if use_ssl else "HTTP",
                        Address=host,
                        Port=str(port)
                    )
                    self.thermostats_fetch_connection.Connect()

                except json.JSONDecodeError as e:
                    Domoticz.Error(f"Failed to parse zones JSON: {e}")
                    Connection.Disconnect()
                    self.zones_fetch_connection = None
                    return

            # Handle thermostats fetch response (JSON)
            if Connection == self.thermostats_fetch_connection and 'Data' in Data:
                raw_data = Data['Data'].decode('utf-8', errors='ignore')
                Domoticz.Debug(f"Received thermostats data: {len(raw_data)} bytes")

                try:
                    # Parse JSON response
                    thermostats_response = json.loads(raw_data)
                    thermostats = thermostats_response.get('thermostats', [])

                    Domoticz.Log(f"Fetched {len(thermostats)} thermostats from API")

                    # Create devices for non-leader thermostats
                    for thermostat in thermostats:
                        device_id = thermostat.get('device_id')
                        zone_id = thermostat.get('zone_id')
                        zone_name = thermostat.get('zone_name', 'Unknown Zone')
                        serial_number = thermostat.get('serial_number', '')
                        is_zone_leader = thermostat.get('is_zone_leader', False)
                        state = thermostat.get('state', {})

                        if device_id:
                            # Cache thermostat info including zone_id
                            self.thermostats_cache[device_id] = {
                                'zone_id': zone_id,
                                'zone_name': zone_name,
                                'serial_number': serial_number,
                                'is_zone_leader': is_zone_leader,
                                'last_update': time.time()
                            }

                            # Only create sensors for non-leader thermostats
                            # Zone leaders are already represented by the main zone device
                            if not is_zone_leader and zone_id:
                                # Track the first non-leader thermostat for each zone
                                # We only have 1 slot (Unit 4) for additional thermostats per zone
                                zone_key = f'extra_thermostat_{zone_id}'
                                if zone_key not in self.zones_cache or zone_id not in self.zones_cache:
                                    # First additional thermostat for this zone
                                    if zone_id not in self.zones_cache:
                                        self.zones_cache[zone_id] = {}
                                    self.zones_cache[zone_id]['extra_thermostat_device_id'] = device_id
                                    Domoticz.Log(f"Creating sensor for non-leader thermostat: {zone_name} ({serial_number[-4:]}) - device_id {device_id}")
                                    self.updateThermostatDevice(device_id, zone_id, zone_name, serial_number, state)
                                elif self.zones_cache[zone_id].get('extra_thermostat_device_id') == device_id:
                                    # This is the tracked additional thermostat, update it
                                    Domoticz.Debug(f"Updating tracked non-leader thermostat: {zone_name} ({serial_number[-4:]}) - device_id {device_id}")
                                    self.updateThermostatDevice(device_id, zone_id, zone_name, serial_number, state)
                                else:
                                    # Second+ additional thermostat - ignore
                                    Domoticz.Debug(f"Ignoring additional thermostat beyond first: {zone_name} ({serial_number[-4:]}) - device_id {device_id}")
                            else:
                                Domoticz.Debug(f"Skipping zone leader: {zone_name} ({serial_number[-4:]})")

                    self.thermostats_fetched = True

                    # Close thermostats fetch connection
                    Connection.Disconnect()
                    self.thermostats_fetch_connection = None

                    # Now connect to SSE
                    url_parts = self.api_url.replace('http://', '').replace('https://', '').split(':')
                    host = url_parts[0]
                    port = int(url_parts[1]) if len(url_parts) > 1 else 8000
                    self.connectSSE(host, port, False)

                except json.JSONDecodeError as e:
                    Domoticz.Error(f"Failed to parse zones JSON: {e}")
                    Connection.Disconnect()
                    self.zones_fetch_connection = None
                    return

        except Exception as e:
            Domoticz.Error(f"Error in onMessage: {e}")

    def onCommand(self, Unit, Command, Level, Hue):
        """Domoticz calls this when a device is controlled"""
        Domoticz.Log(f"onCommand: Unit={Unit}, Command={Command}, Level={Level}")

        try:
            # Find the zone_id for this thermostat unit
            # Thermostat is at position 2 in each zone's 5-unit block
            # Unit 2 → Zone 1, Unit 7 → Zone 2, Unit 12 → Zone 3, etc.
            # Formula: zone_id = ((unit - 2) // 5) + 1

            zone_id = None
            for zid, zone_data in self.zones_cache.items():
                if zone_data.get('thermostat_unit') == Unit:
                    zone_id = zid
                    break

            if zone_id is None:
                Domoticz.Debug(f"Unit {Unit} is not a thermostat device")
                return

            zone_name = self.zones_cache[zone_id]['name']
            Domoticz.Log(f"Processing command for zone: {zone_name} (ID: {zone_id})")

            # Handle thermostat setpoint changes
            if Command == "Set Level":
                target_temp = float(Level)
                if target_temp == 0:
                    Domoticz.Log(f"Turning off {zone_name}")
                    self.controlZone(zone_id, heating_enabled=False)
                elif target_temp < 0:
                    # Negative value = resume schedule/enable without changing temperature
                    Domoticz.Log(f"Resuming schedule for {zone_name} (enable heating)")
                    self.controlZone(zone_id, heating_enabled=True)
                else:
                    Domoticz.Log(f"Setting {zone_name} to {target_temp}°C")
                    self.controlZone(zone_id, target_temperature=target_temp, heating_enabled=True)
            elif Command == "Off":
                Domoticz.Log(f"Turning off {zone_name}")
                self.controlZone(zone_id, heating_enabled=False)
            elif Command == "On":
                Domoticz.Log(f"Turning on {zone_name}")
                self.controlZone(zone_id, heating_enabled=True)

        except Exception as e:
            Domoticz.Error(f"Error in onCommand: {e}")

    def onNotification(self, Name, Subject, Text, Status, Priority, Sound, ImageFile):
        """Domoticz calls this when a notification is received"""
        Domoticz.Debug(f"onNotification: {Name}, {Subject}, {Text}, {Status}")

    def onDisconnect(self, Connection):
        """Domoticz calls this when a connection is closed"""
        Domoticz.Debug(f"onDisconnect: {Connection.Name}")

        if Connection == self.sse_connection:
            Domoticz.Log("SSE connection closed - will reconnect on next heartbeat")
            self.sse_connection = None
            self.sse_buffer = ""  # Clear buffer on disconnect
        elif Connection == self.zones_fetch_connection:
            Domoticz.Debug("Zones fetch connection closed")
            self.zones_fetch_connection = None

    def onHeartbeat(self):
        """Domoticz calls this every heartbeat interval"""
        Domoticz.Debug("onHeartbeat called")
        self.heartbeat_counter += 1

        # After first successful connection, increase heartbeat interval to reduce CPU usage
        # We use SSE for real-time updates, heartbeat is only for reconnection checks
        if self.sse_connection and self.heartbeat_counter == 2:
            Domoticz.Heartbeat(30)  # Switch to 30 seconds after initial connection
            Domoticz.Debug("Increased heartbeat interval to 30 seconds")

        # Check if we need to reconnect
        if self.sse_connection is None and not self.is_connecting:
            # Check if enough time has passed since last attempt
            current_time = time.time()
            if current_time - self.last_connection_attempt >= self.retry_interval:
                Domoticz.Log("Reconnecting to SSE stream...")
                self.fetchZonesAndConnect()

        # No need to poll - the SSE connection with types=zone,device and refresh_interval=300
        # provides both real-time events and periodic refresh updates for all devices

        # Send keepalive debug message every 10 heartbeats
        if self.sse_connection and self.heartbeat_counter % 10 == 0:
            Domoticz.Debug("Connection still active")

    def fetchZonesAndConnect(self):
        """Fetch zones and thermostats from API and establish SSE connection"""
        Domoticz.Debug("fetchZonesAndConnect called")
        self.last_connection_attempt = time.time()
        self.zones_fetched = False
        self.thermostats_fetched = False

        try:
            # Parse API URL
            url_parts = self.api_url.replace('http://', '').replace('https://', '').split(':')
            host = url_parts[0]
            port = int(url_parts[1]) if len(url_parts) > 1 else 8000
            use_ssl = 'https://' in self.api_url

            # Close existing connections
            if self.zones_fetch_connection:
                try:
                    self.zones_fetch_connection.Disconnect()
                except:
                    pass

            if self.thermostats_fetch_connection:
                try:
                    self.thermostats_fetch_connection.Disconnect()
                except:
                    pass

            # Create connection for /zones request
            Domoticz.Log("Fetching zones from API...")
            self.zones_fetch_connection = Domoticz.Connection(
                Name="Zones Fetch",
                Transport="TCP/IP",
                Protocol="HTTP",
                Address=host,
                Port=str(port)
            )
            self.zones_fetch_connection.Connect()

        except Exception as e:
            Domoticz.Error(f"Error in fetchZonesAndConnect: {e}")

    def connectSSE(self, host: str, port: int, use_ssl: bool = False):
        """Establish SSE connection to /events/zones"""
        Domoticz.Debug(f"Connecting to SSE stream at {host}:{port}")

        try:
            self.is_connecting = True

            # Close existing connection if any
            if self.sse_connection:
                try:
                    self.sse_connection.Disconnect()
                except:
                    pass

            # Create new SSE connection using raw TCP (not HTTP protocol)
            # This allows us to receive streaming data without waiting for response to end
            self.sse_connection = Domoticz.Connection(
                Name="SSE Events",
                Transport="TCP/IP",
                Protocol="None",  # Raw TCP, we'll handle HTTP/SSE ourselves
                Address=host,
                Port=str(port)
            )
            self.sse_connection.Connect()

        except Exception as e:
            Domoticz.Error(f"Error connecting to SSE: {e}")
            self.is_connecting = False
            self.sse_connection = None

    def handleEvent(self, event_data: Dict[str, Any]):
        """Handle incoming SSE event"""
        Domoticz.Debug(f"Received event: {event_data.get('type')}")

        try:
            event_type = event_data.get('type')

            if event_type == 'keepalive':
                Domoticz.Debug("Received keepalive")
                return

            if event_type == 'zone':
                zone_id = event_data.get('zone_id')
                zone_name = event_data.get('zone_name')
                state = event_data.get('state', {})
                is_refresh = event_data.get('refresh', False)

                if is_refresh:
                    Domoticz.Debug(f"Zone refresh update: {zone_name} (ID: {zone_id})")
                else:
                    Domoticz.Debug(f"Zone update: {zone_name} (ID: {zone_id})")

                # Skip if zones haven't been fetched yet
                if not self.zones_fetched:
                    Domoticz.Debug(f"Skipping zone event - zones not yet fetched")
                    return

                # Create or update device for this zone
                self.updateZoneDevice(zone_id, zone_name, state)

            elif event_type == 'device':
                device_id = event_data.get('device_id')
                zone_name = event_data.get('zone_name', 'Unknown Zone')
                serial = event_data.get('serial', '')
                state = event_data.get('state', {})

                Domoticz.Debug(f"Device update: device_id={device_id}, zone={zone_name}, serial={serial}")

                # Skip if thermostats haven't been fetched yet
                if not self.thermostats_fetched:
                    Domoticz.Debug(f"Skipping device event - thermostats not yet fetched")
                    return

                # Update individual thermostat sensor (non-leader devices only)
                if device_id and device_id in self.thermostats_cache:
                    cached_info = self.thermostats_cache[device_id]
                    is_zone_leader = cached_info.get('is_zone_leader', False)
                    zone_id = cached_info.get('zone_id')

                    if not is_zone_leader and zone_id:
                        # Check if this is the tracked additional thermostat for this zone
                        if zone_id in self.zones_cache:
                            tracked_device_id = self.zones_cache[zone_id].get('extra_thermostat_device_id')
                            if tracked_device_id == device_id:
                                # This is the tracked additional thermostat - update it
                                Domoticz.Debug(f"Updating tracked additional thermostat: device_id={device_id}")
                                self.updateThermostatDevice(device_id, zone_id, zone_name, serial, state)
                            else:
                                # Not the tracked one - ignore
                                Domoticz.Debug(f"Ignoring non-tracked additional thermostat: device_id={device_id}")
                        else:
                            # Zone not in cache yet - skip
                            Domoticz.Debug(f"Zone {zone_id} not in cache yet for device {device_id}")
                    else:
                        Domoticz.Debug(f"Skipping device event for zone leader: device_id={device_id}")
                else:
                    Domoticz.Debug(f"Device {device_id} not found in thermostats cache")

        except Exception as e:
            Domoticz.Error(f"Error handling event: {e}")

    def updateZoneDevice(self, zone_id: int, zone_name: str, state: Dict[str, Any]):
        """Create or update Domoticz devices for a zone

        Simple unit allocation: 5 units per zone
        - Zone 1: Units 1-5
        - Zone 2: Units 6-10
        - Zone 3: Units 11-15
        - etc.

        Within each zone's 5 units:
        - Unit 1: Temp+Humidity sensor
        - Unit 2: Thermostat setpoint
        - Unit 3: Heating indicator
        - Unit 4: First additional thermostat (if present)
        - Unit 5: Reserved
        """
        Domoticz.Debug(f"updateZoneDevice: zone_id={zone_id}, zone_name='{zone_name}'")

        # Safety check - don't create/update devices if we don't have valid state
        if not state:
            Domoticz.Debug(f"Skipping updateZoneDevice for {zone_name} - no state data")
            return

        try:
            # Calculate base unit: Zone 1 → Unit 1, Zone 2 → Unit 6, Zone 3 → Unit 11, etc.
            # Formula: base_unit = (zone_id - 1) * 5 + 1
            MAX_ZONES = 51  # 51 zones × 5 units = 255 max units
            if zone_id < 1 or zone_id > MAX_ZONES:
                Domoticz.Error(f"Zone ID {zone_id} out of range (1-{MAX_ZONES})")
                return

            base_unit = (zone_id - 1) * 5 + 1
            temp_unit = base_unit          # Unit 1, 6, 11, 16, etc.
            thermostat_unit = base_unit + 1  # Unit 2, 7, 12, 17, etc.
            heating_unit = base_unit + 2     # Unit 3, 8, 13, 18, etc.

            Domoticz.Debug(f"Zone {zone_id} '{zone_name}' uses units {base_unit}-{base_unit+4}")

            # Cache zone info for control commands
            if zone_id not in self.zones_cache:
                self.zones_cache[zone_id] = {}
            self.zones_cache[zone_id].update({
                'name': zone_name,
                'base_unit': base_unit,
                'thermostat_unit': thermostat_unit
            })

            # Create temperature + humidity sensor if needed
            if temp_unit not in self.device_creation_attempted and temp_unit not in Devices:
                self.device_creation_attempted.add(temp_unit)
                try:
                    device = Domoticz.Device(
                        Name=f"{zone_name}",
                        Unit=temp_unit,
                        TypeName="Temp+Hum",
                        Used=1 if self.auto_enable_devices else 0
                    )
                    device.Create()
                    Domoticz.Log(f"Created temperature sensor: {zone_name} (Unit {temp_unit})")
                    
                    # Set voicecontrol XML if enabled (after device creation)
                    if self.setup_voicecontrol and temp_unit in Devices:
                        # We need to get the idx values after creation, so we'll update on next cycle
                        pass
                except Exception as e:
                    Domoticz.Debug(f"Temp sensor (Unit {temp_unit}) already exists: {e}")

            # Create thermostat setpoint device if needed
            if thermostat_unit not in self.device_creation_attempted and thermostat_unit not in Devices:
                self.device_creation_attempted.add(thermostat_unit)
                try:
                    device = Domoticz.Device(
                        Name=f"{zone_name} Thermostat",
                        Unit=thermostat_unit,
                        Type=242,
                        Subtype=1,
                        Used=1 if self.auto_enable_devices else 0
                    )
                    device.Create()
                    Domoticz.Log(f"Created thermostat: {zone_name} (Unit {thermostat_unit})")
                except Exception as e:
                    Domoticz.Debug(f"Thermostat (Unit {thermostat_unit}) already exists: {e}")

            # Create heating mode selector if needed
            if heating_unit not in self.device_creation_attempted and heating_unit not in Devices:
                self.device_creation_attempted.add(heating_unit)
                try:
                    # Selector Switch with HomeKit-compatible modes
                    # Level 0 = Off, Level 1 = Heat, Level 2 = Idle
                    # This matches cur_heating values: 0=Off, 1=Heating, 2=Idle
                    options = {
                        'LevelActions': '||',  # 2 separators = 3 levels
                        'LevelNames': 'Off|Heat|Idle',
                        'LevelOffHidden': 'true',  # Hide the internal "Off" state
                        'SelectorStyle': '1'  # 0=buttons, 1=dropdown
                    }
                    device = Domoticz.Device(
                        Name=f"{zone_name} Heating",
                        Unit=heating_unit,
                        Type=244,
                        Subtype=62,
                        Switchtype=18,
                        Options=options,
                        Used=1 if self.auto_enable_devices else 0
                    )
                    device.Create()
                    Domoticz.Log(f"Created heating status selector: {zone_name} (Unit {heating_unit})")
                except Exception as e:
                    Domoticz.Debug(f"Heating status selector (Unit {heating_unit}) already exists: {e}")

            # Update voicecontrol descriptions if enabled and all devices exist
            if self.setup_voicecontrol and temp_unit in Devices and thermostat_unit in Devices and heating_unit in Devices:
                try:
                    # Get device idx values
                    temp_idx = Devices[temp_unit].ID
                    thermostat_idx = Devices[thermostat_unit].ID
                    heating_idx = Devices[heating_unit].ID
                    
                    # Check if we need to update descriptions (only if not already set)
                    if '<voicecontrol>' not in Devices[thermostat_unit].Description:
                        # Get existing description and append voicecontrol XML
                        existing_desc = Devices[thermostat_unit].Description.strip()
                        voicecontrol_xml = f"""<voicecontrol>
  nicknames = {zone_name} Thermostat
  room = {zone_name}
  actual_temp_idx = {temp_idx}
  minThreehold = 5
  maxThreehold = 30
</voicecontrol>"""
                        thermostat_desc = f"{existing_desc}\n{voicecontrol_xml}" if existing_desc else voicecontrol_xml
                        Devices[thermostat_unit].Update(nValue=Devices[thermostat_unit].nValue, 
                                                        sValue=Devices[thermostat_unit].sValue,
                                                        Description=thermostat_desc)
                        Domoticz.Debug(f"Set voicecontrol XML for thermostat unit {thermostat_unit}")
                    
                    # Update temp sensor to hide it
                    if '<voicecontrol>' not in Devices[temp_unit].Description:
                        existing_desc = Devices[temp_unit].Description.strip()
                        voicecontrol_xml = f"""<voicecontrol>
  room = {zone_name}
  hide = True
</voicecontrol>"""
                        temp_desc = f"{existing_desc}\n{voicecontrol_xml}" if existing_desc else voicecontrol_xml
                        Devices[temp_unit].Update(nValue=Devices[temp_unit].nValue,
                                                  sValue=Devices[temp_unit].sValue,
                                                  Description=temp_desc)
                        Domoticz.Debug(f"Set voicecontrol XML for temp sensor unit {temp_unit}")
                    
                    # Update heating selector to hide it
                    if '<voicecontrol>' not in Devices[heating_unit].Description:
                        existing_desc = Devices[heating_unit].Description.strip()
                        voicecontrol_xml = f"""<voicecontrol>
  room = {zone_name}
  hide = True
</voicecontrol>"""
                        heating_desc = f"{existing_desc}\n{voicecontrol_xml}" if existing_desc else voicecontrol_xml
                        Devices[heating_unit].Update(nValue=Devices[heating_unit].nValue,
                                                     sValue=Devices[heating_unit].sValue,
                                                     Description=heating_desc)
                        Domoticz.Debug(f"Set voicecontrol XML for heating selector unit {heating_unit}")
                        
                except Exception as e:
                    Domoticz.Debug(f"Error setting voicecontrol XML: {e}")

            # Extract state values
            cur_temp = state.get('cur_temp_c')
            humidity = state.get('hum_perc', 50)
            target_temp = state.get('target_temp_c', 0)
            mode = state.get('mode', 0)
            cur_heating = state.get('cur_heating', 0)
            battery_low = state.get('battery_low', False)

            # Skip update if critical values are missing
            if cur_temp is None:
                Domoticz.Debug(f"Skipping update for {zone_name} - no temperature data")
                return

            # Ensure humidity is valid
            if humidity is None or humidity < 0 or humidity > 100:
                humidity = 50

            # Determine battery level from battery_low flag
            # Domoticz: 255 = normal/full, 0-100 = percentage, <20 = low warning
            battery_level = 10 if battery_low else 255

            # Update temperature + humidity sensor
            if temp_unit in Devices:
                temp_status = "Normal" if not battery_low else "Low Battery"
                sValue = f"{cur_temp};{humidity};{temp_status}"
                Devices[temp_unit].Update(nValue=0, sValue=sValue, BatteryLevel=battery_level)
                Domoticz.Debug(f"Updated {zone_name} temp: {cur_temp}°C, {humidity}%, battery: {battery_level}")

            # Update thermostat setpoint
            if thermostat_unit in Devices:
                setpoint_temp = target_temp if mode != 0 else 0.0
                Domoticz.Debug(f"Updating {zone_name} thermostat: setpoint={setpoint_temp}°C, mode={mode}")
                Devices[thermostat_unit].Update(nValue=0, sValue=str(setpoint_temp), BatteryLevel=battery_level)

            # Update heating status selector
            if heating_unit in Devices:
                # cur_heating: 0=Off, 1=Heating, 2=Idle
                # Map directly to selector levels 0, 1, 2
                Domoticz.Debug(f"Updating {zone_name} heating status: {cur_heating}")
                    # For selector switch: nValue matches cur_heating (1=Heating, 2=Idle, 0=Off)
                    nValue = cur_heating if cur_heating in (0, 1, 2) else 0
                    sValue = str(cur_heating)
                    Devices[heating_unit].Update(nValue=nValue, sValue=sValue, BatteryLevel=battery_level)

        except Exception as e:
            Domoticz.Error(f"Error updating zone device: {e}")

    def updateThermostatDevice(self, device_id: int, zone_id: int, zone_name: str, serial_number: str, state: Dict[str, Any]):
        """Create or update Domoticz temp+hum sensor for additional thermostats (non-leaders)

        Uses Unit 4 within the zone's 5-unit block for the first additional thermostat.
        Additional thermostats beyond the first are ignored (Unit 5 reserved).
        """
        Domoticz.Debug(f"updateThermostatDevice: device_id={device_id}, zone_id={zone_id}, zone={zone_name}, serial={serial_number}")

        # Safety check
        if not state:
            Domoticz.Debug(f"Skipping updateThermostatDevice for device {device_id} - no state data")
            return

        try:
            # Calculate base unit for this zone
            MAX_ZONES = 51
            if zone_id < 1 or zone_id > MAX_ZONES:
                Domoticz.Debug(f"Zone ID {zone_id} out of range for additional thermostat")
                return

            base_unit = (zone_id - 1) * 5 + 1
            extra_unit = base_unit + 3  # Unit 4 within the zone's block

            # Only create device for the first additional thermostat
            # We use Unit 4, which leaves Unit 5 reserved for future use
            Domoticz.Debug(f"Additional thermostat for zone {zone_id} uses Unit {extra_unit}")

            # Create temperature + humidity sensor if needed
            if extra_unit not in self.device_creation_attempted and extra_unit not in Devices:
                self.device_creation_attempted.add(extra_unit)
                try:
                    device_name = f"{zone_name} ({serial_number[-4:]})"
                    Domoticz.Device(
                        Name=device_name,
                        Unit=extra_unit,
                        TypeName="Temp+Hum",
                        Used=1 if self.auto_enable_devices else 0
                    ).Create()
                    Domoticz.Log(f"Created additional thermostat sensor: {device_name} (Unit {extra_unit})")
                except Exception as e:
                    Domoticz.Debug(f"Additional thermostat (Unit {extra_unit}) already exists: {e}")

            # Extract state values
            cur_temp = state.get('cur_temp_c')
            humidity = state.get('hum_perc', 50)
            battery_low = state.get('battery_low', False)

            # Skip update if critical values missing
            if cur_temp is None:
                Domoticz.Debug(f"Skipping update for device {device_id} - no temperature data")
                return

            # Ensure humidity is valid
            if humidity is None or humidity < 0 or humidity > 100:
                humidity = 50

            # Determine battery level from battery_low flag
            battery_level = 10 if battery_low else 255

            # Update temperature + humidity sensor
            if extra_unit in Devices:
                temp_status = "Normal" if not battery_low else "Low Battery"
                sValue = f"{cur_temp};{humidity};{temp_status}"
                Devices[extra_unit].Update(nValue=0, sValue=sValue, BatteryLevel=int(battery_level))
                Domoticz.Debug(f"Updated thermostat {device_id} sensor: {cur_temp}°C, {humidity}%, battery: {battery_level}")

        except Exception as e:
            Domoticz.Error(f"Error updating thermostat device: {e}")

    def controlZone(self, zone_id: int, target_temperature: Optional[float] = None, heating_enabled: Optional[bool] = None):
        """Control a zone with optional temperature and/or heating mode in a single request"""
        try:
            # Build query parameters
            params = []
            if target_temperature is not None:
                params.append(f"temperature={target_temperature}")
                Domoticz.Log(f"controlZone: zone {zone_id} temperature={target_temperature}°C")
            if heating_enabled is not None:
                params.append(f"heating_enabled={'true' if heating_enabled else 'false'}")
                Domoticz.Log(f"controlZone: zone {zone_id} heating_enabled={heating_enabled}")

            if not params:
                Domoticz.Error("controlZone called with no parameters")
                return

            # Send POST request to /zones/{zone_id}/set with query parameters
            url_parts = self.api_url.replace('http://', '').replace('https://', '').split(':')
            host = url_parts[0]
            port = int(url_parts[1]) if len(url_parts) > 1 else 8000

            # Create temporary connection for control request
            control_conn = Domoticz.Connection(
                Name="Zone Control",
                Transport="TCP/IP",
                Protocol="HTTP",
                Address=host,
                Port=str(port)
            )

            # Build request with query parameters
            query_string = "&".join(params)
            headers = {
                'Content-Length': '0',
                'Connection': 'close'  # Ask server to close connection after response
            }
            if self.api_key:
                headers['Authorization'] = f'Bearer {self.api_key}'

            sendData = {
                'Verb': 'POST',
                'URL': f'/zones/{zone_id}/set?{query_string}',
                'Headers': headers
            }

            # Store the request to send after connection is established
            self.control_connections[control_conn] = sendData

            # Connect (will send in onConnect callback)
            control_conn.Connect()
            Domoticz.Log(f"Initiating control request for zone {zone_id}")

        except Exception as e:
            Domoticz.Error(f"Error controlling zone: {e}")


global _plugin
_plugin = BasePlugin()

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onConnect(Connection, Status, Description):
    global _plugin
    _plugin.onConnect(Connection, Status, Description)

def onMessage(Connection, Data):
    global _plugin
    _plugin.onMessage(Connection, Data)

def onCommand(Unit, Command, Level, Hue):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Hue)

def onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile):
    global _plugin
    _plugin.onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile)

def onDisconnect(Connection):
    global _plugin
    _plugin.onDisconnect(Connection)

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()
