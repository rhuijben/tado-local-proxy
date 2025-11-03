"""
Tado Local Plugin for Domoticz
Connects to Tado Local and creates/updates thermostat devices for each zone.
"""
#
# Copyright 2025 TadoLocalProxy and AmpScm contributors.
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
        Uses Domoticz Extended Framework for unlimited device support.<br/>
        <br/>
        <h3>Device Hierarchy</h3>
        Each zone uses Unit = zone_id with sub-units for device types:<br/>
        - Sub-unit 1: Sensor (temp+humidity)<br/>
        - Sub-unit 2: Thermostat (setpoint)<br/>
        - Sub-unit 3: Heating indicator<br/>
        - Sub-units 10+: Additional non-leader thermostats<br/>
        <br/>
        Example: Zone 1 → Unit 1 (Sub 1, 2, 3), Zone 2 → Unit 2 (Sub 1, 2, 3)<br/>
        <br/>
        Extended Framework removes the 255 device limit, supporting unlimited zones.<br/>
        All zone devices report battery status from the zone's leader thermostat.<br/>
        <br/>
        <h3>Features</h3>
        - Real-time updates via Server-Sent Events (SSE)<br/>
        - Automatic zone discovery and device creation<br/>
        - Temperature, humidity, and heating status monitoring<br/>
        - Thermostat control (target temperature and on/off)<br/>
        - Battery status reporting<br/>
        - Auto-reconnect with configurable retry interval<br/>
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
        self.auto_enable_devices = (Parameters.get("Mode2", "1") == "1")
        self.api_key = Parameters.get("Mode3", "").strip()
        
        # Set debug mode
        if Parameters["Mode6"] == "Debug":
            Domoticz.Debugging(1)
        
        Domoticz.Log(f"Tado Local Plugin started - API: {self.api_url}")
        Domoticz.Log(f"Retry interval: {self.retry_interval} seconds")
        Domoticz.Log(f"Auto-enable devices: {'Yes' if self.auto_enable_devices else 'No'}")
        if self.api_key:
            Domoticz.Log("API Key configured (authentication enabled)")
        else:
            Domoticz.Log("No API Key configured (authentication disabled)")
        
        # Clean up any devices with invalid unit numbers (> 255 or corrupt data)
        # This fixes devices created with old buggy unit number logic
        devices_to_delete = []
        for unit in Devices:
            try:
                device = Devices[unit]
                Domoticz.Debug(f"Found existing device: Unit {unit}, Name: {device.Name}, DeviceID: {device.DeviceID}")
                
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
        
        # Set heartbeat to 30 seconds (reduced from 10)
        Domoticz.Heartbeat(30)
        
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
                                Domoticz.Log(f"Creating sensor for non-leader thermostat: {zone_name} ({serial_number[-4:]})")
                                self.updateThermostatDevice(device_id, zone_id, zone_name, serial_number, state)
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
        Domoticz.Log(f"onCommand called: Unit={Unit}, Command={Command}, Level={Level}, Hue={Hue}")
        
        try:
            # Find the zone_id for this device
            zone_id = None
            for zid, zone_data in self.zones_cache.items():
                # Check if this is the setpoint device for this zone
                if zone_data.get('setpoint_unit') == Unit:
                    zone_id = zid
                    break
            
            Domoticz.Log(f"Found zone_id: {zone_id} for unit {Unit}")
            
            if zone_id is None:
                Domoticz.Log(f"Unit {Unit} is not a thermostat setpoint device")
                Domoticz.Log(f"zones_cache: {self.zones_cache}")
                return
            
            zone_name = self.zones_cache[zone_id]['name']
            Domoticz.Log(f"Processing command for zone: {zone_name} (ID: {zone_id})")
            
            # Handle thermostat setpoint changes
            # Note: Thermostat devices send "Set Level" command, not "Set Point"
            if Command == "Set Level":
                # Level contains the target temperature directly
                target_temp = float(Level)
                
                if target_temp == 0:
                    # Setting to 0 means turn off
                    Domoticz.Log(f"Turning off {zone_name}")
                    self.controlZone(zone_id, heating_enabled=False)
                else:
                    # Set target temperature and ensure heating is on (single request)
                    Domoticz.Log(f"Setting {zone_name} target temperature to {target_temp}°C")
                    self.controlZone(zone_id, target_temperature=target_temp, heating_enabled=True)
            
            elif Command == "Off":
                # Turn off heating
                Domoticz.Log(f"Turning off {zone_name}")
                self.controlZone(zone_id, heating_enabled=False)
            
            elif Command == "On":
                # Turn on heating (with last known setpoint)
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
        
        # Check if we need to reconnect
        if self.sse_connection is None and not self.is_connecting:
            # Check if enough time has passed since last attempt
            current_time = time.time()
            if current_time - self.last_connection_attempt >= self.retry_interval:
                Domoticz.Log("Reconnecting to SSE stream...")
                self.fetchZonesAndConnect()
        
        # No need to poll - the SSE connection with types=zone,device and refresh_interval=300
        # provides both real-time events and periodic refresh updates for all devices
        
        # Send keepalive debug message every 10 heartbeats (5 minutes with 30s interval)
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
                        self.updateThermostatDevice(device_id, zone_id, zone_name, serial, state)
                    else:
                        Domoticz.Debug(f"Skipping device event for zone leader: device_id={device_id}")
                else:
                    Domoticz.Debug(f"Device {device_id} not found in thermostats cache")
        
        except Exception as e:
            Domoticz.Error(f"Error handling event: {e}")
    
    def updateZoneDevice(self, zone_id: int, zone_name: str, state: Dict[str, Any]):
        """Create or update Domoticz devices for a zone using Extended Framework
        
        Extended Framework hierarchy using DeviceID with sub-units:
        - Zone X uses Unit = zone_id (1, 2, 3, etc.)
          - Sub-unit 1: Sensor (temp+humidity)
          - Sub-unit 2: Thermostat (setpoint)
          - Sub-unit 3: Heating indicator
          - Sub-units 10+: Additional non-leader thermostats
        
        This provides a clean hierarchy supporting unlimited zones.
        All zone devices report battery status from the zone's leader thermostat.
        """
        Domoticz.Log(f"updateZoneDevice called: zone_id={zone_id}, zone_name='{zone_name}'")
        
        # Safety check - don't create/update devices if we don't have valid state
        if not state:
            Domoticz.Debug(f"Skipping updateZoneDevice for {zone_name} - no state data")
            return
        
        try:
            # Extended Framework: Use zone_id as Unit, with sub-units for device types
            # Zone 1, Unit 1: sub-unit 1 (sensor), 2 (thermostat), 3 (heating)
            # Zone 2, Unit 2: sub-unit 1 (sensor), 2 (thermostat), 3 (heating)
            # etc.
            
            # Sanity check: limit zone_id to reasonable range to prevent crashes
            MAX_ZONE_ID = 1000  # Practical limit - most users have <50 zones
            if zone_id > MAX_ZONE_ID:
                Domoticz.Log(f"Skipping zone_id={zone_id} ('{zone_name}'): exceeds maximum supported zone ID {MAX_ZONE_ID}")
                return
            
            unit = zone_id
            temp_subunit = 1      # Temp+Humidity sensor
            setpoint_subunit = 2   # Thermostat setpoint
            heating_subunit = 3    # Heating status indicator
            
            # Check if we already have devices for this zone
            if zone_id not in self.zones_cache:
                self.zones_cache[zone_id] = {
                    'unit': unit,
                    'name': zone_name
                }
            
            # Create temperature + humidity device if needed (Unit X, Sub-unit 1)
            device_key = (unit, temp_subunit)
            if device_key not in self.device_creation_attempted:
                self.device_creation_attempted.add(device_key)
                
                try:
                    # Try to create - will fail silently if already exists
                    Domoticz.Device(
                        Name=f"{zone_name}",
                        Unit=unit,
                        DeviceID=f"{unit}:{temp_subunit}",  # Extended Framework ID with sub-unit
                        TypeName="Temp+Hum",
                        Used=1 if self.auto_enable_devices else 0
                    ).Create()
                    Domoticz.Log(f"Created temperature sensor for zone: {zone_name} (Unit {unit}, Sub-unit {temp_subunit})")
                except Exception as e:
                    # Device already exists - this is normal and not an error
                    Domoticz.Debug(f"Temp sensor ({unit}:{temp_subunit}) for {zone_name} already exists")
            else:
                Domoticz.Debug(f"Skipping temp sensor for {zone_name} - already attempted in this session")
            
            # Create thermostat setpoint device if needed (Unit X, Sub-unit 2)
            device_key = (unit, setpoint_subunit)
            if device_key not in self.device_creation_attempted:
                self.device_creation_attempted.add(device_key)
                
                try:
                    # Use Thermostat Setpoint device - allows continuous temperature selection
                    Domoticz.Device(
                        Name=f"{zone_name} Thermostat",
                        Unit=unit,
                        DeviceID=f"{unit}:{setpoint_subunit}",  # Extended Framework ID with sub-unit
                        Type=242,
                        Subtype=1,
                        Used=1 if self.auto_enable_devices else 0
                    ).Create()
                    
                    Domoticz.Log(f"Created thermostat for zone: {zone_name} (Unit {unit}, Sub-unit {setpoint_subunit})")
                except Exception as e:
                    Domoticz.Debug(f"Thermostat ({unit}:{setpoint_subunit}) for {zone_name} already exists")
            else:
                Domoticz.Debug(f"Skipping thermostat for {zone_name} - already attempted")
            
            # Create heating status indicator (Unit X, Sub-unit 3)
            device_key = (unit, heating_subunit)
            if device_key not in self.device_creation_attempted:
                self.device_creation_attempted.add(device_key)
                
                try:
                    # Use switch to show heating on/off status
                    Domoticz.Device(
                        Name=f"{zone_name} Heating",
                        Unit=unit,
                        DeviceID=f"{unit}:{heating_subunit}",  # Extended Framework ID with sub-unit
                        TypeName="Switch",
                        Used=1 if self.auto_enable_devices else 0
                    ).Create()
                    
                    Domoticz.Log(f"Created heating indicator for zone: {zone_name} (Unit {unit}, Sub-unit {heating_subunit})")
                except Exception as e:
                    Domoticz.Debug(f"Heating indicator ({unit}:{heating_subunit}) for {zone_name} already exists")
            else:
                Domoticz.Debug(f"Skipping heating indicator for {zone_name} - already attempted")
            
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
            
            # Determine battery level (255 = normal, <20 = low)
            battery_level = 20 if battery_low else 255
            
            # Update temperature + humidity device (Unit X, Sub-unit 1)
            if (unit, temp_subunit) in Devices and cur_temp is not None:
                temp_status = "Normal" if not battery_low else "Low Battery"
                sValue = f"{cur_temp};{humidity};{temp_status}"
                
                Devices[(unit, temp_subunit)].Update(
                    nValue=0,
                    sValue=sValue,
                    BatteryLevel=battery_level,
                    TimedOut=0
                )
                Domoticz.Debug(f"Updated {zone_name} temp sensor: {cur_temp}°C, {humidity}%")
            
            # Update thermostat setpoint device (Unit X, Sub-unit 2)
            # When mode=0 (Off), set target to 0
            # Otherwise use actual target temperature
            if (unit, setpoint_subunit) in Devices:
                setpoint_temp = target_temp if mode != 0 else 0.0
                
                # Thermostat setpoint uses sValue as just the temperature
                Domoticz.Log(f"Updating {zone_name} thermostat: setpoint={setpoint_temp}°C, mode={mode}")
                
                try:
                    Devices[(unit, setpoint_subunit)].Update(
                        nValue=0,
                        sValue=str(setpoint_temp),
                        BatteryLevel=battery_level,
                        TimedOut=0
                    )
                except Exception as e:
                    Domoticz.Error(f"Failed to update thermostat unit {unit} sub {setpoint_subunit}: {e}")
            
            # Update heating status indicator (Unit X, Sub-unit 3)
            if (unit, heating_subunit) in Devices:
                # cur_heating: 0=Off, 1=Heating, 2=Idle (on but not heating)
                # For switch: nValue=1 means On (heating), nValue=0 means Off
                is_heating = (cur_heating == 1)
                
                Domoticz.Log(f"Updating {zone_name} heating status: {'ON' if is_heating else 'OFF'} (cur_heating={cur_heating})")
                
                try:
                    Devices[(unit, heating_subunit)].Update(
                        nValue=1 if is_heating else 0,
                        sValue="On" if is_heating else "Off",
                        BatteryLevel=battery_level,
                        TimedOut=0
                    )
                except Exception as e:
                    Domoticz.Error(f"Failed to update heating status unit {unit} sub {heating_subunit}: {e}")
        
        except Exception as e:
            Domoticz.Error(f"Error updating zone device: {e}")
    
    def updateThermostatDevice(self, device_id: int, zone_id: int, zone_name: str, serial_number: str, state: Dict[str, Any]):
        """Create or update Domoticz temp+hum sensor for individual thermostats (non-leaders)
        
        Extended Framework hierarchy:
        - Additional non-leader thermostats use same Unit as zone, with sub-units starting at 10
        - Zone X, device_id 0 -> Unit X, Sub-unit 10
        - Zone X, device_id 1 -> Unit X, Sub-unit 11
        - etc.
        
        This keeps all zone devices logically grouped under the same Unit.
        """
        Domoticz.Debug(f"updateThermostatDevice: device_id={device_id}, zone_id={zone_id}, zone={zone_name}, serial={serial_number}")
        
        # Safety check - don't create/update devices if we don't have valid state
        if not state:
            Domoticz.Debug(f"Skipping updateThermostatDevice for device {device_id} - no state data")
            return
        
        try:
            # Use zone's unit with sub-units starting at 10 for additional thermostats
            # Zone devices use sub-units 1-3, additional thermostats use 10+
            unit = zone_id
            subunit = 10 + device_id
            
            # Sanity check: limit sub-unit numbers to prevent crashes
            # Most users only need the main zone devices (sub-units 1-3)
            # Limit additional thermostats to reasonable range
            MAX_SUBUNIT = 100  # Allow up to 90 additional thermostats per zone
            if subunit > MAX_SUBUNIT:
                Domoticz.Log(f"Skipping thermostat device_id={device_id} for zone {zone_id}: sub-unit {subunit} exceeds limit {MAX_SUBUNIT}")
                Domoticz.Log(f"Too many additional thermostats in zone '{zone_name}'. Only the first {MAX_SUBUNIT - 10} will be created.")
                return
            
            # Create temperature + humidity device if needed
            device_key = (unit, subunit)
            if device_key not in self.device_creation_attempted:
                self.device_creation_attempted.add(device_key)
                
                try:
                    # Create device with zone + serial name for identification
                    device_name = f"{zone_name} ({serial_number[-4:]})"
                    Domoticz.Device(
                        Name=device_name,
                        Unit=unit,
                        DeviceID=f"{unit}:{subunit}",  # Extended Framework ID with sub-unit
                        TypeName="Temp+Hum",
                        Used=1 if self.auto_enable_devices else 0
                    ).Create()
                    Domoticz.Log(f"Created thermostat sensor: {device_name} (Unit {unit}, Sub-unit {subunit})")
                except Exception as e:
                    Domoticz.Debug(f"Thermostat sensor ({unit}:{subunit}) already exists")
            else:
                Domoticz.Debug(f"Skipping thermostat sensor ({unit}:{subunit}) - already attempted")
            
            # Extract state values
            cur_temp = state.get('cur_temp_c')
            humidity = state.get('hum_perc', 50)
            battery_low = state.get('battery_low', False)
            
            # Skip update if critical values are missing
            if cur_temp is None:
                Domoticz.Debug(f"Skipping update for device {device_id} - no temperature data")
                return
            
            # Ensure humidity is valid
            if humidity is None or humidity < 0 or humidity > 100:
                humidity = 50
            
            # Determine battery level (255 = normal, <20 = low)
            battery_level = 20 if battery_low else 255
            
            # Update temperature + humidity device
            if (unit, subunit) in Devices and cur_temp is not None:
                temp_status = "Normal" if not battery_low else "Low Battery"
                sValue = f"{cur_temp};{humidity};{temp_status}"
                
                Devices[(unit, subunit)].Update(
                    nValue=0,
                    sValue=sValue,
                    BatteryLevel=battery_level,
                    TimedOut=0
                )
                Domoticz.Debug(f"Updated thermostat {device_id} sensor: {cur_temp}°C, {humidity}%")
        
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
