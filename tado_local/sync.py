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

"""Synchronize Tado Cloud API data to local database."""

import logging
import sqlite3
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


def normalize_device_type(tado_device_type: str) -> str:
    """
    Normalize Tado device type codes to friendly names.

    Args:
        tado_device_type: Tado device type code (e.g., "IB01", "RU02", "VA02")

    Returns:
        Normalized device type string
    """
    if not tado_device_type:
        return "unknown"

    # Map Tado device type codes to friendly names
    type_map = {
        "IB01": "internet_bridge",
        "RU01": "thermostat",
        "RU02": "thermostat",
        "VA01": "radiator_valve",
        "VA02": "radiator_valve",
        "WR01": "wireless_receiver",
        "WR02": "wireless_receiver",
        "SU02": "smart_ac_control",
    }

    return type_map.get(tado_device_type.upper(), tado_device_type.lower())


class TadoCloudSync:
    """Syncs Tado Cloud API data to local database."""

    def __init__(self, db_path: str):
        """
        Initialize sync manager.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path

    def sync_home(self, home_data: Dict[str, Any]) -> bool:
        """
        Sync home information to database.

        Args:
            home_data: Home data from Tado Cloud API

        Returns:
            True if successful
        """
        try:
            conn = sqlite3.connect(self.db_path)

            home_id = home_data['id']
            name = home_data['name']
            timezone = home_data.get('dateTimeZone')
            temp_unit = home_data.get('temperatureUnit')

            conn.execute("""
                INSERT OR REPLACE INTO tado_homes
                (tado_home_id, name, timezone, temperature_unit, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (home_id, name, timezone, temp_unit))

            conn.commit()
            conn.close()

            logger.info(f"[OK] Synced home: {name} (ID: {home_id})")
            return True

        except Exception as e:
            logger.error(f"Failed to sync home: {e}")
            return False

    def sync_zones(self, zones_data: List[Dict[str, Any]], home_id: int) -> bool:
        """
        Sync zones from Tado Cloud API to database.

        Creates or updates zones and their device mappings based on Cloud API data.
        Maintains separate internal zone_id while tracking tado_zone_id for mapping.
        Preserves the zone order from the API (user-configured order in Tado app).

        Args:
            zones_data: List of zone dicts from Tado Cloud API
            home_id: Tado home ID

        Returns:
            True if successful
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            synced_zones = 0
            synced_devices = 0

            for order_index, zone in enumerate(zones_data):
                tado_zone_id = zone['id']
                zone_name = zone['name']
                zone_type = zone.get('type', 'HEATING')

                # Check if zone already exists
                cursor.execute("""
                    SELECT zone_id FROM zones
                    WHERE tado_home_id = ? AND tado_zone_id = ?
                """, (home_id, tado_zone_id))
                existing = cursor.fetchone()

                if existing:
                    # Update existing zone
                    zone_id = existing[0]
                    cursor.execute("""
                        UPDATE zones
                        SET name = ?, zone_type = ?, order_id = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE zone_id = ?
                    """, (zone_name, zone_type, order_index, zone_id))
                    logger.debug(f"Updated zone {zone_id}: {zone_name} (Tado ID: {tado_zone_id}, order: {order_index})")
                else:
                    # Insert new zone
                    cursor.execute("""
                        INSERT INTO zones
                        (tado_zone_id, tado_home_id, name, zone_type, order_id, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """, (tado_zone_id, home_id, zone_name, zone_type, order_index))
                    zone_id = cursor.lastrowid
                    logger.info(f"Created zone {zone_id}: {zone_name} (Tado ID: {tado_zone_id}, order: {order_index})")

                synced_zones += 1

                # Process devices in this zone
                for device in zone.get('devices', []):
                    serial = device['serialNo']
                    device_type = device['deviceType']
                    firmware = device.get('currentFwVersion')
                    battery_state = device.get('batteryState')
                    duties = device.get('duties', [])

                    # Parse duties
                    is_leader = 'ZONE_LEADER' in duties
                    is_circuit_driver = 'CIRCUIT_DRIVER' in duties
                    is_zone_driver = 'ZONE_DRIVER' in duties
                    duties_str = ','.join(duties) if duties else None

                    # Check if device exists
                    cursor.execute("""
                        SELECT device_id FROM devices WHERE serial_number = ?
                    """, (serial,))
                    existing_device = cursor.fetchone()

                    if existing_device:
                        # Update existing device - don't overwrite name (comes from HomeKit)
                        device_id = existing_device[0]
                        cursor.execute("""
                            UPDATE devices
                            SET tado_zone_id = ?, zone_id = ?, device_type = ?,
                                battery_state = ?, firmware_version = ?,
                                is_zone_leader = ?, is_circuit_driver = ?, is_zone_driver = ?,
                                duties = ?, last_seen = CURRENT_TIMESTAMP
                            WHERE device_id = ?
                        """, (tado_zone_id, zone_id, device_type, battery_state,
                              firmware, is_leader, is_circuit_driver, is_zone_driver,
                              duties_str, device_id))
                        logger.debug(f"Updated device {serial} in zone {zone_name}")
                    else:
                        # Insert new device - use device type + serial as placeholder name
                        # (will be updated with proper name from HomeKit later)
                        device_name = f"{device_type}_{serial[-6:]}"
                        cursor.execute("""
                            INSERT INTO devices
                            (serial_number, tado_zone_id, zone_id, device_type, name,
                             battery_state, firmware_version, is_zone_leader,
                             is_circuit_driver, is_zone_driver, duties,
                             first_seen, last_seen)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """, (serial, tado_zone_id, zone_id, device_type, device_name,
                              battery_state, firmware, is_leader, is_circuit_driver,
                              is_zone_driver, duties_str))
                        device_id = cursor.lastrowid
                        logger.info(f"Created device {serial} ({device_type}) in zone {zone_name}")

                    synced_devices += 1

                    # Update zone leader if this device is the leader
                    # DISABLED: Tado handles circuit driver relationships internally
                    # if is_leader:
                    #     cursor.execute("""
                    #         UPDATE zones SET leader_device_id = ? WHERE zone_id = ?
                    #     """, (device_id, zone_id))

            conn.commit()
            conn.close()

            logger.info(f"[OK] Synced {synced_zones} zones and {synced_devices} device assignments from Tado Cloud")
            return True

        except Exception as e:
            logger.error(f"Failed to sync zones: {e}", exc_info=True)
            return False

    def sync_device_list(self, device_list_data: Dict[str, Any], home_id: int) -> bool:
        """
        Sync device list from Tado Cloud API to update battery states and metadata.

        The deviceList endpoint provides additional device information including
        battery states that may not be available via HomeKit.

        Args:
            device_list_data: Device list response from Tado Cloud API
            home_id: Tado home ID

        Returns:
            True if successful
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            updated_count = 0

            entries = device_list_data.get('entries', [])
            for entry in entries:
                device = entry.get('device')
                if not device:
                    continue

                serial = device.get('serialNo')
                if not serial:
                    continue

                battery_state = device.get('batteryState')
                firmware = device.get('currentFwVersion')
                raw_device_type = device.get('deviceType')
                device_type = normalize_device_type(raw_device_type) if raw_device_type else None
                zone_info = entry.get('zone', {})
                tado_zone_id = zone_info.get('discriminator')

                # Check if device exists
                cursor.execute("""
                    SELECT device_id FROM devices WHERE serial_number = ?
                """, (serial,))
                existing = cursor.fetchone()

                if existing:
                    # Update existing device
                    cursor.execute("""
                        UPDATE devices
                        SET battery_state = ?, firmware_version = ?,
                            device_type = ?, tado_zone_id = ?,
                            last_seen = CURRENT_TIMESTAMP
                        WHERE serial_number = ?
                    """, (battery_state, firmware, device_type, tado_zone_id, serial))
                    updated_count += 1
                else:
                    # Device not yet in database - will be added during zone sync
                    logger.debug(f"Device {serial} not in database yet (will be added during zone sync)")

            conn.commit()
            conn.close()

            logger.info(f"[OK] Updated {updated_count} devices from device list")
            return True

        except Exception as e:
            logger.error(f"Failed to sync device list: {e}", exc_info=True)
            return False

    async def sync_all(self, cloud_api,
                       home_data=None,
                       zones_data=None,
                       zone_states_data=None,
                       devices_data=None) -> bool:
        """
        Sync all data from Tado Cloud API to database.

        Args:
            cloud_api: TadoCloudAPI instance
            home_data: Pre-fetched home info (optional, will fetch if None)
            zones_data: Pre-fetched zones data (optional, will fetch if None)
            zone_states_data: Pre-fetched zone states (optional, currently unused)
            devices_data: Pre-fetched device list (optional, will fetch if None)

        Returns:
            True if all syncs successful

        Note: Passing None for any parameter means that data type won't be synced
              in this call. This enables differential sync (e.g., battery data
              every 4h, static config every 24h).
        """
        if not cloud_api.is_authenticated():
            logger.warning("Cannot sync: not authenticated with Tado Cloud API")
            return False

        home_id = cloud_api.home_id
        if not home_id:
            logger.error("Cannot sync: no home_id available")
            return False

        success = True
        synced_any = False

        # 1. Sync home info (if provided or needs fetching)
        if home_data is not None:
            logger.info("Syncing home information...")
            if not self.sync_home(home_data):
                success = False
            else:
                synced_any = True
        elif home_data is False:  # Explicitly disabled
            pass
        else:
            # Fetch if not provided
            logger.info("Fetching and syncing home information...")
            home_data = await cloud_api.get_home_info()
            if home_data:
                if not self.sync_home(home_data):
                    success = False
                else:
                    synced_any = True
            else:
                logger.error("Failed to fetch home info")
                success = False

        # 2. Sync zones (if provided or needs fetching)
        if zones_data is not None:
            logger.info("Syncing zones and devices...")
            if not self.sync_zones(zones_data, home_id):
                success = False
            else:
                synced_any = True
        elif zones_data is False:  # Explicitly disabled
            pass
        else:
            # Fetch if not provided
            logger.info("Fetching and syncing zones...")
            zones_data = await cloud_api.get_zones()
            if zones_data:
                if not self.sync_zones(zones_data, home_id):
                    success = False
                else:
                    synced_any = True
            else:
                logger.error("Failed to fetch zones")
                success = False

        # 3. Sync device list (if provided or needs fetching)
        if devices_data is not None:
            logger.info("Syncing device list (battery states)...")
            if not self.sync_device_list(devices_data, home_id):
                success = False
            else:
                synced_any = True
        elif devices_data is False:  # Explicitly disabled
            pass
        else:
            # Fetch if not provided
            logger.info("Fetching and syncing device list...")
            device_list = await cloud_api.get_device_list()
            if device_list:
                if not self.sync_device_list(device_list, home_id):
                    success = False
                else:
                    synced_any = True
            else:
                logger.error("Failed to fetch device list")
                success = False

        if synced_any:
            if success:
                logger.info("[OK] Cloud sync completed successfully")
            else:
                logger.warning("Cloud sync completed with errors")
        else:
            logger.debug("Cloud sync: no data to sync in this call")

        return success
