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

"""Device state tracking, history, and change detection."""

import datetime
import logging
import sqlite3
import time
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

class DeviceStateManager:
    """Manages device state tracking, history, and change detection."""

    # HomeKit characteristic UUIDs we care about
    # Temperature & HVAC
    CHAR_CURRENT_TEMPERATURE = '00000011-0000-1000-8000-0026bb765291'
    CHAR_TARGET_TEMPERATURE = '00000035-0000-1000-8000-0026bb765291'
    CHAR_CURRENT_HEATING_COOLING = '0000000f-0000-1000-8000-0026bb765291'
    CHAR_TARGET_HEATING_COOLING = '00000033-0000-1000-8000-0026bb765291'
    CHAR_HEATING_THRESHOLD = '00000012-0000-1000-8000-0026bb765291'
    CHAR_COOLING_THRESHOLD = '0000000d-0000-1000-8000-0026bb765291'
    CHAR_TEMP_DISPLAY_UNITS = '00000036-0000-1000-8000-0026bb765291'

    # Humidity
    CHAR_CURRENT_HUMIDITY = '00000010-0000-1000-8000-0026bb765291'
    CHAR_TARGET_HUMIDITY = '00000034-0000-1000-8000-0026bb765291'

    # Battery
    CHAR_BATTERY_LEVEL = '00000068-0000-1000-8000-0026bb765291'
    CHAR_STATUS_LOW_BATTERY = '00000079-0000-1000-8000-0026bb765291'

    # Active state (for heaters, coolers, etc.)
    CHAR_ACTIVE = '000000b0-0000-1000-8000-0026bb765291'

    # Valve position (for radiator controls)
    CHAR_VALVE_POSITION = '0000004f-0000-1000-8000-0026bb765291'

    # Water heater specific
    CHAR_CURRENT_WATER_TEMPERATURE = '00000011-0000-1000-8000-0026bb765291'  # Same as current temp
    CHAR_TARGET_WATER_TEMPERATURE = '00000035-0000-1000-8000-0026bb765291'  # Same as target temp

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.device_id_cache: Dict[str, int] = {}  # serial_number -> device_id
        self.aid_to_device_id: Dict[int, int] = {}  # aid -> device_id (bidirectional mapping)
        self.device_info_cache: Dict[int, Dict[str, Any]] = {}  # device_id -> {name, zone_name, serial, aid, etc}
        self.zone_cache: Dict[int, Dict[str, Any]] = {}  # zone_id -> {name, leader_device_id, etc}
        self.current_state: Dict[int, Dict[str, Any]] = {}  # device_id -> current state
        self.last_saved_bucket: Dict[int, str] = {}  # device_id -> last saved bucket
        self.bucket_state_snapshot: Dict[int, Dict[str, Any]] = {}  # device_id -> state when bucket was saved
        
        # Optimistic update tracking (for UI responsiveness)
        self.optimistic_state: Dict[int, Dict[str, Any]] = {}  # device_id -> predicted state changes
        self.optimistic_timestamps: Dict[int, float] = {}  # device_id -> timestamp when prediction was made
        self.optimistic_timeout = 10.0  # Revert predictions after 10 seconds if no real update

        # Ensure DB schema and migrations are applied before using DB. All
        # schema updates are centralized in `tado_local.database.ensure_schema_and_migrate`.
        from .database import ensure_schema_and_migrate
        ensure_schema_and_migrate(self.db_path)

        # Load caches and latest state (schema guaranteed by central migrator)
        self._load_device_cache()
        self._load_zone_cache()
        self._load_latest_state_from_db()

        # Note: schema creation and migrations are centralized in tado_local.database.ensure_schema_and_migrate

    def _load_device_cache(self):
        """Load device ID mappings and info from database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
         SELECT d.device_id, d.serial_number, d.aid, d.name, d.device_type,
             d.zone_id, z.name as zone_name, d.is_zone_leader, d.is_circuit_driver, d.battery_state
         FROM devices d
         LEFT JOIN zones z ON d.zone_id = z.zone_id
        """)
        for device_id, serial_number, aid, name, device_type, zone_id, zone_name, is_zone_leader, is_circuit_driver, battery_state in cursor.fetchall():
            self.device_id_cache[serial_number] = device_id
            if aid:
                self.aid_to_device_id[aid] = device_id
            self.device_info_cache[device_id] = {
                'serial_number': serial_number,
                'aid': aid,
                'name': name,
                'device_type': device_type,
                'zone_id': zone_id,
                'zone_name': zone_name,
                'is_zone_leader': bool(is_zone_leader),
                'is_circuit_driver': bool(is_circuit_driver),
                'battery_state': battery_state  # From Cloud API: "NORMAL", "LOW", etc.
            }
        conn.close()
        logger.info(f"Loaded {len(self.device_id_cache)} devices from cache")

    def _load_zone_cache(self):
        """Load zone information into memory cache."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT z.zone_id, z.name, z.leader_device_id, z.order_id,
                   d.serial_number as leader_serial, d.device_type as leader_type,
                   d.is_circuit_driver, z.uuid
            FROM zones z
            LEFT JOIN devices d ON z.leader_device_id = d.device_id
            ORDER BY z.order_id, z.name
        """)

        for zone_id, name, leader_device_id, order_id, leader_serial, leader_type, is_circuit_driver, uuid_val in cursor.fetchall():
            self.zone_cache[zone_id] = {
                'zone_id': zone_id,
                'name': name,
                'leader_device_id': leader_device_id,
                'order_id': order_id,
                'leader_serial': leader_serial,
                'leader_type': leader_type,
                'is_circuit_driver': bool(is_circuit_driver),
                'uuid': uuid_val
            }

        conn.close()
        logger.info(f"Loaded {len(self.zone_cache)} zones from cache")

    def _load_latest_state_from_db(self):
        """Load the most recent state for each device from the database to avoid duplicate saves on startup."""
        conn = sqlite3.connect(self.db_path)

        # Get the most recent state for each device
        cursor = conn.execute("""
            SELECT device_id, timestamp_bucket,
                   current_temperature, target_temperature,
                   current_heating_cooling_state, target_heating_cooling_state,
                   heating_threshold_temperature, cooling_threshold_temperature,
                   temperature_display_units, battery_level, status_low_battery,
                   humidity, target_humidity, active_state, valve_position
            FROM device_state_history
            WHERE (device_id, timestamp_bucket) IN (
                SELECT device_id, MAX(timestamp_bucket)
                FROM device_state_history
                GROUP BY device_id
            )
        """)

        for row in cursor.fetchall():
            device_id = row[0]
            timestamp_bucket = row[1]

            # Populate current_state with the last known values
            self.current_state[device_id] = {
                'current_temperature': row[2],
                'target_temperature': row[3],
                'current_heating_cooling_state': row[4],
                'target_heating_cooling_state': row[5],
                'heating_threshold_temperature': row[6],
                'cooling_threshold_temperature': row[7],
                'temperature_display_units': row[8],
                'battery_level': row[9],
                'status_low_battery': row[10],
                'humidity': row[11],
                'target_humidity': row[12],
                'active_state': row[13],
                'valve_position': row[14],
            }

            # Set the last saved bucket
            self.last_saved_bucket[device_id] = timestamp_bucket

            # Set the snapshot to match what we just loaded
            self.bucket_state_snapshot[device_id] = self.current_state[device_id].copy()

        conn.close()
        logger.info(f"Loaded latest state for {len(self.current_state)} devices from database")

    def get_device_info(self, device_id: int) -> Dict[str, Any]:
        """Get cached device info including zone name, aid, etc."""
        return self.device_info_cache.get(device_id, {})

    def get_device_id_by_aid(self, aid: int) -> Optional[int]:
        """Get device_id from HomeKit accessory ID (aid)."""
        return self.aid_to_device_id.get(aid)

    def get_or_create_device(self, serial_number: str, aid: int, accessory_data: dict) -> int:
        """Get or create device ID for a serial number, updating aid if needed."""
        if serial_number in self.device_id_cache:
            device_id = self.device_id_cache[serial_number]

            # Update aid if it's not set or has changed
            device_info = self.device_info_cache.get(device_id, {})
            current_aid = device_info.get('aid')

            if current_aid != aid:
                logger.info(f"Updating aid for device {device_id} ({serial_number}): {current_aid} -> {aid}")
                conn = sqlite3.connect(self.db_path)
                conn.execute("""
                    UPDATE devices SET aid = ? WHERE device_id = ?
                """, (aid, device_id))
                conn.commit()
                conn.close()

                # Update caches
                if aid:
                    self.aid_to_device_id[aid] = device_id
                if device_info:
                    device_info['aid'] = aid

            return device_id

        # Extract device info from accessory data
        device_type = "unknown"
        name = None
        model = None
        manufacturer = None

        for service in accessory_data.get('services', []):
            # AccessoryInformation service
            if service.get('type') == '0000003e-0000-1000-8000-0026bb765291':
                for char in service.get('characteristics', []):
                    char_type = char.get('type', '').lower()
                    value = char.get('value')
                    if char_type == '00000023-0000-1000-8000-0026bb765291':  # Name
                        name = value
                    elif char_type == '00000021-0000-1000-8000-0026bb765291':  # Model
                        model = value
                    elif char_type == '00000020-0000-1000-8000-0026bb765291':  # Manufacturer
                        manufacturer = value

            # Determine device type from services
            service_type = service.get('type', '').lower()
            if service_type == '0000004a-0000-1000-8000-0026bb765291':
                device_type = "thermostat"
            elif service_type == '0000008a-0000-1000-8000-0026bb765291':
                device_type = "temperature_sensor"
            elif service_type == '00000082-0000-1000-8000-0026bb765291':
                device_type = "humidity_sensor"

        # If device type still unknown, detect from serial number prefix
        # Based on Tado Cloud API device types: IB01, RU02, VA02, etc.
        if device_type == "unknown" and serial_number:
            prefix = serial_number[:2].upper()
            if prefix == "IB":
                device_type = "internet_bridge"
            elif prefix == "RU":
                device_type = "thermostat"  # Room Unit / Smart Thermostat
            elif prefix == "VA":
                device_type = "radiator_valve"  # Smart Radiator Thermostat
            elif prefix == "WR":
                device_type = "wireless_receiver"  # Extension Kit

        # Create device entry
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            INSERT INTO devices (serial_number, aid, device_type, name, model, manufacturer)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (serial_number, aid, device_type, name, model, manufacturer))
        device_id = cursor.lastrowid
        conn.commit()

        # Get zone_name and is_zone_leader if device has a zone assigned
        zone_cursor = conn.execute("""
            SELECT z.name, d.is_zone_leader
            FROM devices d
            LEFT JOIN zones z ON d.zone_id = z.zone_id
            WHERE d.device_id = ?
        """, (device_id,))
        zone_row = zone_cursor.fetchone()
        zone_name = zone_row[0] if zone_row else None
        is_zone_leader = bool(zone_row[1]) if zone_row and zone_row[1] is not None else False

        conn.close()

        # Update both caches
        self.device_id_cache[serial_number] = device_id
        if aid:
            self.aid_to_device_id[aid] = device_id
        self.device_info_cache[device_id] = {
            'serial_number': serial_number,
            'aid': aid,
            'name': name,
            'device_type': device_type,
            'zone_name': zone_name,
            'is_zone_leader': is_zone_leader
        }
        logger.info(f"Created device {device_id} for {serial_number} ({name})")

        return device_id

    def update_device_characteristic(self, device_id: int, char_type: str, value: Any, timestamp: float):
        """Update a single characteristic for a device."""
        if device_id not in self.current_state:
            self.current_state[device_id] = {}

        # Map characteristic to state field
        char_mapping = {
            self.CHAR_CURRENT_TEMPERATURE: 'current_temperature',
            self.CHAR_TARGET_TEMPERATURE: 'target_temperature',
            self.CHAR_CURRENT_HEATING_COOLING: 'current_heating_cooling_state',
            self.CHAR_TARGET_HEATING_COOLING: 'target_heating_cooling_state',
            self.CHAR_HEATING_THRESHOLD: 'heating_threshold_temperature',
            self.CHAR_COOLING_THRESHOLD: 'cooling_threshold_temperature',
            self.CHAR_TEMP_DISPLAY_UNITS: 'temperature_display_units',
            self.CHAR_BATTERY_LEVEL: 'battery_level',
            self.CHAR_STATUS_LOW_BATTERY: 'status_low_battery',
            self.CHAR_CURRENT_HUMIDITY: 'humidity',
            self.CHAR_TARGET_HUMIDITY: 'target_humidity',
            self.CHAR_ACTIVE: 'active_state',
            self.CHAR_VALVE_POSITION: 'valve_position',
        }

        field_name = char_mapping.get(char_type.lower())
        if field_name:
            old_value = self.current_state[device_id].get(field_name)

            # Only update if value actually changed
            if old_value == value:
                return None, None, None  # No change

            self.current_state[device_id][field_name] = value
            self.current_state[device_id]['last_update'] = timestamp

            # Check if we need to save to history
            current_bucket = self._get_timestamp_bucket(timestamp)
            last_bucket = self.last_saved_bucket.get(device_id)

            # Save if: new bucket OR state changed within same bucket
            if last_bucket != current_bucket or self._has_state_changed(device_id):
                self._save_to_history(device_id, timestamp)

            return field_name, old_value, value

        return None, None, None

    def _has_state_changed(self, device_id: int) -> bool:
        """Check if current state differs from last saved snapshot."""
        if device_id not in self.bucket_state_snapshot:
            return True  # No snapshot yet, definitely changed

        current = self.current_state.get(device_id, {})
        snapshot = self.bucket_state_snapshot.get(device_id, {})

        # Compare only the data fields, not metadata like 'last_update'
        data_fields = [
            'current_temperature', 'target_temperature',
            'current_heating_cooling_state', 'target_heating_cooling_state',
            'heating_threshold_temperature', 'cooling_threshold_temperature',
            'temperature_display_units', 'battery_level', 'status_low_battery',
            'humidity', 'target_humidity', 'active_state', 'valve_position'
        ]

        for field in data_fields:
            if current.get(field) != snapshot.get(field):
                return True

        return False

    def _get_timestamp_bucket(self, timestamp: float) -> str:
        """Convert timestamp to 10-second bucket (format: YYYYMMDDHHMMSSx where x is 0-5)."""
        dt = datetime.datetime.fromtimestamp(timestamp)
        # Round down to 10-second interval
        second = (dt.second // 10) * 10
        return dt.strftime(f'%Y%m%d%H%M{second:02d}')

    def _save_to_history(self, device_id: int, timestamp: float):
        """Save current state to history table using 10-second bucket."""
        if device_id not in self.current_state:
            return

        state = self.current_state[device_id]
        bucket = self._get_timestamp_bucket(timestamp)

        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO device_state_history (
                device_id, timestamp_bucket,
                current_temperature, target_temperature,
                current_heating_cooling_state, target_heating_cooling_state,
                heating_threshold_temperature, cooling_threshold_temperature,
                temperature_display_units, battery_level, status_low_battery,
                humidity, target_humidity, active_state, valve_position
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id, timestamp_bucket) DO UPDATE SET
                current_temperature = COALESCE(excluded.current_temperature, current_temperature),
                target_temperature = COALESCE(excluded.target_temperature, target_temperature),
                current_heating_cooling_state = COALESCE(excluded.current_heating_cooling_state, current_heating_cooling_state),
                target_heating_cooling_state = COALESCE(excluded.target_heating_cooling_state, target_heating_cooling_state),
                heating_threshold_temperature = COALESCE(excluded.heating_threshold_temperature, heating_threshold_temperature),
                cooling_threshold_temperature = COALESCE(excluded.cooling_threshold_temperature, cooling_threshold_temperature),
                temperature_display_units = COALESCE(excluded.temperature_display_units, temperature_display_units),
                battery_level = COALESCE(excluded.battery_level, battery_level),
                status_low_battery = COALESCE(excluded.status_low_battery, status_low_battery),
                humidity = COALESCE(excluded.humidity, humidity),
                target_humidity = COALESCE(excluded.target_humidity, target_humidity),
                active_state = COALESCE(excluded.active_state, active_state),
                valve_position = COALESCE(excluded.valve_position, valve_position),
                updated_at = CURRENT_TIMESTAMP
        """, (
            device_id, bucket,
            state.get('current_temperature'),
            state.get('target_temperature'),
            state.get('current_heating_cooling_state'),
            state.get('target_heating_cooling_state'),
            state.get('heating_threshold_temperature'),
            state.get('cooling_threshold_temperature'),
            state.get('temperature_display_units'),
            state.get('battery_level'),
            state.get('status_low_battery'),
            state.get('humidity'),
            state.get('target_humidity'),
            state.get('active_state'),
            state.get('valve_position')
        ))
        conn.commit()
        conn.close()

        # Update tracking: remember this bucket and state snapshot
        self.last_saved_bucket[device_id] = bucket
        self.bucket_state_snapshot[device_id] = {
            'current_temperature': state.get('current_temperature'),
            'target_temperature': state.get('target_temperature'),
            'current_heating_cooling_state': state.get('current_heating_cooling_state'),
            'target_heating_cooling_state': state.get('target_heating_cooling_state'),
            'heating_threshold_temperature': state.get('heating_threshold_temperature'),
            'cooling_threshold_temperature': state.get('cooling_threshold_temperature'),
            'temperature_display_units': state.get('temperature_display_units'),
            'battery_level': state.get('battery_level'),
            'status_low_battery': state.get('status_low_battery'),
            'humidity': state.get('humidity'),
            'target_humidity': state.get('target_humidity'),
            'active_state': state.get('active_state'),
            'valve_position': state.get('valve_position'),
        }

        logger.debug(f"Saved device {device_id} state to history bucket {bucket}")

    def get_device_history(self, device_id: int, start_time: float = None, end_time: float = None, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get device state history with standardized format."""
        conn = sqlite3.connect(self.db_path)

        query = """
            SELECT current_temperature, target_temperature,
                   current_heating_cooling_state, target_heating_cooling_state,
                   heating_threshold_temperature, cooling_threshold_temperature,
                   temperature_display_units, battery_level, status_low_battery, humidity,
                   valve_position, updated_at
            FROM device_state_history
            WHERE device_id = ?
        """
        params = [device_id]

        if start_time:
            query += " AND timestamp_bucket >= ?"
            params.append(self._get_timestamp_bucket(start_time))

        if end_time:
            query += " AND timestamp_bucket <= ?"
            params.append(self._get_timestamp_bucket(end_time))

        query += " ORDER BY timestamp_bucket DESC LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)

        cursor = conn.execute(query, params)

        history = []
        for row in cursor.fetchall():
            cur_temp_c = row[0]
            target_temp_c = row[1]

            # Build standardized state format (same as /devices, /zones, /events)
            record = {
                'state': {
                    'cur_temp_c': cur_temp_c,
                    'cur_temp_f': round(cur_temp_c * 9/5 + 32, 1) if cur_temp_c is not None else None,
                    'target_temp_c': target_temp_c,
                    'target_temp_f': round(target_temp_c * 9/5 + 32, 1) if target_temp_c is not None else None,
                    'mode': row[3],  # target_heating_cooling_state
                    'cur_heating': row[2],  # current_heating_cooling_state
                    'hum_perc': row[9],  # humidity
                    'valve_position': row[10],  # valve_position
                    'battery_low': bool(row[8]) if row[8] is not None else False,  # status_low_battery
                },
                'timestamp': row[11]  # updated_at (last update time in bucket)
            }
            history.append(record)

        conn.close()

        return history

    def get_current_state(self, device_id: int = None) -> Dict:
        """Get current state for one or all devices."""
        if device_id is not None:
            return self.current_state.get(device_id, {})
        return self.current_state

    def set_optimistic_state(self, device_id: int, state_changes: Dict[str, Any]):
        """
        Set optimistic state prediction for a device.
        
        This allows immediate UI feedback before HomeKit confirms the change.
        Predictions automatically expire after self.optimistic_timeout seconds.
        
        Args:
            device_id: Device to update
            state_changes: Dict of state keys to predicted values
        """
        self.optimistic_state[device_id] = state_changes.copy()
        self.optimistic_timestamps[device_id] = time.time()
        logger.debug(f"Set optimistic state for device {device_id}: {state_changes}")

    def clear_optimistic_state(self, device_id: int):
        """Clear optimistic predictions for a device (called when real state arrives)."""
        if device_id in self.optimistic_state:
            # Check if we need to log a mismatch (optimistic state was overridden)
            # This would indicate the device rejected or modified our change
            predicted = self.optimistic_state[device_id]
            actual = self.current_state.get(device_id, {})
            
            mismatches = []
            for key, predicted_value in predicted.items():
                actual_value = actual.get(key)
                if actual_value is not None and actual_value != predicted_value:
                    mismatches.append(f"{key}: predicted={predicted_value}, actual={actual_value}")
            
            if mismatches:
                logger.info(f"Device {device_id}: Optimistic state was overridden by device - {', '.join(mismatches)}")
            
            del self.optimistic_state[device_id]
            del self.optimistic_timestamps[device_id]
            logger.debug(f"Cleared optimistic state for device {device_id}")

    def get_state_with_optimistic(self, device_id: int) -> Dict:
        """
        Get device state with optimistic predictions overlaid.
        
        If optimistic predictions exist and haven't expired, they override
        the real state values. Expired predictions are automatically cleared.
        
        Returns:
            Dict with current state + active optimistic overrides
        """
        # Start with real state
        state = self.current_state.get(device_id, {}).copy()
        
        # Check for optimistic overrides
        if device_id in self.optimistic_state:
            prediction_time = self.optimistic_timestamps[device_id]
            age = time.time() - prediction_time
            
            if age > self.optimistic_timeout:
                # Prediction expired - clear it
                logger.debug(f"Optimistic state for device {device_id} expired after {age:.1f}s")
                self.clear_optimistic_state(device_id)
            else:
                # Apply optimistic overrides
                optimistic = self.optimistic_state[device_id]
                state.update(optimistic)
                logger.debug(f"Applied optimistic state to device {device_id} (age: {age:.1f}s)")
        
        return state

    def get_all_devices(self) -> List[Dict]:
        """Get all registered devices."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT device_id, serial_number, aid, device_type, name, model, manufacturer,
                   first_seen, last_seen
            FROM devices
            ORDER BY device_id
        """)
        columns = [desc[0] for desc in cursor.description]
        devices = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return devices
