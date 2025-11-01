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

"""Tado Local API - Main API class for managing HomeKit connections and device state."""

import asyncio
import json
import logging
import sqlite3
import time
from collections import defaultdict
from typing import Dict, List, Any, Optional

from fastapi import HTTPException
from aiohomekit.controller.ip.pairing import IpPairing

from .state import DeviceStateManager
from .homekit_uuids import get_service_name, get_characteristic_name

# Configure logging
logger = logging.getLogger('tado-local')


class TadoLocalAPI:
    """Tado Local API that leverages HomeKit for real-time data without cloud dependency."""
    accessories_cache : List[Any]
    accessories_dict : Dict[str, Any]
    accessories_id : Dict[int, str]
    characteristic_map : Dict[tuple[int, int], str]
    device_to_characteristics : Dict[int, List[tuple[int, int, str]]]  # device_id -> [(aid, iid, char_type)]
    
    def __init__(self, db_path: str):
        self.pairing: Optional[IpPairing] = None
        self.accessories_cache = []
        self.accessories_dict = {}
        self.accessories_id = {}
        self.characteristic_map = {}
        self.device_to_characteristics = {}
        self.event_listeners: List[asyncio.Queue] = []
        self.last_update: Optional[float] = None
        self.device_states: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self.state_manager = DeviceStateManager(db_path)
        self.is_initializing = False  # Flag to suppress logging during startup
        
        # Cleanup tracking
        self.subscribed_characteristics: List[tuple[int, int]] = []
        self.background_tasks: List[asyncio.Task] = []
        self.is_shutting_down = False
        
    async def initialize(self, pairing: IpPairing):
        """Initialize the API with a HomeKit pairing."""
        self.pairing = pairing
        self.is_initializing = True  # Suppress change logging during init
        await self.refresh_accessories()
        await self.initialize_device_states()
        self.is_initializing = False  # Re-enable change logging
        await self.setup_event_listeners()
        logger.info("Tado Local API initialized successfully")
    
    async def cleanup(self):
        """Clean up resources and unsubscribe from events."""
        logger.info("Starting cleanup...")
        self.is_shutting_down = True
        
        # Cancel all background tasks
        if self.background_tasks:
            logger.info(f"Cancelling {len(self.background_tasks)} background tasks")
            for task in self.background_tasks:
                if not task.done():
                    task.cancel()
            
            # Wait for tasks to complete cancellation
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
            logger.info("Background tasks cancelled")
        
        # Unsubscribe from all event characteristics
        if self.pairing and self.subscribed_characteristics:
            try:
                logger.info(f"Unsubscribing from {len(self.subscribed_characteristics)} event characteristics")
                await self.pairing.unsubscribe(self.subscribed_characteristics)
                logger.info("Successfully unsubscribed from events")
            except Exception as e:
                logger.warning(f"Error during unsubscribe: {e}")
        
        # Close all event listener queues
        if self.event_listeners:
            logger.info(f"Closing {len(self.event_listeners)} event listener queues")
            for queue in self.event_listeners:
                try:
                    # Signal end of stream
                    await queue.put(None)
                except:
                    pass  # Queue might already be closed
            self.event_listeners.clear()
        
        logger.info("Cleanup complete")
        
    async def refresh_accessories(self):
        """Refresh accessories from HomeKit and cache them."""
        if not self.pairing:
            raise HTTPException(status_code=503, detail="Bridge not connected")
            
        try:
            raw_accessories = await self.pairing.list_accessories_and_characteristics()
            self.accessories_dict = self._process_raw_accessories(raw_accessories)
            self.accessories_cache = list(self.accessories_dict.values())
            self.last_update = time.time()
            logger.info(f"Refreshed {len(self.accessories_cache)} accessories")
            return self.accessories_cache
        except Exception as e:
            logger.error(f"Failed to refresh accessories: {e}")
            raise HTTPException(status_code=503, detail=f"Failed to refresh accessories: {e}")
        
    def _process_raw_accessories(self, raw_accessories):
        accessories={}

        for a in raw_accessories:
            aid = a.get('aid')
            # Try to find serial number from AccessoryInformation service
            serial_number = None
            
            for service in a.get('services', []):
                # AccessoryInformation service UUID
                if service.get('type') == '0000003E-0000-1000-8000-0026BB765291':
                    for char in service.get('characteristics', []):
                        # SerialNumber characteristic UUID
                        if char.get('type') == '00000030-0000-1000-8000-0026BB765291':
                            serial_number = char.get('value')
                            break
                if serial_number:
                    break
            
            # Use database device_id as primary key
            device_id = None
            
            # Register device and get device_id
            if serial_number:
                device_id = self.state_manager.get_or_create_device(serial_number, aid, a)
                
                # Map characteristics to device_id for efficient lookup
                char_list = []
                for service in a.get('services', []):
                    for char in service.get('characteristics', []):
                        char_type = char.get('type', '').lower()
                        iid = char.get('iid')
                        # Only track characteristics we care about
                        if char_type in [
                            DeviceStateManager.CHAR_CURRENT_TEMPERATURE,
                            DeviceStateManager.CHAR_TARGET_TEMPERATURE,
                            DeviceStateManager.CHAR_CURRENT_HEATING_COOLING,
                            DeviceStateManager.CHAR_TARGET_HEATING_COOLING,
                            DeviceStateManager.CHAR_HEATING_THRESHOLD,
                            DeviceStateManager.CHAR_COOLING_THRESHOLD,
                            DeviceStateManager.CHAR_TEMP_DISPLAY_UNITS,
                            DeviceStateManager.CHAR_BATTERY_LEVEL,
                            DeviceStateManager.CHAR_STATUS_LOW_BATTERY,
                            DeviceStateManager.CHAR_CURRENT_HUMIDITY,
                            DeviceStateManager.CHAR_TARGET_HUMIDITY,
                            DeviceStateManager.CHAR_ACTIVE,
                            DeviceStateManager.CHAR_VALVE_POSITION,
                        ]:
                            char_list.append((aid, iid, char_type))
                
                self.device_to_characteristics[device_id] = char_list
            
            # Use device_id as key (or fallback to aid if no serial)
            key = device_id if device_id else f'aid_{aid}'
            
            accessories[key] = {
                'id': device_id,  # Primary key for API
                'aid': aid,       # HomeKit accessory ID
                'serial_number': serial_number,
            } | a
            
            # Keep aid lookup for event handling
            if device_id:
                self.accessories_id[aid] = device_id

        return accessories
    
    async def initialize_device_states(self):
        """Poll all characteristics once on startup to establish baseline state."""
        if not self.pairing:
            logger.warning("No pairing available for initial state sync")
            return
        
        logger.info("Initializing device states from current values...")
        
        # Collect all readable characteristics we care about
        chars_to_poll = []
        
        for device_id, char_list in self.device_to_characteristics.items():
            for aid, iid, char_type in char_list:
                # Find the characteristic to check if it's readable
                for accessory in self.accessories_cache:
                    if accessory.get('aid') == aid:
                        for service in accessory.get('services', []):
                            for char in service.get('characteristics', []):
                                if char.get('iid') == iid:
                                    perms = char.get('perms', [])
                                    if 'pr' in perms:  # Readable
                                        chars_to_poll.append((aid, iid, device_id, char_type))
                                    break
        
        if not chars_to_poll:
            logger.warning("No characteristics found to poll for initialization")
            return
        
        logger.info(f"Polling {len(chars_to_poll)} characteristics for initial state...")
        
        # Poll in batches to avoid overwhelming the device
        batch_size = 10
        timestamp = time.time()
        
        for i in range(0, len(chars_to_poll), batch_size):
            batch = chars_to_poll[i:i+batch_size]
            char_keys = [(aid, iid) for aid, iid, _, _ in batch]
            
            try:
                results = await self.pairing.get_characteristics(char_keys)
                
                for (aid, iid, device_id, char_type) in batch:
                    if (aid, iid) in results:
                        char_data = results[(aid, iid)]
                        value = char_data.get('value')
                        
                        if value is not None:
                            # Update device state
                            field_name, old_val, new_val = self.state_manager.update_device_characteristic(
                                device_id, char_type, value, timestamp
                            )
                            if field_name:
                                logger.debug(f"Initialized device {device_id} {field_name}: {value}")
                
            except Exception as e:
                logger.error(f"Error polling batch during initialization: {e}")
        
        logger.info(f"Device state initialization complete - baseline established for {len(self.device_to_characteristics)} devices")

    
    async def setup_event_listeners(self):
        """Setup unified change detection with events + polling comparison."""
        if not self.pairing:
            return
            
        # Initialize change tracking
        self.change_tracker = {
            'events_received': 0,
            'polling_changes': 0,
            'last_values': {},  # Store last known values
            'event_characteristics': set(),  # Track which chars have events
        }
        
        # Populate last_values from current device states to avoid logging "None -> X" on startup
        for device_id, char_list in self.device_to_characteristics.items():
            current_state = self.state_manager.get_current_state(device_id)
            for aid, iid, char_type in char_list:
                # Map char_type to state field
                char_mapping = {
                    DeviceStateManager.CHAR_CURRENT_TEMPERATURE: 'current_temperature',
                    DeviceStateManager.CHAR_TARGET_TEMPERATURE: 'target_temperature',
                    DeviceStateManager.CHAR_CURRENT_HEATING_COOLING: 'current_heating_cooling_state',
                    DeviceStateManager.CHAR_TARGET_HEATING_COOLING: 'target_heating_cooling_state',
                    DeviceStateManager.CHAR_HEATING_THRESHOLD: 'heating_threshold_temperature',
                    DeviceStateManager.CHAR_COOLING_THRESHOLD: 'cooling_threshold_temperature',
                    DeviceStateManager.CHAR_TEMP_DISPLAY_UNITS: 'temperature_display_units',
                    DeviceStateManager.CHAR_BATTERY_LEVEL: 'battery_level',
                    DeviceStateManager.CHAR_STATUS_LOW_BATTERY: 'status_low_battery',
                    DeviceStateManager.CHAR_CURRENT_HUMIDITY: 'humidity',
                    DeviceStateManager.CHAR_TARGET_HUMIDITY: 'target_humidity',
                    DeviceStateManager.CHAR_ACTIVE: 'active_state',
                    DeviceStateManager.CHAR_VALVE_POSITION: 'valve_position',
                }
                field_name = char_mapping.get(char_type.lower())
                if field_name and field_name in current_state:
                    self.change_tracker['last_values'][(aid, iid)] = current_state[field_name]
        
        logger.info(f"Initialized change tracker with {len(self.change_tracker['last_values'])} known values from database")
        
        # Try to set up persistent event system
        await self.setup_persistent_events()
        
        # Always set up polling as backup/comparison
        await self.setup_polling_system()
    
    async def setup_persistent_events(self):
        """Set up persistent event subscriptions to all event characteristics."""
        try:
            logger.info("Setting up persistent event system...")
            
            # Register unified change handler for events
            def event_callback(update_data : dict[tuple[int, int], dict]):
                """Handle ALL HomeKit characteristic updates."""
                logger.debug(f"Event callback received update: {update_data}")
                for k, v in update_data.items():
                    asyncio.create_task(self.handle_change(k[0], k[1], v, source="EVENT"))

            # Register the callback with the pairing's dispatcher
            self.pairing.dispatcher_connect(event_callback)
            logger.info("Event callback registered with dispatcher")
            
            # Collect ALL event-capable characteristics from ALL accessories
            all_event_characteristics = []
            
            for accessory in self.accessories_cache:
                aid = accessory.get('aid')
                for service in accessory.get('services', []):
                    for char in service.get('characteristics', []):
                        perms = char.get('perms', [])
                        if 'ev' in perms:  # Event notification supported
                            iid = char.get('iid')
                            char_type = char.get('type', '').lower()
                            
                            # Track what this characteristic is
                            all_event_characteristics.append((aid, iid))
                            self.characteristic_map[(aid, iid)] = get_characteristic_name(char_type)
                            self.change_tracker['event_characteristics'].add((aid, iid))
                                        
            if all_event_characteristics:
                # Subscribe to ALL event characteristics at once - this is critical!
                await self.pairing.subscribe(all_event_characteristics)
                # Track subscriptions for cleanup
                self.subscribed_characteristics = all_event_characteristics.copy()
                logger.info(f"Subscribed to {len(all_event_characteristics)} event characteristics")
                logger.debug(f"Characteristic map: {self.characteristic_map}")
                                
                return True
            else:
                logger.warning("No event-capable characteristics found")
                return False
                
        except Exception as e:
            logger.warning(f"Event system setup failed: {e}")
            return False
       
    async def handle_change(self, aid, iid, update_data, source="UNKNOWN"):
        """Unified handler for all characteristic changes (events AND polling)."""
        try:
            # Extract change information
            value = update_data.get('value')
            timestamp = time.time()
            
            if aid is None or iid is None:
                logger.debug(f"Invalid change data from {source}: {update_data}")
                return
            
            # Ignore None values - these typically indicate network/connection issues
            # Events will restore the actual values once connection is restored
            if value is None:
                logger.debug(f"[{source}] Ignoring None value for aid={aid} iid={iid} (likely connection issue)")
                return
            
            # Get characteristic info - try cached first, then lookup
            char_key = (aid, iid)
            char_name = self.characteristic_map.get(char_key)
            
            # If not in cache, look it up from the accessory data
            if not char_name:
                for accessory in self.accessories_cache:
                    if accessory.get('aid') == aid:
                        for service in accessory.get('services', []):
                            for char in service.get('characteristics', []):
                                if char.get('iid') == iid:
                                    char_type = char.get('type', '').lower()
                                    char_name = get_characteristic_name(char_type)
                                    # Cache it for next time
                                    self.characteristic_map[char_key] = char_name
                                    break
                            if char_name:
                                break
                        break
                
                # Fallback if still not found
                if not char_name:
                    char_name = f"{aid}.{iid}"

                       
            # Check if this is actually a change
            last_value = self.change_tracker['last_values'].get(char_key)
            if last_value == value:
                return  # No actual change
            
            # Store new value
            self.change_tracker['last_values'][char_key] = value
            
            # Get device info for better logging
            device_id = self.accessories_id.get(aid)
            device_info = self.state_manager.get_device_info(device_id) if device_id else {}
            zone_name = device_info.get('zone_name', 'No Zone')
            device_name = device_info.get('name') or device_info.get('serial_number', f'Device {device_id}')
            is_zone_leader = device_info.get('is_zone_leader', False)
            
            # Update device state manager
            if device_id and device_id in self.accessories_dict:
                accessory = self.accessories_dict[device_id]
                
                if accessory.get('id'):
                    # Find the characteristic type for this aid/iid
                    char_type = None
                    for service in accessory.get('services', []):
                        for char in service.get('characteristics', []):
                            if char.get('iid') == iid:
                                char_type = char.get('type', '').lower()
                                break
                        if char_type:
                            break
                    
                    if char_type:
                        field_name, old_val, new_val = self.state_manager.update_device_characteristic(
                            accessory['id'], char_type, value, timestamp
                        )
                        if field_name:
                            logger.debug(f"Updated device {accessory['id']} {field_name}: {old_val} -> {new_val}")
            
            # Skip logging during initialization
            if not self.is_initializing:
                # Track change by source and log with nice format
                src = "E" if source == "EVENT" else "P"
                if source == "EVENT":
                    self.change_tracker['events_received'] += 1
                else:
                    self.change_tracker['polling_changes'] += 1

                # Format log message: show zone name, only add device detail if not zone leader
                if is_zone_leader:
                    # Zone leader - just show zone name
                    logger.info(f"[{src}] {zone_name} | {char_name}: {last_value} -> {value}")
                else:
                    # Non-leader device - show zone + device to distinguish multiple devices
                    logger.info(f"[{src}] {zone_name} ({device_name}) | {char_name}: {last_value} -> {value}")

            # Send to event stream for clients (always, even during init)
            # Don't send raw characteristic events anymore - we'll send aggregated state changes
            
            # Broadcast aggregated state change for relevant characteristics
            if char_name in ['TargetTemperature', 'CurrentTemperature', 'TargetHeatingCoolingState', 
                            'CurrentHeatingCoolingState', 'CurrentRelativeHumidity', 'ValvePosition']:
                await self.broadcast_state_change(device_id, zone_name)
            
        except Exception as e:
            logger.error(f"Error handling unified change: {e}")
    
    async def broadcast_event(self, event_data):
        """Broadcast change event to all connected SSE clients."""
        try:
            event_json = json.dumps(event_data)
            event_message = f"data: {event_json}\n\n"
            
            # Send to all connected event listeners
            disconnected_listeners = []
            for listener in self.event_listeners:
                try:
                    await listener.put(event_message)
                except:
                    disconnected_listeners.append(listener)
            
            # Remove disconnected listeners
            for listener in disconnected_listeners:
                if listener in self.event_listeners:
                    self.event_listeners.remove(listener)
                    
        except Exception as e:
            logger.error(f"Error broadcasting event: {e}")
    
    def _celsius_to_fahrenheit(self, celsius: float) -> float:
        """Convert Celsius to Fahrenheit."""
        if celsius is None:
            return None
        return round(celsius * 9/5 + 32, 1)
    
    def _build_device_state(self, device_id: int) -> dict:
        """Build a standardized device state dictionary."""
        state = self.state_manager.get_current_state(device_id)
        device_info = self.state_manager.device_info_cache.get(device_id, {})
        
        cur_temp_c = state.get('current_temperature')
        target_temp_c = state.get('target_temperature')
        
        # Determine battery_low from Cloud API battery_state (cached, no extra DB query)
        battery_state = device_info.get('battery_state')
        battery_low = battery_state is not None and battery_state != 'NORMAL'
        
        return {
            'cur_temp_c': cur_temp_c,
            'cur_temp_f': self._celsius_to_fahrenheit(cur_temp_c) if cur_temp_c is not None else None,
            'hum_perc': state.get('humidity'),
            'target_temp_c': target_temp_c,
            'target_temp_f': self._celsius_to_fahrenheit(target_temp_c) if target_temp_c is not None else None,
            'mode': state.get('target_heating_cooling_state', 0),  # 0=Off, 1=Heat, 2=Cool, 3=Auto
            'cur_heating': 1 if state.get('current_heating_cooling_state') == 1 else 0,
            'valve_position': state.get('valve_position'),
            'battery_low': battery_low,
        }
    
    async def broadcast_state_change(self, device_id: int, zone_name: str):
        """
        Broadcast device and zone state change events.
        
        Sends standardized state updates for both the device and its zone (if assigned).
        """
        try:
            # Get device info
            device_info = self.state_manager.get_device_info(device_id)
            if not device_info:
                return
            
            serial = device_info.get('serial_number')
            is_zone_leader = device_info.get('is_zone_leader', False)
            is_circuit_driver = device_info.get('is_circuit_driver', False)
            
            # Build device state
            device_state = self._build_device_state(device_id)
            
            # Broadcast device state change
            device_event = {
                'type': 'device',
                'device_id': device_id,
                'serial': serial,
                'zone_name': zone_name,
                'state': device_state,
                'timestamp': time.time()
            }
            await self.broadcast_event(device_event)
            
            # Also broadcast zone state if device is assigned to a zone
            conn = sqlite3.connect(self.state_manager.db_path)
            cursor = conn.execute("""
                SELECT z.zone_id, z.name, z.leader_device_id, d.is_circuit_driver
                FROM devices d
                JOIN zones z ON d.zone_id = z.zone_id
                LEFT JOIN devices leader ON z.leader_device_id = leader.device_id
                WHERE d.device_id = ?
            """, (device_id,))
            row = cursor.fetchone()
            
            if row:
                zone_id, zone_name, leader_device_id, leader_is_circuit = row
                
                # Get leader state for zone
                if leader_device_id:
                    leader_state = self._build_device_state(leader_device_id)
                    
                    # Build zone state using zone logic
                    zone_state = {
                        'cur_temp_c': leader_state['cur_temp_c'],
                        'cur_temp_f': leader_state['cur_temp_f'],
                        'hum_perc': leader_state['hum_perc'],
                        'target_temp_c': leader_state['target_temp_c'],
                        'target_temp_f': leader_state['target_temp_f'],
                        'mode': 0,
                        'cur_heating': 0
                    }
                    
                    # Apply circuit driver logic for heating states
                    if leader_is_circuit:
                        # Circuit driver - check radiator valves in zone
                        valve_cursor = conn.execute("""
                            SELECT device_id FROM devices 
                            WHERE zone_id = ? AND is_circuit_driver = 0
                        """, (zone_id,))
                        
                        for (valve_id,) in valve_cursor.fetchall():
                            valve_state = self._build_device_state(valve_id)
                            if valve_state['mode'] == 1:
                                zone_state['mode'] = 1
                            if valve_state['cur_heating'] == 1:
                                zone_state['cur_heating'] = 1
                    else:
                        # Regular device - use leader state
                        zone_state['mode'] = leader_state['mode']
                        zone_state['cur_heating'] = leader_state['cur_heating']
                    
                    # Broadcast zone state change
                    zone_event = {
                        'type': 'zone',
                        'zone_id': zone_id,
                        'zone_name': zone_name,
                        'state': zone_state,
                        'timestamp': time.time()
                    }
                    await self.broadcast_event(zone_event)
            
            conn.close()
            
        except Exception as e:
            logger.debug(f"Error broadcasting state change: {e}")
    
    async def setup_polling_system(self):
        """Setup polling system for comparison with events."""
        try:
            # Find all interesting characteristics for polling (not just temperature)
            self.poll_chars = []
            
            for accessory in self.accessories_cache:
                aid = accessory["aid"]
                for service in accessory["services"]:
                    for char in service["characteristics"]:
                        perms = char.get("perms", [])
                        
                        # Poll the characteristics that support polling and events
                        if "ev" in perms and "pr" in perms:
                            iid = char["iid"]
                            self.poll_chars.append((aid, iid))                            
                            
            if self.poll_chars:
                logger.info(f"Found {len(self.poll_chars)} characteristics for polling")
                # Store for the polling loop to use
                self.monitored_characteristics = self.poll_chars
                # Start background polling task and track it
                task = asyncio.create_task(self.background_polling_loop())
                self.background_tasks.append(task)
                logger.info("Background polling system started")
            else:
                logger.warning("No characteristics found for polling")
                
        except Exception as e:
            logger.warning(f"Failed to setup polling system: {e}")
    
    async def background_polling_loop(self):
        """Background task that polls all monitored characteristics.
        
        Uses intelligent polling intervals:
        - Fast poll (60s) for characteristics that have events but might not fire reliably
        - Slow poll (120s) as safety net for everything else
        """
        fast_poll_interval = 60  # 1 minute for priority characteristics
        slow_poll_interval = 120  # 2 minutes for everything
        
        # Track characteristics that need faster polling (like humidity)
        priority_chars = set()
        
        # Identify priority characteristics (humidity, battery, etc.)
        for aid, iid in self.monitored_characteristics:
            char_key = (aid, iid)
            char_name = self.characteristic_map.get(char_key, "")
            
            # Add humidity to priority list
            if 'humidity' in char_name.lower():
                priority_chars.add((aid, iid))
        
        if priority_chars:
            logger.info(f"Fast polling ({fast_poll_interval}s) for {len(priority_chars)} characteristics")
            logger.info(f"Normal polling ({slow_poll_interval}s) for {len(self.monitored_characteristics) - len(priority_chars)} characteristics")
        
        last_fast_poll = 0
        last_slow_poll = 0
        
        while not self.is_shutting_down:
            try:
                current_time = time.time()
                await asyncio.sleep(10)  # Check every 10 seconds
                
                if not self.pairing or not hasattr(self, 'monitored_characteristics'):
                    continue
                
                # Fast poll priority characteristics
                if current_time - last_fast_poll >= fast_poll_interval and priority_chars:
                    logger.debug(f"Fast polling {len(priority_chars)} priority characteristics")
                    await self._poll_characteristics(list(priority_chars), "FAST-POLL")
                    last_fast_poll = current_time
                
                # Slow poll all characteristics
                if current_time - last_slow_poll >= slow_poll_interval:
                    logger.debug(f"Slow polling {len(self.monitored_characteristics)} characteristics")
                    await self._poll_characteristics(self.monitored_characteristics, "POLLING")
                    last_slow_poll = current_time
                        
            except Exception as e:
                logger.error(f"Background polling error: {e}")
                await asyncio.sleep(5)  # Short delay before retrying
    
    async def _poll_characteristics(self, char_list, source="POLLING"):
        """Poll a list of characteristics and process changes."""
        # Poll in batches to avoid overwhelming the device
        batch_size = 15
        
        for i in range(0, len(char_list), batch_size):
            batch = char_list[i:i+batch_size]
            
            try:
                results = await self.pairing.get_characteristics(batch)
                
                for aid, iid in batch:
                    if (aid, iid) in results:
                        char_data = results[(aid, iid)]
                        value = char_data.get('value')
                        
                        # Create proper update_data format for unified change handler
                        update_data = {
                            'value': value
                        }
                        
                        # Use the unified change handler
                        await self.handle_change(aid, iid, update_data, source)
                        
            except Exception as e:
                logger.error(f"Error polling batch: {e}")
    
    async def handle_homekit_event(self, event_data):
        """Handle incoming HomeKit events and update device states."""
        try:
            # Update device states from HomeKit events (if any events still come through)
            aid = event_data.get('aid')
            iid = event_data.get('iid') 
            value = event_data.get('value')
            
            if aid and iid and value is not None:
                self.device_states[str(aid)][str(iid)] = {
                    'value': value,
                    'timestamp': time.time()
                }
                
                # Notify event listeners (for SSE)
                for queue in self.event_listeners:
                    try:
                        await queue.put(event_data)
                    except:
                        pass  # Queue might be closed
                        
                logger.debug(f"Updated device state from event: aid={aid}, iid={iid}, value={value}")
                
        except Exception as e:
            logger.error(f"Error handling HomeKit event: {e}")
    
    async def set_device_characteristics(self, device_id: int, char_updates: Dict[str, Any]) -> bool:
        """
        Set characteristics for a device.
        
        Args:
            device_id: Database device ID
            char_updates: Dict mapping characteristic UUIDs to values
                         e.g., {'target_temperature': 21.0, 'target_heating_cooling_state': 1}
        
        Returns:
            True if successful
            
        Raises:
            ValueError if device not found or characteristics not writable
        """
        if not self.pairing:
            raise ValueError("Bridge not connected")
        
        # Get device info from in-memory cache
        device_info = self.state_manager.get_device_info(device_id)
        if not device_info:
            raise ValueError(f"Device {device_id} not found")
        
        aid = device_info.get('aid')
        if not aid:
            # Cache might be stale, try reloading
            logger.info(f"Device {device_id} has no aid in cache, reloading device cache...")
            self.state_manager._load_device_cache()
            device_info = self.state_manager.get_device_info(device_id)
            aid = device_info.get('aid') if device_info else None
            
            if not aid:
                raise ValueError(f"Device {device_id} has no HomeKit accessory ID (aid)")
        
        # Map characteristic names to UUIDs
        char_uuid_map = {
            'target_temperature': DeviceStateManager.CHAR_TARGET_TEMPERATURE,
            'target_heating_cooling_state': DeviceStateManager.CHAR_TARGET_HEATING_COOLING,
            'target_humidity': DeviceStateManager.CHAR_TARGET_HUMIDITY,
        }
        
        # Find the characteristic IIDs in the accessory
        characteristics_to_set = []
        for char_name, value in char_updates.items():
            char_uuid = char_uuid_map.get(char_name)
            if not char_uuid:
                logger.warning(f"Unknown characteristic: {char_name}")
                continue
            
            # Find the IID for this characteristic
            for acc in self.accessories_cache:
                if acc.get('aid') == aid:
                    for service in acc.get('services', []):
                        for char in service.get('characteristics', []):
                            if char.get('type').lower() == char_uuid.lower():
                                iid = char.get('iid')
                                if iid:
                                    characteristics_to_set.append((aid, iid, value))
                                    logger.info(f"Setting {char_name} on device {device_id} (aid={aid}, iid={iid}) to {value}")
                                break
        
        if not characteristics_to_set:
            raise ValueError("No valid characteristics to set")
        
        # Set the characteristics
        logger.info(f"Sending to HomeKit: {characteristics_to_set}")
        await self.pairing.put_characteristics(characteristics_to_set)
        logger.info(f"Successfully set characteristics on device {device_id}")
        return True
