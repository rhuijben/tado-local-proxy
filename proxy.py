#!/usr/bin/env python3
import argparse
import asyncio
import datetime
import json
import logging
import os
import sqlite3
import time
import traceback
import uuid

from collections import defaultdict
from typing import Dict, List, Any, Optional


from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from pathlib import Path
from typing import Optional

from aiohomekit.characteristic_cache import CharacteristicCacheMemory
from aiohomekit.controller.ip.connection import HomeKitConnection
from aiohomekit.controller.ip.pairing import IpPairing
from aiohomekit.controller.ip.connection import HomeKitConnection
from aiohomekit.controller.ip.controller import IpController
from aiohomekit.protocol import perform_pair_setup_part1, perform_pair_setup_part2
from aiohomekit.utils import check_pin_format, pair_with_auth
from aiohomekit.controller import Controller
        
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
import uvicorn

from zeroconf.asyncio import AsyncZeroconf

from aiohomekit import hkjson

from homekit_uuids import enhance_accessory_data, get_service_name, get_characteristic_name

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__ == '__main__' and 'tado-local-proxy' or __name__)

class CharacteristicCacheSQLite(CharacteristicCacheMemory):
    """SQLite-backed characteristic cache with in-memory caching for performance.
    
    Stores HomeKit accessory metadata in SQLite with 'homekit_' prefix tables.
    Caches everything in RAM and only writes to DB when data changes.
    Designed for dozens of devices (scales to thousands).
    """
    
    def __init__(self, db_path: str):
        """Initialize SQLite-backed cache.
        
        Args:
            db_path: Path to SQLite database file
        """
        super().__init__()
        self.db_path = db_path
        self._init_db()
        self._load_from_db()
    
    def _init_db(self):
        """Initialize database schema for HomeKit cache storage."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS homekit_cache (
                homekit_id TEXT PRIMARY KEY,
                config_num INTEGER NOT NULL,
                accessories TEXT NOT NULL,
                broadcast_key TEXT,
                state_num INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
        logger.debug(f"Initialized HomeKit cache schema in {self.db_path}")
    
    def _load_from_db(self):
        """Load all cached data from database into memory."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT homekit_id, config_num, accessories, broadcast_key, state_num 
            FROM homekit_cache
        """)
        
        for row in cursor.fetchall():
            homekit_id, config_num, accessories_json, broadcast_key, state_num = row
            try:
                accessories = hkjson.loads(accessories_json)
                self.storage_data[homekit_id] = {
                    'config_num': config_num,
                    'accessories': accessories,
                    'broadcast_key': broadcast_key,
                    'state_num': state_num
                }
                logger.debug(f"Loaded HomeKit cache for {homekit_id}")
            except Exception as e:
                logger.warning(f"Failed to load cache for {homekit_id}: {e}")
        
        conn.close()
        logger.info(f"Loaded {len(self.storage_data)} HomeKit cache entries from database")
    
    def async_create_or_update_map(
        self,
        homekit_id: str,
        config_num: int,
        accessories: list,
        broadcast_key: str | None = None,
        state_num: int | None = None,
    ):
        """Create or update pairing cache in memory and database.
        
        Args:
            homekit_id: Unique identifier for the HomeKit pairing
            config_num: Configuration number from HomeKit
            accessories: List of accessory data
            broadcast_key: Optional broadcast encryption key
            state_num: Optional state number for tracking changes
        
        Returns:
            The cached pairing data
        """
        # Update in-memory cache
        data = super().async_create_or_update_map(
            homekit_id, config_num, accessories, broadcast_key, state_num
        )
        
        # Persist to database
        self._save_to_db(homekit_id, config_num, accessories, broadcast_key, state_num)
        
        return data
    
    def async_delete_map(self, homekit_id: str) -> None:
        """Delete pairing cache from memory and database.
        
        Args:
            homekit_id: Unique identifier for the HomeKit pairing
        """
        # Remove from in-memory cache
        super().async_delete_map(homekit_id)
        
        # Remove from database
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM homekit_cache WHERE homekit_id = ?", (homekit_id,))
        conn.commit()
        conn.close()
        logger.debug(f"Deleted HomeKit cache for {homekit_id}")
    
    def _save_to_db(
        self,
        homekit_id: str,
        config_num: int,
        accessories: list,
        broadcast_key: str | None,
        state_num: int | None,
    ):
        """Save cache entry to database.
        
        Args:
            homekit_id: Unique identifier for the HomeKit pairing
            config_num: Configuration number from HomeKit
            accessories: List of accessory data
            broadcast_key: Optional broadcast encryption key
            state_num: Optional state number for tracking changes
        """
        try:
            conn = sqlite3.connect(self.db_path)
            accessories_json = hkjson.dumps(accessories)
            
            conn.execute("""
                INSERT OR REPLACE INTO homekit_cache 
                (homekit_id, config_num, accessories, broadcast_key, state_num, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (homekit_id, config_num, accessories_json, broadcast_key, state_num))
            
            conn.commit()
            conn.close()
            logger.debug(f"Saved HomeKit cache for {homekit_id} (config_num={config_num})")
        except Exception as e:
            logger.error(f"Failed to save HomeKit cache for {homekit_id}: {e}")

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS pairings (
    id INTEGER PRIMARY KEY,
    bridge_ip TEXT UNIQUE,
    pairing_data TEXT
);

CREATE TABLE IF NOT EXISTS controller_identity (
    id INTEGER PRIMARY KEY,
    controller_id TEXT UNIQUE,
    private_key BLOB,
    public_key BLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pairing_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bridge_ip TEXT,
    controller_id TEXT,
    session_state TEXT,
    part1_salt BLOB,
    part1_public_key BLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (controller_id) REFERENCES controller_identity(controller_id)
);

CREATE TABLE IF NOT EXISTS zones (
    zone_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    leader_device_id INTEGER,
    order_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (leader_device_id) REFERENCES devices(device_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_zones_order ON zones(order_id);

CREATE TABLE IF NOT EXISTS devices (
    device_id INTEGER PRIMARY KEY AUTOINCREMENT,
    serial_number TEXT UNIQUE NOT NULL,
    aid INTEGER,
    zone_id INTEGER,
    device_type TEXT,
    name TEXT,
    model TEXT,
    manufacturer TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (zone_id) REFERENCES zones(zone_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_devices_serial ON devices(serial_number);
CREATE INDEX IF NOT EXISTS idx_devices_zone ON devices(zone_id);

CREATE TABLE IF NOT EXISTS device_state_history (
    device_id INTEGER NOT NULL,
    timestamp_bucket TEXT NOT NULL,
    current_temperature REAL,
    target_temperature REAL,
    current_heating_cooling_state INTEGER,
    target_heating_cooling_state INTEGER,
    heating_threshold_temperature REAL,
    cooling_threshold_temperature REAL,
    temperature_display_units INTEGER,
    battery_level INTEGER,
    status_low_battery INTEGER,
    humidity REAL,
    target_humidity REAL,
    active_state INTEGER,
    valve_position INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (device_id, timestamp_bucket),
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_history_device_time ON device_state_history(device_id, timestamp_bucket DESC);
"""

class TadoBridge:
    @staticmethod
    async def get_or_create_controller_identity(db_path: str):
        """Get or create a persistent controller identity for HomeKit pairing."""
        
        conn = sqlite3.connect(db_path)
        conn.executescript(DB_SCHEMA)
        
        # Try to get existing controller identity
        cursor = conn.execute("SELECT controller_id, private_key, public_key FROM controller_identity LIMIT 1")
        row = cursor.fetchone()
        
        if row:
            controller_id, private_key_bytes, public_key_bytes = row
            print(f"Using existing controller identity: {controller_id}")
            
            # Deserialize keys
            private_key = serialization.load_der_private_key(private_key_bytes, password=None)
            public_key = private_key.public_key()
            
            conn.close()
            return controller_id, private_key, public_key
        else:
            # Create new controller identity
            controller_id = str(uuid.uuid4())
            private_key = Ed25519PrivateKey.generate()
            public_key = private_key.public_key()
            
            # Serialize keys for storage
            private_key_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            public_key_bytes = public_key.public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
            
            # Store in database
            conn.execute(
                "INSERT INTO controller_identity (controller_id, private_key, public_key) VALUES (?, ?, ?)",
                (controller_id, private_key_bytes, public_key_bytes)
            )
            conn.commit()
            conn.close()
            
            print(f"Created new controller identity: {controller_id}")
            return controller_id, private_key, public_key

    @staticmethod
    async def save_pairing_session(db_path: str, bridge_ip: str, controller_id: str, salt: bytes, public_key: bytes):
        """Save Part 1 pairing session state for potential resumption."""
        conn = sqlite3.connect(db_path)
        
        # Clean up any old sessions for this bridge
        conn.execute("DELETE FROM pairing_sessions WHERE bridge_ip = ?", (bridge_ip,))
        
        # Save new session
        conn.execute(
            "INSERT INTO pairing_sessions (bridge_ip, controller_id, session_state, part1_salt, part1_public_key) VALUES (?, ?, ?, ?, ?)",
            (bridge_ip, controller_id, "part1_complete", salt, public_key)
        )
        conn.commit()
        conn.close()
        print("Saved Part 1 pairing session for potential resumption")

    @staticmethod
    async def get_pairing_session(db_path: str, bridge_ip: str):
        """Get saved pairing session state."""
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT controller_id, part1_salt, part1_public_key FROM pairing_sessions WHERE bridge_ip = ? AND session_state = 'part1_complete'",
            (bridge_ip,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if row:
            print("Found saved Part 1 pairing session")
            return row[0], row[1], row[2]  # controller_id, salt, public_key
        return None

    @staticmethod
    async def clear_pairing_session(db_path: str, bridge_ip: str):
        """Clear pairing session after successful completion."""
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM pairing_sessions WHERE bridge_ip = ?", (bridge_ip,))
        conn.commit()
        conn.close()

    @staticmethod
    async def perform_pairing(host: str, port: int, pin: str, db_path: str = None):
        """Perform pairing with persistent controller identity management."""
        # Default db path if not provided
        if not db_path:
            db_path = str(Path.home() / ".tado-local.db")
        
        # Get or create persistent controller identity
        controller_id, private_key, public_key = await TadoBridge.get_or_create_controller_identity(db_path)
        
        # Check if we have a saved Part 1 session we can resume from
        saved_session = await TadoBridge.get_pairing_session(db_path, host)
        
        if saved_session:
            controller_id_saved, salt, part1_public_key = saved_session
            if controller_id_saved == controller_id:
                print("=> Found saved Part 1 session, attempting to resume with Part 2...")
                try:
                    result = await TadoBridge.perform_part2_only(host, port, pin, controller_id, salt, part1_public_key, db_path)
                    if result:
                        await TadoBridge.clear_pairing_session(db_path, host)
                        return result
                except Exception as e:
                    print(f"Failed to resume from saved session: {e}")
                    print("Will start fresh pairing...")
        
        # Perform fresh pairing with persistent identity
        return await TadoBridge.perform_fresh_pairing(host, port, pin, controller_id, db_path)

    @staticmethod
    async def perform_part2_only(host: str, port: int, pin: str, controller_id: str, salt: bytes, part1_public_key: bytes, db_path: str):
        """Resume pairing from Part 2 using saved Part 1 state."""

        check_pin_format(pin)
        
        print(f"Resuming Part 2 with saved state for controller {controller_id[:8]}...")
        
        connection = HomeKitConnection(owner=None, hosts=[host], port=port)
        
        try:
            await connection.ensure_connection()
            print("Connection established for Part 2")
            
            # Use saved Part 1 results with our controller ID
            state_machine = perform_pair_setup_part2(pin, controller_id, salt, part1_public_key)
            request, expected = state_machine.send(None)
            
            try:
                while True:
                    response = await connection.post_tlv(
                        "/pair-setup",
                        body=request,
                        expected=expected,
                    )
                    request, expected = state_machine.send(response)
            except StopIteration as result:
                pairing_data = result.value
                print("Part 2 successful - pairing complete using saved session!")
                
                pairing_data["AccessoryIP"] = host
                pairing_data["AccessoryPort"] = port
                pairing_data["Connection"] = "IP"
                
                return pairing_data
                
        except Exception as e:
            print(f"Part 2 resumption failed: {e}")
            raise
        finally:
            await connection.close()

    @staticmethod
    async def perform_fresh_pairing(host: str, port: int, pin: str, controller_id: str, db_path: str):
        """Perform fresh pairing with persistent controller identity."""
        
        check_pin_format(pin)
        
        print(f"Starting fresh pairing with persistent controller {controller_id[:8]}...")
        
        # Try different approaches as before, but with persistent controller ID
        approaches = [
            ("single_connection", "Keep connection open throughout"),
            ("reconnect_between_parts", "Reconnect between Part 1 and Part 2"),
        ]
        
        for approach_name, approach_desc in approaches:
            print(f"\n--- Trying approach: {approach_name} ---")
            print(f"Description: {approach_desc}")
            
            # Try different feature flag values for each approach
            feature_flag_variations = [0, 1]
            
            for feature_flags in feature_flag_variations:
                connection = None
                try:
                    print(f"\nTrying feature flags: {feature_flags}")
                    
                    # Create initial connection
                    print(f"Connecting to {host}:{port}...")
                    connection = HomeKitConnection(owner=None, hosts=[host], port=port)
                    await connection.ensure_connection()
                    print("Connection established")
                    
                    # Part 1: Start pairing
                    print(f"Starting Part 1 with feature flags {feature_flags}...")
                    state_machine = perform_pair_setup_part1(pair_with_auth(feature_flags))
                    request, expected = state_machine.send(None)
                    
                    print(f"Sending pair-setup request...")
                    response = await connection.post_tlv(
                        "/pair-setup",
                        body=request,
                        expected=expected,
                    )
                    
                    # Continue Part 1 state machine
                    try:
                        request, expected = state_machine.send(response)
                        while True:
                            response = await connection.post_tlv(
                                "/pair-setup",
                                body=request,
                                expected=expected,
                            )
                            request, expected = state_machine.send(response)
                    except StopIteration as result:
                        salt, part1_public_key = result.value
                        print(f"Part 1 successful with feature flags {feature_flags}")
                        
                        # Save Part 1 state in case Part 2 fails
                        await TadoBridge.save_pairing_session(db_path, host, controller_id, salt, part1_public_key)
                        
                        # Handle connection between parts based on approach
                        if approach_name == "reconnect_between_parts":
                            print("Closing and reopening connection between parts...")
                            await connection.close()
                            await asyncio.sleep(0.5)
                            connection = HomeKitConnection(owner=None, hosts=[host], port=port)
                            await connection.ensure_connection()
                            print("Reconnected")
                        
                        # Part 2: Complete pairing with PIN
                        print("Starting Part 2 with PIN...")
                        try:
                            state_machine = perform_pair_setup_part2(pin, controller_id, salt, part1_public_key)
                            request, expected = state_machine.send(None)
                            
                            try:
                                while True:
                                    response = await connection.post_tlv(
                                        "/pair-setup",
                                        body=request,
                                        expected=expected,
                                    )
                                    request, expected = state_machine.send(response)
                            except StopIteration as result:
                                pairing_data = result.value
                                print(f"Part 2 successful with approach {approach_name}, feature flags {feature_flags}")
                                
                                # If we get here, pairing succeeded!
                                print(f"\n*** PAIRING SUCCESS! ***")
                                print(f"Successful approach: {approach_name}")
                                print(f"Feature flags: {feature_flags}")
                                print(f"Controller ID: {controller_id}")
                                
                                pairing_data["AccessoryIP"] = host
                                pairing_data["AccessoryPort"] = port
                                pairing_data["Connection"] = "IP"
                                
                                # Clear the saved session since we completed successfully
                                await TadoBridge.clear_pairing_session(db_path, host)
                                
                                await connection.close()
                                return pairing_data
                                
                        except Exception as e:
                            print(f"Part 2 failed with approach {approach_name}, feature flags {feature_flags}: {e}")
                            print(f"Error type: {type(e)}")
                            print("Part 1 state saved - you can retry and we'll attempt to resume from Part 2")
                            await connection.close()
                            continue
                        
                except Exception as e:
                    print(f"Overall attempt failed with approach {approach_name}, feature flags {feature_flags}: {e}")
                    if connection:
                        await connection.close()
                    continue
            
            print(f"--- Approach {approach_name} completed, trying next approach ---")
        
        # If we get here, all attempts failed
        print("\n============================================================")
        print("ALL PAIRING ATTEMPTS FAILED")
        print("============================================================")
        print("Tried approaches:")
        for approach_name, approach_desc in approaches:
            print(f"- {approach_name}: {approach_desc}")
        print(f"\nController identity persisted: {controller_id}")
        print("Part 1 state may be saved - retry might resume from Part 2")
        print("\nPossible issues:")
        print("1. Device is already paired to another HomeKit controller")
        print("2. Device is not in pairing mode") 
        print("3. Wrong PIN code")
        print("4. Device needs to be reset/factory reset")
        print("5. Network connectivity issues")
        print("6. Device-specific pairing behavior not yet understood")
        print("\nTroubleshooting steps:")
        print("- Check if device is showing on other HomeKit controllers")
        print("- Try factory resetting the device")
        print("- Verify the PIN is correct from device label")
        print("- Ensure device is in pairing mode")
        print("- Retry - we'll attempt to resume from saved Part 1 state")
        print("============================================================")
        raise Exception("All pairing attempts failed - see troubleshooting info above")

    @staticmethod
    async def perform_manual_pairing(host: str, port: int, pin: str):
        """Manual pairing method - redirects to new persistent pairing approach."""
        print("WARNING: perform_manual_pairing is deprecated - use perform_pairing with db_path instead")
        # Use default db path for backward compatibility
        db_path = str(Path.home() / ".tado-local.db")
        return await TadoBridge.perform_pairing(host, port, pin, db_path)

    @staticmethod
    async def perform_alternative_pairing(host: str, port: int, pin: str):
        """Try alternative pairing approach with different timing and connection handling."""
        
        print("Trying alternative pairing approach...")
        print("- Using longer timeouts")
        print("- Different connection handling")
        print("- Modified feature flags")
        
        # Try with just feature flags 0 and 1, but with different timing
        for feature_flags in [0, 1]:
            print(f"\nTesting alternative approach with feature flags {feature_flags}")
            
            connection = HomeKitConnection(owner=None, hosts=[host], port=port)
            
            try:
                # Longer connection timeout
                await connection.ensure_connection()
                print("Connection established")
                
                # Try to get pairing info first
                try:
                    response = await connection.get("/pair-setup")
                    print(f"Pair-setup status: {response}")
                except Exception as e:
                    print(f"Cannot query pair-setup status: {e}")
                
                # Wait a moment before starting pairing
                await asyncio.sleep(2)
                
                print(f"=> Starting pairing protocol with feature flags {feature_flags}")
                
                # Part 1: Modified approach
                state_machine = perform_pair_setup_part1(pair_with_auth(feature_flags))
                request, expected = state_machine.send(None)
                
                # Send with longer timeout expectations
                print("Sending initial pairing request...")
                response = await connection.post_tlv(
                    "/pair-setup",
                    body=request,
                    expected=expected,
                )
                
                print(f"Got response: {type(response)}")
                
                # Process the state machine with better error handling
                try:
                    request, expected = state_machine.send(response)
                    
                    # Continue the conversation
                    while True:
                        print("Continuing pairing conversation...")
                        response = await connection.post_tlv(
                            "/pair-setup",
                            body=request,
                            expected=expected,
                        )
                        request, expected = state_machine.send(response)
                        
                except StopIteration as result:
                    salt, pub_key = result.value
                    print(f"Part 1 completed with feature flags {feature_flags}")
                    
                    # Close and create fresh connection for part 2
                    await connection.close()
                    await asyncio.sleep(1)  # Brief pause between parts
                    
                    connection = HomeKitConnection(owner=None, hosts=[host], port=port)
                    await connection.ensure_connection()
                    
                    print("Starting Part 2 with PIN...")
                    state_machine = perform_pair_setup_part2(pin, str(uuid.uuid4()), salt, pub_key)
                    request, expected = state_machine.send(None)
                    
                    try:
                        while True:
                            response = await connection.post_tlv(
                                "/pair-setup",
                                body=request,
                                expected=expected,
                            )
                            request, expected = state_machine.send(response)
                    except StopIteration as result:
                        pairing_data = result.value
                        print("Alternative pairing approach succeeded!")
                        
                        pairing_data["AccessoryIP"] = host
                        pairing_data["AccessoryPort"] = port
                        pairing_data["Connection"] = "IP"
                        
                        return pairing_data
                        
            except Exception as e:
                print(f"Alternative approach failed with feature flags {feature_flags}: {e}")
                
            finally:
                await connection.close()
        
    @staticmethod
    async def perform_simple_pairing(host: str, port: int, pin: str):
        """Simplified pairing approach to isolate the UnavailableError issue."""
        
        print("SIMPLIFIED PAIRING APPROACH")
        print("=" * 40)
        
        # Test basic connectivity first
        print("Testing basic connectivity...")
        connection = HomeKitConnection(owner=None, hosts=[host], port=port)
        
        try:
            await connection.ensure_connection()
            print("Basic connection: SUCCESS")
        except Exception as e:
            print(f"Basic connection: FAILED - {e}")
            raise
        finally:
            await connection.close()
        
        # Test pair-setup endpoint accessibility
        print("\nTesting pair-setup endpoint...")
        connection = HomeKitConnection(owner=None, hosts=[host], port=port)
        
        try:
            await connection.ensure_connection()
            
            # Try GET first to see if endpoint is responsive
            try:
                response = await connection.get("/pair-setup")
                print(f"pair-setup GET: {response}")
            except Exception as e:
                print(f"pair-setup GET failed: {e}")
                # This might be normal for some devices
                
        except Exception as e:
            print(f"pair-setup endpoint test: FAILED - {e}")
            raise
        finally:
            await connection.close()
        
        # Now try the actual pairing with minimal complexity
        print("\nAttempting minimal pairing...")
        connection = HomeKitConnection(owner=None, hosts=[host], port=port)
        
        try:
            await connection.ensure_connection()
            
            # Use only feature flags 0 (most basic)
            print("Sending M1 (pair-setup start) with feature flags 0...")
            
            state_machine = perform_pair_setup_part1(pair_with_auth(0))
            request, expected = state_machine.send(None)
            
            print(f"Request type: {type(request)}")
            print(f"Expected response: {expected}")
            
            # Send the request and see exactly what we get back
            response = await connection.post_tlv(
                "/pair-setup",
                body=request,
                expected=expected,
            )
            
            print(f"Response type: {type(response)}")
            print(f"Response content: {response}")
            
            # Check for error codes in the response
            if hasattr(response, 'get'):
                error_code = response.get(b'\x02')  # kTLVType_Error
                if error_code:
                    print(f"Device returned error code: {error_code}")
                    if error_code == b'\x02':
                        print("   Error: Unavailable (0x02)")
                        print("   This means the device thinks it's already paired or not ready")
                    elif error_code == b'\x01':
                        print("   Error: Unknown (0x01)")
                    elif error_code == b'\x03':
                        print("   Error: Authentication (0x03)")
                    elif error_code == b'\x06':
                        print("   Error: Busy (0x06)")
                    
                    # Let's try to understand WHY the device says unavailable
                    print("\nDEVICE STATUS ANALYSIS:")
                    print("The device is saying 'Unavailable' but is discoverable.")
                    print("This could mean:")
                    print("- Device has partial pairing state from previous attempt")
                    print("- Device needs specific reset sequence")
                    print("- Device has maximum pairing limit reached")
                    print("- Device requires specific pairing button sequence")
                    
                    raise Exception(f"Device returned error code {error_code} - see analysis above")
            
            # If no error, continue with the state machine
            try:
                request, expected = state_machine.send(response)
                print("M1 successful, continuing with pairing process...")
                # Continue the pairing conversation
                while True:
                    response = await connection.post_tlv(
                        "/pair-setup",
                        body=request,
                        expected=expected,
                    )
                    request, expected = state_machine.send(response)
            except StopIteration as result:
                # Part 1 completed successfully!
                salt, pub_key = result.value
                print("Part 1 (M1-M2) completed successfully!")
                print("Starting Part 2 with PIN...")
                
                # Close connection and create fresh one for Part 2
                await connection.close()
                
                # Wait a moment before Part 2
                await asyncio.sleep(2)
                
                connection = HomeKitConnection(owner=None, hosts=[host], port=port)
                await connection.ensure_connection()
                print("Fresh connection established for Part 2")
                
                # Part 2: Complete pairing with PIN
                state_machine = perform_pair_setup_part2(pin, str(uuid.uuid4()), salt, pub_key)
                request, expected = state_machine.send(None)
                
                print("Sending Part 2 M3 message...")
                
                try:
                    while True:
                        response = await connection.post_tlv(
                            "/pair-setup",
                            body=request,
                            expected=expected,
                        )
                        print(f"Part 2 response: {type(response)}")
                        request, expected = state_machine.send(response)
                except StopIteration as result:
                    pairing_data = result.value
                    print("Part 2 completed - PAIRING SUCCESSFUL!")
                    
                    # Add connection info to pairing data
                    pairing_data["AccessoryIP"] = host
                    pairing_data["AccessoryPort"] = port
                    pairing_data["Connection"] = "IP"
                    
                    return pairing_data
            
        except Exception as e:
            print(f"Pairing failed: {e}")
            raise
        finally:
            await connection.close()
        
        # Should not reach here if pairing was successful
        raise Exception("Pairing completed but no data returned")
    
    @staticmethod
    async def pair_or_load(bridge_ip: Optional[str], pin: Optional[str], db_path: Path, clear_pairings: bool = False):
        conn = sqlite3.connect(db_path)
        conn.executescript(DB_SCHEMA)  # Use executescript for multiple statements
        conn.commit()

        # Clear existing pairings if requested
        if clear_pairings:
            conn.execute("DELETE FROM pairings")
            conn.commit()
            print("Cleared all existing pairings as requested")

        # Get all existing pairings
        all_pairings = conn.execute("SELECT bridge_ip, pairing_data FROM pairings").fetchall()
        
        if all_pairings:
            print(f"Found {len(all_pairings)} existing pairing(s):")
            for i, (ip, _) in enumerate(all_pairings):
                print(f"  {i+1}. {ip}")
        
        # Auto-select pairing logic
        pairing_data = None
        selected_bridge_ip = None
        
        if bridge_ip:
            # User specified a bridge IP, try to find that specific pairing
            row = conn.execute(
                "SELECT pairing_data FROM pairings WHERE bridge_ip = ?", (bridge_ip,)
            ).fetchone()
            if row:
                pairing_data = json.loads(row[0])
                selected_bridge_ip = bridge_ip
                print(f"Found existing pairing for specified IP: {bridge_ip}")
            else:
                print(f"No existing pairing found for specified IP: {bridge_ip}")
        else:
            # No bridge IP specified, auto-select if only one pairing exists
            if len(all_pairings) == 1:
                pairing_data = json.loads(all_pairings[0][1])
                selected_bridge_ip = all_pairings[0][0]
                print(f"Auto-selected the only existing pairing: {selected_bridge_ip}")
            elif len(all_pairings) > 1:
                print(f"Multiple pairings found. Please specify --bridge-ip with one of:")
                for ip, _ in all_pairings:
                    print(f"   --bridge-ip {ip}")
                raise RuntimeError("Multiple pairings available. Please specify --bridge-ip.")
            else:
                print(f"No existing pairings found.")

        # If we have existing pairing data, test it first
        if pairing_data is not None:
            print(f"=> Testing existing pairing for {selected_bridge_ip}...")
            
            # Create a controller with proper async context
            try:
                # Create async zeroconf instance 
                zeroconf_instance = AsyncZeroconf()
                
                # Create SQLite-backed characteristic cache
                char_cache = CharacteristicCacheSQLite(str(db_path))
                
                # Create controller with proper dependencies
                controller = IpController(char_cache=char_cache, zeroconf_instance=zeroconf_instance)
                
                # Create pairing with controller instance
                pairing = IpPairing(controller, pairing_data)
                
                # Test connection
                await pairing._ensure_connected()
                accessories = await pairing.list_accessories_and_characteristics()
                print(f"Successfully connected to {selected_bridge_ip}!")
                print(f"Found {len(accessories)} accessories")
                
                return pairing
                
            except Exception as e:
                print(f"Failed to connect to existing pairing: {e}")
                print(f"Connection failed, but keeping pairing data (may be temporary network issue)")
                print(f"To force re-pairing, delete the pairing manually or use --pin to create a new one")
                
                # DO NOT remove the pairing data automatically - it might just be a temporary issue
                # conn.execute("DELETE FROM pairings WHERE bridge_ip = ?", (selected_bridge_ip,))
                # conn.commit()
                
                # Still raise the error so we don't try to continue with a broken connection
                raise RuntimeError(f"Failed to connect to existing pairing for {selected_bridge_ip}: {e}")

        # Need to pair
        if pin:
            if not bridge_ip:
                raise RuntimeError("Bridge IP required for initial pairing with PIN")
            print(f"Starting fresh pairing with {bridge_ip} using PIN {pin}...")
            
            try:
                # Perform pairing using enhanced protocol with persistent controller identity
                pairing_result = await TadoBridge.perform_pairing_with_controller(bridge_ip, 80, pin, str(db_path))
                
                # Use the pairing data as returned by the protocol
                pairing_data = pairing_result
                
                # Save to DB
                conn.execute(
                    "INSERT OR REPLACE INTO pairings (bridge_ip, pairing_data) VALUES (?, ?)",
                    (bridge_ip, json.dumps(pairing_data)),
                )
                conn.commit()
                print("Pairing successful and saved to database!")
                
                # Create pairing instance with the new data
                # Create a controller instance for the pairing
                
                zeroconf_instance = AsyncZeroconf()
                char_cache = CharacteristicCacheSQLite(str(db_path))
                controller = IpController(char_cache=char_cache, zeroconf_instance=zeroconf_instance)
                pairing = IpPairing(controller, pairing_data)
                await pairing._ensure_connected()
                await pairing.list_accessories_and_characteristics()
                print("Connected and fetched accessories!")
                
                return pairing
                
            except Exception as e:
                print(f"Pairing failed: {e}")
                
                # Provide enhanced error messages based on Home Assistant's approach
                if "UnavailableError" in str(type(e)) or "Unavailable" in str(e):
                    print("\n" + "="*60)
                    print("DEVICE REPORTS 'UNAVAILABLE' FOR PAIRING")
                    print("="*60)
                    print("Based on Home Assistant's approach, this typically means:")
                    print("1. Device is already paired to another HomeKit controller")
                    print("2. Device needs to be reset to clear existing pairings")
                    print("3. Device might be paired to iPhone/iPad/Mac HomeKit")
                    print("4. Device might be paired to another Home Assistant instance")
                    print("")
                    print("SOLUTIONS TO TRY:")
                    print("1. Check if device appears in:")
                    print("   - iPhone/iPad Home app")
                    print("   - Other Home Assistant instances")
                    print("   - HomeKit-enabled apps")
                    print("")
                    print("2. If device is paired elsewhere:")
                    print("   - Remove it from that HomeKit controller first")
                    print("   - OR factory reset the device")
                    print("")
                    print("3. For Tado devices specifically:")
                    print("   - Try holding reset button for 10+ seconds")
                    print("   - Look for factory reset procedure in manual")
                    print("   - Some Tado devices require power cycling after reset")
                    print("")
                    print("4. Advanced troubleshooting:")
                    print("   - Check device status flags in mDNS browser")
                    print("   - Look for 'sf=1' (unpaired) vs 'sf=0' (paired)")
                    print("   - Verify device is actually advertising for pairing")
                    print("="*60 + "\n")
                elif "Already" in str(e):
                    print("Device appears to already be paired.")
                elif "Authentication" in str(type(e)) or "Authentication" in str(e):
                    print("Authentication error - check PIN or try resetting device.")
                elif "BusyError" in str(type(e)) or "Busy" in str(e):
                    print("Device is busy - wait a moment and try again.")
                    
                raise

        raise RuntimeError("No pairing data found and no PIN provided. Provide --pin to pair first.")

    @staticmethod
    async def perform_pairing_with_controller(host: str, port: int = 1234, hap_pin: str = "557-15-876", db_path: str = None):
        """
        Perform HomeKit pairing using Controller.start_pairing() method.
        """
        try:
            print(f"Starting controller-based pairing with {host}:{port} using PIN: {hap_pin}")
            
            # Default db path if not provided
            if not db_path:
                db_path = str(Path.home() / ".tado-local.db")
            
            # Get or create persistent controller identity
            controller_id, private_key, public_key = await TadoBridge.get_or_create_controller_identity(db_path)
            print(f"Using Controller ID: {controller_id}")
            
            try:
                # Create required dependencies for Controller
                
                # Create AsyncZeroconf instance
                zeroconf_instance = AsyncZeroconf()
                
                # Create SQLite-backed characteristic cache
                char_cache = CharacteristicCacheSQLite(db_path)
                
                # Create the main Controller (not IpController)
                controller = Controller(
                    async_zeroconf_instance=zeroconf_instance,
                    char_cache=char_cache
                )
                
                print(f"Created controller with proper dependencies")
                
                # Start pairing using the controller's built-in method
                print(f"Starting pairing process...")
                
                # This should use the controller's pairing method which returns an IpPairing
                pairing = await controller.start_pairing(host, hap_pin)
                
                print(f"Pairing completed successfully!")
                
                # Clean up zeroconf instance
                await zeroconf_instance.async_close()
                
                # Extract pairing data in the correct format
                pairing_data = pairing.pairing_data
                
                print(f"PAIRING SUCCESS! Controller-based approach, Controller ID: {controller_id}")
                
                return pairing_data
                
            except Exception as e:
                print(f"Controller-based pairing failed: {e}")
                traceback.print_exc()
                raise
                
        except Exception as e:
            print(f"Pairing failed with error: {e}")
            traceback.print_exc()
            raise

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
        self.device_info_cache: Dict[int, Dict[str, Any]] = {}  # device_id -> {name, zone_name, serial, etc}
        self.current_state: Dict[int, Dict[str, Any]] = {}  # device_id -> current state
        self.last_saved_bucket: Dict[int, str] = {}  # device_id -> last saved bucket
        self.bucket_state_snapshot: Dict[int, Dict[str, Any]] = {}  # device_id -> state when bucket was saved
        self._ensure_schema()
        self._load_device_cache()
        self._load_latest_state_from_db()
    
    def _ensure_schema(self):
        """Ensure the device tables exist in the database."""
        conn = sqlite3.connect(self.db_path)
        
        # Create zones table first WITHOUT foreign key (to avoid circular dependency)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS zones (
                zone_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                leader_device_id INTEGER,
                order_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_zones_order ON zones(order_id)")
        
        # Create devices table with zone_id column
        conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                device_id INTEGER PRIMARY KEY AUTOINCREMENT,
                serial_number TEXT UNIQUE NOT NULL,
                aid INTEGER,
                zone_id INTEGER,
                device_type TEXT,
                name TEXT,
                model TEXT,
                manufacturer TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (zone_id) REFERENCES zones(zone_id) ON DELETE SET NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_serial ON devices(serial_number)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_zone ON devices(zone_id)")
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS device_state_history (
                device_id INTEGER NOT NULL,
                timestamp_bucket TEXT NOT NULL,
                current_temperature REAL,
                target_temperature REAL,
                current_heating_cooling_state INTEGER,
                target_heating_cooling_state INTEGER,
                heating_threshold_temperature REAL,
                cooling_threshold_temperature REAL,
                temperature_display_units INTEGER,
                battery_level INTEGER,
                status_low_battery INTEGER,
                humidity REAL,
                target_humidity REAL,
                active_state INTEGER,
                valve_position INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (device_id, timestamp_bucket),
                FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_device_time ON device_state_history(device_id, timestamp_bucket DESC)")
        
        conn.commit()
        conn.close()
    
    def _load_device_cache(self):
        """Load device ID mappings and info from database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT d.device_id, d.serial_number, d.name, d.device_type, z.name as zone_name
            FROM devices d
            LEFT JOIN zones z ON d.zone_id = z.zone_id
        """)
        for device_id, serial_number, name, device_type, zone_name in cursor.fetchall():
            self.device_id_cache[serial_number] = device_id
            self.device_info_cache[device_id] = {
                'serial_number': serial_number,
                'name': name,
                'device_type': device_type,
                'zone_name': zone_name
            }
        conn.close()
        logger.info(f"Loaded {len(self.device_id_cache)} devices from cache")
    
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
        """Get cached device info including zone name."""
        return self.device_info_cache.get(device_id, {})
    
    def get_or_create_device(self, serial_number: str, aid: int, accessory_data: dict) -> int:
        """Get or create device ID for a serial number."""
        if serial_number in self.device_id_cache:
            return self.device_id_cache[serial_number]
        
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
        
        # Create device entry
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            INSERT INTO devices (serial_number, aid, device_type, name, model, manufacturer)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (serial_number, aid, device_type, name, model, manufacturer))
        device_id = cursor.lastrowid
        conn.commit()
        
        # Get zone_name if device has a zone assigned
        zone_cursor = conn.execute("""
            SELECT z.name 
            FROM devices d
            LEFT JOIN zones z ON d.zone_id = z.zone_id
            WHERE d.device_id = ?
        """, (device_id,))
        zone_row = zone_cursor.fetchone()
        zone_name = zone_row[0] if zone_row else None
        
        conn.close()
        
        # Update both caches
        self.device_id_cache[serial_number] = device_id
        self.device_info_cache[device_id] = {
            'serial_number': serial_number,
            'name': name,
            'device_type': device_type,
            'zone_name': zone_name
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
    
    def get_device_history(self, device_id: int, start_time: float = None, end_time: float = None, limit: int = 100) -> List[Dict]:
        """Get device state history."""
        conn = sqlite3.connect(self.db_path)
        
        query = """
            SELECT timestamp_bucket, current_temperature, target_temperature,
                   current_heating_cooling_state, target_heating_cooling_state,
                   heating_threshold_temperature, cooling_threshold_temperature,
                   temperature_display_units, battery_level, status_low_battery, humidity,
                   updated_at
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
        
        query += " ORDER BY timestamp_bucket DESC LIMIT ?"
        params.append(limit)
        
        cursor = conn.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        history = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        
        return history
    
    def get_current_state(self, device_id: int = None) -> Dict:
        """Get current state for one or all devices."""
        if device_id is not None:
            return self.current_state.get(device_id, {})
        return self.current_state
    
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

# FastAPI app with HomeKit event handling
app = FastAPI(
    title="Tado Local API",
    description="Local REST API for Tado devices via HomeKit bridge",
    version="1.0.0"
)

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
        
    async def initialize(self, pairing: IpPairing):
        """Initialize the API with a HomeKit pairing."""
        self.pairing = pairing
        self.is_initializing = True  # Suppress change logging during init
        await self.refresh_accessories()
        await self.initialize_device_states()
        self.is_initializing = False  # Re-enable change logging
        await self.setup_event_listeners()
        logger.info("Tado Local API initialized successfully")
        
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
                print(f"Event callback received update: {update_data}")
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
                if source == "EVENT":
                    self.change_tracker['events_received'] += 1
                    logger.info(f"[EVENT] Zone: {zone_name} | Device: {device_name} | {char_name}: {last_value} -> {value}")
                elif source == "POLLING" or source == "FAST-POLL":
                    self.change_tracker['polling_changes'] += 1
                    logger.info(f"[{source}] Zone: {zone_name} | Device: {device_name} | {char_name}: {last_value} -> {value}")
            
            # Send to event stream for clients (always, even during init)
            event_data = {
                'source': source,
                'timestamp': timestamp,
                'aid': aid,
                'iid': iid,
                'characteristic': char_name,
                'value': value,
                'previous_value': last_value,
                'id': device_id if device_id in self.accessories_dict else None,
                'zone_name': zone_name,
                'device_name': device_name
            }
            await self.broadcast_event(event_data)
            
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
                # Start background polling task
                asyncio.create_task(self.background_polling_loop())
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
            
            # Add humidity and battery to priority list
            if 'humidity' in char_name.lower() or 'battery' in char_name.lower():
                priority_chars.add((aid, iid))
        
        if priority_chars:
            logger.info(f"Fast polling ({fast_poll_interval}s) for {len(priority_chars)} priority characteristics (humidity, battery)")
            logger.info(f"Normal polling ({slow_poll_interval}s) for {len(self.monitored_characteristics) - len(priority_chars)} other characteristics")
        
        last_fast_poll = 0
        last_slow_poll = 0
        
        while True:
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

# Global API instance - will be initialized in main()
tado_api: Optional[TadoLocalAPI] = None

@app.get("/", tags=["Info"])
async def root():
    """API root with basic information."""
    return {
        "service": "Tado Local API",
        "description": "Local REST API for Tado devices via HomeKit bridge",
        "version": "1.0.0",
        "documentation": "/docs",
        "endpoints": {
            "status": "/status",
            "devices": "/devices",
            "device_by_id": "/devices/{id}",
            "device_history": "/devices/{id}/history",
            "device_assign_zone": "/devices/{id}/zone",
            "current_state": "/devices/current-state",
            "accessories": "/accessories", 
            "zones": "/zones",
            "zones_create": "POST /zones",
            "zones_update": "PUT /zones/{zone_id}",
            "thermostats": "/thermostats",
            "thermostat_by_id": "/thermostats/{id}",
            "events": "/events",
            "refresh": "/refresh",
            "debug_characteristics": "/debug/characteristics",
            "debug_humidity": "/debug/humidity"
        },
        "note": "All endpoints use 'id' (database device_id) as primary key. Thermostat endpoints return LIVE data."
    }

@app.get("/status", tags=["Status"])
async def get_status():
    """Get overall system status."""
    if not tado_api or not tado_api.pairing:
        raise HTTPException(status_code=503, detail="Bridge not connected")
    
    try:
        # Test connection
        await tado_api.pairing.list_accessories_and_characteristics()
        
        devices = tado_api.state_manager.get_all_devices()
        
        return {
            "status": "connected",
            "bridge_connected": True,
            "last_update": tado_api.last_update,
            "cached_accessories": len(tado_api.accessories_cache),
            "tracked_devices": len(devices),
            "active_listeners": len(tado_api.event_listeners),
            "events_received": tado_api.change_tracker.get('events_received', 0),
            "polling_changes": tado_api.change_tracker.get('polling_changes', 0),
            "uptime": time.time() - (tado_api.last_update or time.time())
        }
    except Exception as e:
        return {
            "status": "error", 
            "bridge_connected": False,
            "error": str(e)
        }

@app.get("/accessories", tags=["HomeKit"])
async def get_accessories(enhanced: bool = True):
    """
    Get all HomeKit accessories and their characteristics.
    
    Args:
        enhanced: If True, include human-readable names for UUIDs (default: True)
    """
    accessories = await tado_api.refresh_accessories()
    
    if enhanced:
        return {
            "accessories": enhance_accessory_data(accessories),
            "enhanced": True,
            "note": "UUIDs have been enhanced with human-readable names. Use ?enhanced=false for raw data."
        }
    else:
        return {
            "accessories": accessories,
            "enhanced": False
        }

@app.get("/accessories/{accessory_id}", tags=["HomeKit"])  
async def get_accessory(accessory_id: int, enhanced: bool = True):
    """
    Get specific accessory by ID.
    
    Args:
        accessory_id: The HomeKit accessory ID
        enhanced: If True, include human-readable names for UUIDs (default: True)
    """
    if not tado_api.accessories_cache:
        await tado_api.refresh_accessories()
    
    accessories = tado_api.accessories_cache
    for accessory in accessories:
        if accessory.get('aid') == accessory_id:
            if enhanced:
                enhanced_accessories = enhance_accessory_data([accessory])
                return {
                    "accessory": enhanced_accessories[0] if enhanced_accessories else accessory,
                    "enhanced": True
                }
            else:
                return {
                    "accessory": accessory,
                    "enhanced": False
                }
    
    raise HTTPException(status_code=404, detail=f"Accessory {accessory_id} not found")

@app.get("/zones", tags=["Tado"])
async def get_zones():
    """Get all Tado zones (thermostats, radiator controls, etc)."""
    if not tado_api.accessories_cache:
        await tado_api.refresh_accessories()
    
    zones = []
    accessories = tado_api.accessories_cache
    
    for accessory in accessories:
        services = accessory.get('services', [])
        for service in services:
            service_type = service.get('type')
            
            # Look for thermostat and other HVAC services
            if service_type in ['public.hap.service.thermostat', 
                               'public.hap.service.heater-cooler',
                               'public.hap.service.temperature-sensor']:
                
                zone_info = {
                    'accessory_id': accessory.get('aid'),
                    'service_id': service.get('iid'),
                    'service_type': service_type,
                    'characteristics': {}
                }
                
                # Extract relevant characteristics
                for char in service.get('characteristics', []):
                    char_type = char.get('type')
                    char_value = char.get('value')
                    
                    if char_type == 'public.hap.characteristic.current-temperature':
                        zone_info['characteristics']['current_temperature'] = char_value
                    elif char_type == 'public.hap.characteristic.target-temperature':
                        zone_info['characteristics']['target_temperature'] = char_value
                    elif char_type == 'public.hap.characteristic.current-heating-cooling-state':
                        zone_info['characteristics']['heating_state'] = char_value
                    elif char_type == 'public.hap.characteristic.target-heating-cooling-state':
                        zone_info['characteristics']['target_heating_state'] = char_value
                    elif char_type == 'public.hap.characteristic.current-relative-humidity':
                        zone_info['characteristics']['humidity'] = char_value
                
                zones.append(zone_info)
    
    return {"zones": zones, "count": len(zones)}

@app.get("/thermostats", tags=["Tado"])
async def get_thermostats():
    """Get all thermostat devices with current and target temperatures - uses live state."""
    if not tado_api:
        raise HTTPException(status_code=503, detail="API not initialized")
    
    if not tado_api.accessories_cache:
        await tado_api.refresh_accessories()
    
    thermostats = []
    accessories = tado_api.accessories_cache
    
    # Get current live state from state manager
    current_states = tado_api.state_manager.get_current_state()
    
    for accessory in accessories:
        services = accessory.get('services', [])
        for service in services:
            if service.get('type') == '0000004A-0000-1000-8000-0026BB765291':
                
                device_id = accessory.get('id')
                live_state = current_states.get(device_id, {}) if device_id else {}
                
                thermostat = {
                    'id': device_id,
                    'aid': accessory.get('aid'),
                    'serial_number': accessory.get('serial_number'),
                    # Use live state if available, otherwise fall back to cached
                    'current_temperature': live_state.get('current_temperature'),
                    'target_temperature': live_state.get('target_temperature'),
                    'heating_state': live_state.get('current_heating_cooling_state'),
                    'target_heating_state': live_state.get('target_heating_cooling_state'),
                    'humidity': live_state.get('humidity'),
                    'valve_position': live_state.get('valve_position'),
                    'active_state': live_state.get('active_state'),
                    'last_update': live_state.get('last_update'),
                    'data_source': 'live' if live_state else 'cache'
                }
                
                # Extract name from characteristics if not in live state
                for char in service.get('characteristics', []):
                    char_type = char.get('type')
                    char_value = char.get('value')
                    
                    if char_type == '00000023-0000-1000-8000-0026BB765291':
                        thermostat['name'] = char_value
                    
                    # Only use cached values if no live state available
                    if not live_state:
                        if char_type == '00000011-0000-1000-8000-0026BB765291':
                            thermostat['current_temperature'] = char_value
                        elif char_type == '00000035-0000-1000-8000-0026BB765291':
                            thermostat['target_temperature'] = char_value
                        elif char_type == '0000000F-0000-1000-8000-0026BB765291':
                            thermostat['heating_state'] = char_value
                        elif char_type == '00000033-0000-1000-8000-0026BB765291':
                            thermostat['target_heating_state'] = char_value
                        elif char_type == '00000010-0000-1000-8000-0026BB765291':
                            thermostat['humidity'] = char_value
                
                thermostats.append(thermostat)
    
    return {"thermostats": thermostats, "count": len(thermostats)}

@app.get("/thermostats/{id}", tags=["Tado"])
async def get_thermostat(id: int):
    """Get specific thermostat by ID (database device_id) with live state."""
    if not tado_api:
        raise HTTPException(status_code=503, detail="API not initialized")
    
    if not tado_api.accessories_cache:
        await tado_api.refresh_accessories()
    
    # Find accessory by device ID
    accessory = None
    for acc in tado_api.accessories_cache:
        if acc.get('id') == id:
            accessory = acc
            break
    
    if not accessory:
        raise HTTPException(status_code=404, detail=f"Device with ID {id} not found")
    
    # Check if it's a thermostat
    is_thermostat = False
    for service in accessory.get('services', []):
        if service.get('type') == '0000004A-0000-1000-8000-0026BB765291':
            is_thermostat = True
            break
    
    if not is_thermostat:
        raise HTTPException(status_code=400, detail=f"Device {id} is not a thermostat")
    
    # Get live state
    live_state = tado_api.state_manager.get_current_state(id) if id else {}
    
    thermostat = {
        'id': id,
        'aid': accessory.get('aid'),
        'serial_number': accessory.get('serial_number'),
        'current_temperature': live_state.get('current_temperature'),
        'target_temperature': live_state.get('target_temperature'),
        'heating_state': live_state.get('current_heating_cooling_state'),
        'target_heating_state': live_state.get('target_heating_cooling_state'),
        'humidity': live_state.get('humidity'),
        'valve_position': live_state.get('valve_position'),
        'active_state': live_state.get('active_state'),
        'battery_level': live_state.get('battery_level'),
        'status_low_battery': live_state.get('status_low_battery'),
        'last_update': live_state.get('last_update'),
        'data_source': 'live' if live_state else 'cache'
    }
    
    # Get name from characteristics
    for service in accessory.get('services', []):
        for char in service.get('characteristics', []):
            if char.get('type') == '00000023-0000-1000-8000-0026BB765291':
                thermostat['name'] = char.get('value')
                break
    
    return thermostat

@app.get("/zones", tags=["Zones"])
async def get_zones():
    """Get all zones with their devices."""
    if not tado_api:
        raise HTTPException(status_code=503, detail="API not initialized")
    
    conn = sqlite3.connect(tado_api.state_manager.db_path)
    
    # Get all zones
    cursor = conn.execute("""
        SELECT z.zone_id, z.name, z.leader_device_id, z.order_id,
               d.serial_number as leader_serial
        FROM zones z
        LEFT JOIN devices d ON z.leader_device_id = d.device_id
        ORDER BY z.order_id, z.name
    """)
    
    zones = []
    for zone_id, name, leader_device_id, order_id, leader_serial in cursor.fetchall():
        # Get devices in this zone
        device_cursor = conn.execute("""
            SELECT device_id, serial_number, name, device_type
            FROM devices
            WHERE zone_id = ?
            ORDER BY device_id
        """, (zone_id,))
        
        devices = []
        for dev_id, serial, dev_name, dev_type in device_cursor.fetchall():
            devices.append({
                'device_id': dev_id,
                'serial_number': serial,
                'name': dev_name,
                'device_type': dev_type,
                'is_leader': dev_id == leader_device_id
            })
        
        zones.append({
            'zone_id': zone_id,
            'name': name,
            'leader_device_id': leader_device_id,
            'leader_serial': leader_serial,
            'order_id': order_id,
            'devices': devices,
            'device_count': len(devices)
        })
    
    conn.close()
    
    return {
        'zones': zones,
        'count': len(zones)
    }

@app.post("/zones", tags=["Zones"])
async def create_zone(name: str, leader_device_id: Optional[int] = None, order_id: Optional[int] = None):
    """Create a new zone."""
    if not tado_api:
        raise HTTPException(status_code=503, detail="API not initialized")
    
    conn = sqlite3.connect(tado_api.state_manager.db_path)
    cursor = conn.execute("""
        INSERT INTO zones (name, leader_device_id, order_id)
        VALUES (?, ?, ?)
    """, (name, leader_device_id, order_id))
    zone_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    # Reload device cache to pick up zone info
    tado_api.state_manager._load_device_cache()
    
    return {'zone_id': zone_id, 'name': name}

@app.put("/zones/{zone_id}", tags=["Zones"])
async def update_zone(zone_id: int, name: Optional[str] = None, leader_device_id: Optional[int] = None, order_id: Optional[int] = None):
    """Update a zone."""
    if not tado_api:
        raise HTTPException(status_code=503, detail="API not initialized")
    
    conn = sqlite3.connect(tado_api.state_manager.db_path)
    
    updates = []
    params = []
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if leader_device_id is not None:
        updates.append("leader_device_id = ?")
        params.append(leader_device_id)
    if order_id is not None:
        updates.append("order_id = ?")
        params.append(order_id)
    
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    
    params.append(zone_id)
    conn.execute(f"UPDATE zones SET {', '.join(updates)} WHERE zone_id = ?", params)
    conn.commit()
    conn.close()
    
    # Reload device cache
    tado_api.state_manager._load_device_cache()
    
    return {'zone_id': zone_id, 'updated': True}

@app.put("/devices/{device_id}/zone", tags=["Devices"])
async def assign_device_to_zone(device_id: int, zone_id: Optional[int] = None):
    """Assign a device to a zone (or remove from zone if zone_id is None)."""
    if not tado_api:
        raise HTTPException(status_code=503, detail="API not initialized")
    
    conn = sqlite3.connect(tado_api.state_manager.db_path)
    conn.execute("UPDATE devices SET zone_id = ? WHERE device_id = ?", (zone_id, device_id))
    conn.commit()
    conn.close()
    
    # Reload device cache
    tado_api.state_manager._load_device_cache()
    
    return {'device_id': device_id, 'zone_id': zone_id, 'updated': True}

@app.get("/devices", tags=["Devices"])
async def get_devices():
    """Get all registered devices with current state."""
    if not tado_api:
        raise HTTPException(status_code=503, detail="API not initialized")
    
    devices = tado_api.state_manager.get_all_devices()
    current_states = tado_api.state_manager.get_current_state()
    
    # Enrich devices with current state
    for device in devices:
        device_id = device['device_id']
        device['current_state'] = current_states.get(device_id, {})
    
    return {
        "devices": devices,
        "count": len(devices)
    }

@app.get("/devices/{id}", tags=["Devices"])
async def get_device(id: int):
    """
    Get specific device with current state by ID (database device_id).
    
    Args:
        id: Device ID (database ID)
    """
    if not tado_api:
        raise HTTPException(status_code=503, detail="API not initialized")
    
    devices = tado_api.state_manager.get_all_devices()
    device = next((d for d in devices if d['device_id'] == id), None)
    
    if not device:
        raise HTTPException(status_code=404, detail=f"Device {id} not found")
    
    device['current_state'] = tado_api.state_manager.get_current_state(id)
    return device

@app.get("/devices/{id}/history", tags=["Devices"])
async def get_device_history(
    id: int,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    limit: int = 100
):
    """
    Get device state history.
    
    Args:
        id: Device ID
        start_time: Start timestamp (Unix epoch)
        end_time: End timestamp (Unix epoch)
        limit: Maximum number of records to return (default: 100)
    """
    if not tado_api:
        raise HTTPException(status_code=503, detail="API not initialized")
    
    history = tado_api.state_manager.get_device_history(
        id, start_time, end_time, limit
    )
    
    return {
        "id": id,
        "history": history,
        "count": len(history)
    }

@app.get("/devices/current-state", tags=["Devices"])
async def get_all_current_states():
    """Get current state for all devices."""
    if not tado_api:
        raise HTTPException(status_code=503, detail="API not initialized")
    
    return {
        "states": tado_api.state_manager.get_current_state(),
        "timestamp": time.time()
    }

@app.post("/thermostats/{accessory_id}/set_temperature", tags=["Tado"])
async def set_thermostat_temperature(accessory_id: int, temperature: float):
    """Set target temperature for a specific thermostat."""
    if not tado_api.pairing:
        raise HTTPException(status_code=503, detail="Bridge not connected")
    
    try:
        # Find the thermostat service and target temperature characteristic
        accessories = tado_api.accessories_cache
        
        for accessory in accessories:
            if accessory.get('aid') == accessory_id:
                services = accessory.get('services', [])
                for service in services:
                    if service.get('type') == 'public.hap.service.thermostat':
                        for char in service.get('characteristics', []):
                            if char.get('type') == 'public.hap.characteristic.target-temperature':
                                # Set the temperature
                                char_iid = char.get('iid')
                                characteristics = [(accessory_id, char_iid, temperature)]
                                await tado_api.pairing.put_characteristics(characteristics)
                                
                                return {
                                    "success": True,
                                    "accessory_id": accessory_id,
                                    "target_temperature": temperature,
                                    "message": f"Set target temperature to {temperature} C"
                                }
        
        raise HTTPException(status_code=404, detail=f"Thermostat {accessory_id} not found")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set temperature: {e}")

@app.get("/events", tags=["Events"])
async def get_events():
    """Server-Sent Events endpoint for real-time updates."""
    
    async def event_publisher():
        # Create a queue for this client
        client_queue = asyncio.Queue()
        tado_api.event_listeners.append(client_queue)
        
        try:
            while True:
                # Wait for events
                try:
                    event_data = await asyncio.wait_for(client_queue.get(), timeout=30)
                    yield f"data: {json.dumps(event_data)}\n\n"
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f"data: {json.dumps({'type': 'keepalive', 'timestamp': time.time()})}\n\n"
                    
        except asyncio.CancelledError:
            pass
        finally:
            # Remove this client's queue
            if client_queue in tado_api.event_listeners:
                tado_api.event_listeners.remove(client_queue)
    
    return StreamingResponse(
        event_publisher(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream"
        }
    )

@app.post("/refresh", tags=["Admin"])
async def refresh_data():
    """Manually refresh accessories data from HomeKit."""
    return await tado_api.refresh_accessories()

@app.get("/debug/characteristics", tags=["Debug"])
async def debug_characteristics():
    """
    Compare cached values vs live polled values to identify characteristics that don't send events.
    This helps diagnose why some values (like humidity) aren't updating via events.
    """
    if not tado_api or not tado_api.pairing:
        raise HTTPException(status_code=503, detail="API not initialized")
    
    # Get all readable characteristics
    chars_to_check = []
    char_info = {}
    
    for accessory in tado_api.accessories_cache:
        aid = accessory.get('aid')
        device_id = accessory.get('id')
        serial = accessory.get('serial_number')
        
        for service in accessory.get('services', []):
            service_name = get_service_name(service.get('type', ''))
            
            for char in service.get('characteristics', []):
                iid = char.get('iid')
                char_type = char.get('type', '').lower()
                char_name = get_characteristic_name(char_type)
                perms = char.get('perms', [])
                cached_value = char.get('value')
                
                if 'pr' in perms:  # Readable
                    chars_to_check.append((aid, iid))
                    char_info[(aid, iid)] = {
                        'device_id': device_id,
                        'serial_number': serial,
                        'aid': aid,
                        'iid': iid,
                        'service': service_name,
                        'characteristic': char_name,
                        'type': char_type,
                        'cached_value': cached_value,
                        'has_events': 'ev' in perms,
                        'perms': perms
                    }
    
    logger.info(f"Polling {len(chars_to_check)} characteristics for debug comparison...")
    
    # Poll all characteristics in batches
    batch_size = 20
    differences = []
    no_event_changes = []
    
    for i in range(0, len(chars_to_check), batch_size):
        batch = chars_to_check[i:i+batch_size]
        
        try:
            results = await tado_api.pairing.get_characteristics(batch)
            
            for (aid, iid) in batch:
                if (aid, iid) in results:
                    live_value = results[(aid, iid)].get('value')
                    info = char_info[(aid, iid)]
                    cached_value = info['cached_value']
                    
                    # Update info with live value
                    info['live_value'] = live_value
                    info['values_match'] = (live_value == cached_value)
                    
                    # Track differences
                    if live_value != cached_value:
                        differences.append(info.copy())
                        
                        # Highlight characteristics that changed but don't have event support
                        # or have events but didn't fire
                        if not info['has_events']:
                            no_event_changes.append({
                                **info,
                                'reason': 'no_event_permission'
                            })
                        else:
                            no_event_changes.append({
                                **info,
                                'reason': 'has_events_but_didnt_fire'
                            })
                            
        except Exception as e:
            logger.error(f"Error polling batch: {e}")
    
    return {
        "total_characteristics": len(chars_to_check),
        "differences_found": len(differences),
        "differences": differences,
        "no_event_changes": no_event_changes,
        "note": "Characteristics in 'no_event_changes' either don't support events or have events but didn't fire when value changed"
    }

@app.get("/debug/humidity", tags=["Debug"])
async def debug_humidity():
    """
    Specific debug endpoint for humidity characteristics.
    Shows all humidity sensors and their current status.
    """
    if not tado_api or not tado_api.pairing:
        raise HTTPException(status_code=503, detail="API not initialized")
    
    humidity_chars = []
    
    for accessory in tado_api.accessories_cache:
        aid = accessory.get('aid')
        device_id = accessory.get('id')
        serial = accessory.get('serial_number')
        
        for service in accessory.get('services', []):
            for char in service.get('characteristics', []):
                char_type = char.get('type', '').lower()
                
                if char_type == DeviceStateManager.CHAR_CURRENT_HUMIDITY:
                    iid = char.get('iid')
                    perms = char.get('perms', [])
                    cached_value = char.get('value')
                    
                    # Poll live value
                    try:
                        results = await tado_api.pairing.get_characteristics([(aid, iid)])
                        live_value = results[(aid, iid)].get('value') if (aid, iid) in results else None
                    except Exception as e:
                        live_value = f"Error: {e}"
                    
                    # Get event tracking info
                    char_key = (aid, iid)
                    last_event_value = tado_api.change_tracker['last_values'].get(char_key)
                    has_event_subscription = char_key in tado_api.change_tracker['event_characteristics']
                    
                    # Get state manager value
                    state_value = None
                    if device_id:
                        state = tado_api.state_manager.get_current_state(device_id)
                        state_value = state.get('humidity')
                    
                    humidity_chars.append({
                        'device_id': device_id,
                        'serial_number': serial,
                        'aid': aid,
                        'iid': iid,
                        'permissions': perms,
                        'has_events': 'ev' in perms,
                        'has_event_subscription': has_event_subscription,
                        'cached_value': cached_value,
                        'live_value': live_value,
                        'state_manager_value': state_value,
                        'last_event_value': last_event_value,
                        'cached_vs_live_match': cached_value == live_value,
                        'issue': 'CACHED_STALE' if cached_value != live_value else 'OK'
                    })
    
    return {
        "humidity_sensors": humidity_chars,
        "count": len(humidity_chars),
        "note": "If cached_vs_live_match is False and has_events is True, the device isn't sending humidity events"
    }

bridge_pairing: Optional[IpPairing] = None

async def main(args):
    global bridge_pairing, tado_api
    
    try:
        # Initialize database and pairing
        db_path = Path(os.path.expanduser(args.state))
        
        # Initialize the API with database path
        tado_api = TadoLocalAPI(str(db_path))
        
        # Set up pairing
        bridge_pairing = await TadoBridge.pair_or_load(args.bridge_ip, args.pin, db_path, args.clear_pairings)
        
        # Initialize the API with the pairing
        await tado_api.initialize(bridge_pairing)
        
        logger.info(f"*** Tado Local API ready! ***")
        logger.info(f"Bridge IP: {args.bridge_ip}")
        logger.info(f"API Server: http://0.0.0.0:{args.port}")
        logger.info(f"Documentation: http://0.0.0.0:{args.port}/docs")
        logger.info(f"Status: http://0.0.0.0:{args.port}/status")
        logger.info(f"Thermostats: http://0.0.0.0:{args.port}/thermostats")
        logger.info(f"Live Events: http://0.0.0.0:{args.port}/events")
        
        # Start the FastAPI server
        config = uvicorn.Config(
            app, 
            host="0.0.0.0", 
            port=args.port, 
            log_level="info",
            access_log=True
        )
        server = uvicorn.Server(config)
        await server.serve()
        
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down gracefully...")
    except Exception as e:
        logger.error(f"ERROR: Failed to start Tado Local API: {e}")
        raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Tado Local API - REST API for Tado devices via HomeKit bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Initial pairing (first time setup)
  python proxy.py --bridge-ip 192.168.1.100 --pin 123-45-678
  
  # Start API server with existing pairing
  python proxy.py --bridge-ip 192.168.1.100
  
  # Custom port and database location
  python proxy.py --bridge-ip 192.168.1.100 --port 8080 --state ./my-tado.db
  
API Endpoints:
  GET  /               - API information
  GET  /status         - System status
  GET  /accessories    - All HomeKit accessories
  GET  /zones          - All Tado zones
  GET  /thermostats    - All thermostats with temperatures
  POST /thermostats/{id}/set_temperature - Set thermostat temperature
  GET  /events         - Server-Sent Events for real-time updates
  POST /refresh        - Manually refresh data
        """
    )
    parser.add_argument("--state", default="~/.tado-local.db", 
                       help="Path to state database (default: ~/.tado-local.db)")
    parser.add_argument("--bridge-ip", 
                       help="IP of the Tado bridge (e.g., 192.168.1.100). If not provided, will auto-discover from existing pairings.")
    parser.add_argument("--pin", 
                       help="HomeKit PIN for initial pairing (XXX-XX-XXX format)")
    parser.add_argument("--port", type=int, default=4407, 
                       help="Port for REST API server (default: 4407)")
    parser.add_argument("--clear-pairings", action="store_true",
                       help="Clear all existing pairings from database before starting")
    args = parser.parse_args()

    # Run with proper error handling
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\n*** Shutdown complete ***")
    except Exception as e:
        print(f"\nERROR: {e}")
        exit(1)
