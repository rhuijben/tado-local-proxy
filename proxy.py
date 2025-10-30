#!/usr/bin/env python3
import argparse
import asyncio

import asyncio
import json
import logging
import os
import sqlite3
import time
import traceback
import uuid
import asyncio

from collections import defaultdict
from typing import Dict, List, Any, Optional


from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from pathlib import Path
from typing import Optional

from aiohomekit.controller.ip.connection import HomeKitConnection
from aiohomekit.protocol import perform_pair_setup_part1, perform_pair_setup_part2
from aiohomekit.utils import check_pin_format, pair_with_auth
from aiohomekit.controller.ip.pairing import IpPairing
from aiohomekit.controller.ip.controller import IpController
from aiohomekit.controller.ip.connection import HomeKitConnection
from aiohomekit.protocol import perform_pair_setup_part2
from aiohomekit.utils import check_pin_format
from aiohomekit.controller.ip.connection import HomeKitConnection
from aiohomekit.protocol import perform_pair_setup_part1, perform_pair_setup_part2
from aiohomekit.utils import check_pin_format, pair_with_auth
from aiohomekit.controller.ip.connection import HomeKitConnection
from aiohomekit.protocol import perform_pair_setup_part1, perform_pair_setup_part2
from aiohomekit.utils import check_pin_format, pair_with_auth
from aiohomekit.controller import Controller
        
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
import uvicorn

from zeroconf.asyncio import AsyncZeroconf

from homekit_uuids import enhance_accessory_data, get_service_name, get_characteristic_name

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SimpleCharacteristicCache:
    """Simple cache implementation for aiohomekit"""
    
    def __init__(self):
        self._maps = {}
    
    def get_map(self, pairing_id):
        """Get the map for a specific pairing"""
        # Return empty structure if no cache exists yet
        return self._maps.get(pairing_id, {'accessories': [], 'state_num': 0})
    
    async def async_create_or_update_map(self, pairing_id, accessories, state_num, *args, **kwargs):
        """Create or update the characteristic map for a pairing
        
        Args:
            pairing_id: The pairing identifier
            accessories: The accessories data
            state_num: The state number
            *args: Additional positional arguments (ignored)
            **kwargs: Additional keyword arguments (ignored)
        """
        self._maps[pairing_id] = {
            'accessories': accessories,
            'state_num': state_num,
            'updated_at': time.time()
        }
    
    async def async_delete_map(self, pairing_id):
        """Delete the map for a specific pairing"""
        self._maps.pop(pairing_id, None)

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
            print(f"✓ Using existing controller identity: {controller_id}")
            
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
            
            print(f"✓ Created new controller identity: {controller_id}")
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
        print("✓ Saved Part 1 pairing session for potential resumption")

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
            print("✓ Found saved Part 1 pairing session")
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
                print("🔄 Found saved Part 1 session, attempting to resume with Part 2...")
                try:
                    result = await TadoBridge.perform_part2_only(host, port, pin, controller_id, salt, part1_public_key, db_path)
                    if result:
                        await TadoBridge.clear_pairing_session(db_path, host)
                        return result
                except Exception as e:
                    print(f"❌ Failed to resume from saved session: {e}")
                    print("🔄 Will start fresh pairing...")
        
        # Perform fresh pairing with persistent identity
        return await TadoBridge.perform_fresh_pairing(host, port, pin, controller_id, db_path)

    @staticmethod
    async def perform_part2_only(host: str, port: int, pin: str, controller_id: str, salt: bytes, part1_public_key: bytes, db_path: str):
        """Resume pairing from Part 2 using saved Part 1 state."""

        check_pin_format(pin)
        
        print(f"🔄 Resuming Part 2 with saved state for controller {controller_id[:8]}...")
        
        connection = HomeKitConnection(owner=None, hosts=[host], port=port)
        
        try:
            await connection.ensure_connection()
            print("✓ Connection established for Part 2")
            
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
                print("✓ Part 2 successful - pairing complete using saved session!")
                
                pairing_data["AccessoryIP"] = host
                pairing_data["AccessoryPort"] = port
                pairing_data["Connection"] = "IP"
                
                return pairing_data
                
        except Exception as e:
            print(f"❌ Part 2 resumption failed: {e}")
            raise
        finally:
            await connection.close()

    @staticmethod
    async def perform_fresh_pairing(host: str, port: int, pin: str, controller_id: str, db_path: str):
        """Perform fresh pairing with persistent controller identity."""
        
        check_pin_format(pin)
        
        print(f"🆕 Starting fresh pairing with persistent controller {controller_id[:8]}...")
        
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
                    print("✓ Connection established")
                    
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
                        print(f"✓ Part 1 successful with feature flags {feature_flags}")
                        
                        # Save Part 1 state in case Part 2 fails
                        await TadoBridge.save_pairing_session(db_path, host, controller_id, salt, part1_public_key)
                        
                        # Handle connection between parts based on approach
                        if approach_name == "reconnect_between_parts":
                            print("Closing and reopening connection between parts...")
                            await connection.close()
                            await asyncio.sleep(0.5)
                            connection = HomeKitConnection(owner=None, hosts=[host], port=port)
                            await connection.ensure_connection()
                            print("✓ Reconnected")
                        
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
                                print(f"✓ Part 2 successful with approach {approach_name}, feature flags {feature_flags}")
                                
                                # If we get here, pairing succeeded!
                                print(f"\n🎉 PAIRING SUCCESS!")
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
                            print(f"❌ Part 2 failed with approach {approach_name}, feature flags {feature_flags}: {e}")
                            print(f"Error type: {type(e)}")
                            print("ℹ️  Part 1 state saved - you can retry and we'll attempt to resume from Part 2")
                            await connection.close()
                            continue
                        
                except Exception as e:
                    print(f"❌ Overall attempt failed with approach {approach_name}, feature flags {feature_flags}: {e}")
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
        print("⚠️  perform_manual_pairing is deprecated - use perform_pairing with db_path instead")
        # Use default db path for backward compatibility
        db_path = str(Path.home() / ".tado-local.db")
        return await TadoBridge.perform_pairing(host, port, pin, db_path)

    @staticmethod
    async def perform_alternative_pairing(host: str, port: int, pin: str):
        """Try alternative pairing approach with different timing and connection handling."""
        
        print("🔄 Trying alternative pairing approach...")
        print("- Using longer timeouts")
        print("- Different connection handling")
        print("- Modified feature flags")
        
        # Try with just feature flags 0 and 1, but with different timing
        for feature_flags in [0, 1]:
            print(f"\n🧪 Testing alternative approach with feature flags {feature_flags}")
            
            connection = HomeKitConnection(owner=None, hosts=[host], port=port)
            
            try:
                # Longer connection timeout
                await connection.ensure_connection()
                print("✓ Connection established")
                
                # Try to get pairing info first
                try:
                    response = await connection.get("/pair-setup")
                    print(f"Pair-setup status: {response}")
                except Exception as e:
                    print(f"Cannot query pair-setup status: {e}")
                
                # Wait a moment before starting pairing
                await asyncio.sleep(2)
                
                print(f"🚀 Starting pairing protocol with feature flags {feature_flags}")
                
                # Part 1: Modified approach
                state_machine = perform_pair_setup_part1(pair_with_auth(feature_flags))
                request, expected = state_machine.send(None)
                
                # Send with longer timeout expectations
                print("📤 Sending initial pairing request...")
                response = await connection.post_tlv(
                    "/pair-setup",
                    body=request,
                    expected=expected,
                )
                
                print(f"📥 Got response: {type(response)}")
                
                # Process the state machine with better error handling
                try:
                    request, expected = state_machine.send(response)
                    
                    # Continue the conversation
                    while True:
                        print("📤 Continuing pairing conversation...")
                        response = await connection.post_tlv(
                            "/pair-setup",
                            body=request,
                            expected=expected,
                        )
                        request, expected = state_machine.send(response)
                        
                except StopIteration as result:
                    salt, pub_key = result.value
                    print(f"✅ Part 1 completed with feature flags {feature_flags}")
                    
                    # Close and create fresh connection for part 2
                    await connection.close()
                    await asyncio.sleep(1)  # Brief pause between parts
                    
                    connection = HomeKitConnection(owner=None, hosts=[host], port=port)
                    await connection.ensure_connection()
                    
                    print("🔑 Starting Part 2 with PIN...")
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
                        print("🎉 Alternative pairing approach succeeded!")
                        
                        pairing_data["AccessoryIP"] = host
                        pairing_data["AccessoryPort"] = port
                        pairing_data["Connection"] = "IP"
                        
                        return pairing_data
                        
            except Exception as e:
                print(f"❌ Alternative approach failed with feature flags {feature_flags}: {e}")
                
            finally:
                await connection.close()
        
    @staticmethod
    async def perform_simple_pairing(host: str, port: int, pin: str):
        """Simplified pairing approach to isolate the UnavailableError issue."""
        
        print("🔬 SIMPLIFIED PAIRING APPROACH")
        print("=" * 40)
        
        # Test basic connectivity first
        print("1️⃣ Testing basic connectivity...")
        connection = HomeKitConnection(owner=None, hosts=[host], port=port)
        
        try:
            await connection.ensure_connection()
            print("✅ Basic connection: SUCCESS")
        except Exception as e:
            print(f"❌ Basic connection: FAILED - {e}")
            raise
        finally:
            await connection.close()
        
        # Test pair-setup endpoint accessibility
        print("\n2️⃣ Testing pair-setup endpoint...")
        connection = HomeKitConnection(owner=None, hosts=[host], port=port)
        
        try:
            await connection.ensure_connection()
            
            # Try GET first to see if endpoint is responsive
            try:
                response = await connection.get("/pair-setup")
                print(f"✅ pair-setup GET: {response}")
            except Exception as e:
                print(f"⚠️ pair-setup GET failed: {e}")
                # This might be normal for some devices
                
        except Exception as e:
            print(f"❌ pair-setup endpoint test: FAILED - {e}")
            raise
        finally:
            await connection.close()
        
        # Now try the actual pairing with minimal complexity
        print("\n3️⃣ Attempting minimal pairing...")
        connection = HomeKitConnection(owner=None, hosts=[host], port=port)
        
        try:
            await connection.ensure_connection()
            
            # Use only feature flags 0 (most basic)
            print("📤 Sending M1 (pair-setup start) with feature flags 0...")
            
            state_machine = perform_pair_setup_part1(pair_with_auth(0))
            request, expected = state_machine.send(None)
            
            print(f"📋 Request type: {type(request)}")
            print(f"📋 Expected response: {expected}")
            
            # Send the request and see exactly what we get back
            response = await connection.post_tlv(
                "/pair-setup",
                body=request,
                expected=expected,
            )
            
            print(f"📥 Response type: {type(response)}")
            print(f"📥 Response content: {response}")
            
            # Check for error codes in the response
            if hasattr(response, 'get'):
                error_code = response.get(b'\x02')  # kTLVType_Error
                if error_code:
                    print(f"🚨 Device returned error code: {error_code}")
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
                    print("\n🔍 DEVICE STATUS ANALYSIS:")
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
                print("✅ M1 successful, continuing with pairing process...")
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
                print("🎉 Part 1 (M1-M2) completed successfully!")
                print("🔑 Starting Part 2 with PIN...")
                
                # Close connection and create fresh one for Part 2
                await connection.close()
                
                # Wait a moment before Part 2
                await asyncio.sleep(2)
                
                connection = HomeKitConnection(owner=None, hosts=[host], port=port)
                await connection.ensure_connection()
                print("✅ Fresh connection established for Part 2")
                
                # Part 2: Complete pairing with PIN
                state_machine = perform_pair_setup_part2(pin, str(uuid.uuid4()), salt, pub_key)
                request, expected = state_machine.send(None)
                
                print("📤 Sending Part 2 M3 message...")
                
                try:
                    while True:
                        response = await connection.post_tlv(
                            "/pair-setup",
                            body=request,
                            expected=expected,
                        )
                        print(f"📥 Part 2 response: {type(response)}")
                        request, expected = state_machine.send(response)
                except StopIteration as result:
                    pairing_data = result.value
                    print("🎉 Part 2 completed - PAIRING SUCCESSFUL!")
                    
                    # Add connection info to pairing data
                    pairing_data["AccessoryIP"] = host
                    pairing_data["AccessoryPort"] = port
                    pairing_data["Connection"] = "IP"
                    
                    return pairing_data
            
        except Exception as e:
            print(f"❌ Pairing failed: {e}")
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
            print("🧹 Cleared all existing pairings as requested")

        # Get all existing pairings
        all_pairings = conn.execute("SELECT bridge_ip, pairing_data FROM pairings").fetchall()
        
        if all_pairings:
            print(f"📋 Found {len(all_pairings)} existing pairing(s):")
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
                print(f"✅ Found existing pairing for specified IP: {bridge_ip}")
            else:
                print(f"⚠️  No existing pairing found for specified IP: {bridge_ip}")
        else:
            # No bridge IP specified, auto-select if only one pairing exists
            if len(all_pairings) == 1:
                pairing_data = json.loads(all_pairings[0][1])
                selected_bridge_ip = all_pairings[0][0]
                print(f"🎯 Auto-selected the only existing pairing: {selected_bridge_ip}")
            elif len(all_pairings) > 1:
                print(f"❓ Multiple pairings found. Please specify --bridge-ip with one of:")
                for ip, _ in all_pairings:
                    print(f"   --bridge-ip {ip}")
                raise RuntimeError("Multiple pairings available. Please specify --bridge-ip.")
            else:
                print(f"ℹ️  No existing pairings found.")

        # If we have existing pairing data, test it first
        if pairing_data is not None:
            print(f"🔄 Testing existing pairing for {selected_bridge_ip}...")
            
            # Create a controller with proper async context
            try:
                # Create async zeroconf instance 
                zeroconf_instance = AsyncZeroconf()
                
                # Create character cache (use our simple implementation)
                char_cache = SimpleCharacteristicCache()
                
                # Create controller with proper dependencies
                controller = IpController(char_cache=char_cache, zeroconf_instance=zeroconf_instance)
                
                # Create pairing with controller instance
                pairing = IpPairing(controller, pairing_data)
                
                # Test connection
                await pairing._ensure_connected()
                accessories = await pairing.list_accessories_and_characteristics()
                print(f"✅ Successfully connected to {selected_bridge_ip}!")
                print(f"🏠 Found {len(accessories)} accessories")
                
                return pairing
                
            except Exception as e:
                print(f"❌ Failed to connect to existing pairing: {e}")
                print(f"⚠️  Connection failed, but keeping pairing data (may be temporary network issue)")
                print(f"💡 To force re-pairing, delete the pairing manually or use --pin to create a new one")
                
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
                print("✓ Pairing successful and saved to database!")
                
                # Create pairing instance with the new data
                # Create a controller instance for the pairing
                
                zeroconf_instance = AsyncZeroconf()
                char_cache = SimpleCharacteristicCache()
                controller = IpController(char_cache=char_cache, zeroconf_instance=zeroconf_instance)
                pairing = IpPairing(controller, pairing_data)
                await pairing._ensure_connected()
                await pairing.list_accessories_and_characteristics()
                print("✓ Connected and fetched accessories!")
                
                return pairing
                
            except Exception as e:
                print(f"❌ Pairing failed: {e}")
                
                # Provide enhanced error messages based on Home Assistant's approach
                if "UnavailableError" in str(type(e)) or "Unavailable" in str(e):
                    print("\n" + "="*60)
                    print("DEVICE REPORTS 'UNAVAILABLE' FOR PAIRING")
                    print("="*60)
                    print("Based on Home Assistant's approach, this typically means:")
                    print("1. 🔗 Device is already paired to another HomeKit controller")
                    print("2. 🔄 Device needs to be reset to clear existing pairings")
                    print("3. 📱 Device might be paired to iPhone/iPad/Mac HomeKit")
                    print("4. 🏠 Device might be paired to another Home Assistant instance")
                    print("")
                    print("🛠️  SOLUTIONS TO TRY:")
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
            print(f"🔗 Starting controller-based pairing with {host}:{port} using PIN: {hap_pin}")
            
            # Default db path if not provided
            if not db_path:
                db_path = str(Path.home() / ".tado-local.db")
            
            # Get or create persistent controller identity
            controller_id, private_key, public_key = await TadoBridge.get_or_create_controller_identity(db_path)
            print(f"📱 Using Controller ID: {controller_id}")
            
            try:
                # Create required dependencies for Controller
                
                # Create AsyncZeroconf instance
                zeroconf_instance = AsyncZeroconf()
                
                # Create character cache (simple dict)
                char_cache = {}
                
                # Create the main Controller (not IpController)
                controller = Controller(
                    async_zeroconf_instance=zeroconf_instance,
                    char_cache=char_cache
                )
                
                print(f"🔧 Created controller with proper dependencies")
                
                # Start pairing using the controller's built-in method
                print(f"🚀 Starting pairing process...")
                
                # This should use the controller's pairing method which returns an IpPairing
                pairing = await controller.start_pairing(host, hap_pin)
                
                print(f"✅ Pairing completed successfully!")
                
                # Clean up zeroconf instance
                await zeroconf_instance.async_close()
                
                # Extract pairing data in the correct format
                pairing_data = pairing.pairing_data
                
                print(f"🎉 PAIRING SUCCESS! Controller-based approach, Controller ID: {controller_id}")
                
                return pairing_data
                
            except Exception as e:
                print(f"❌ Controller-based pairing failed: {e}")
                traceback.print_exc()
                raise
                
        except Exception as e:
            print(f"💥 Pairing failed with error: {e}")
            traceback.print_exc()
            raise

# FastAPI app with HomeKit event handling
app = FastAPI(
    title="Tado Local API",
    description="Local REST API for Tado devices via HomeKit bridge",
    version="1.0.0"
)

class TadoLocalAPI:
    """Tado Local API that leverages HomeKit for real-time data without cloud dependency."""
    
    def __init__(self):
        self.pairing: Optional[IpPairing] = None
        self.accessories_cache: Dict[str, Any] = {}
        self.event_listeners: List[asyncio.Queue] = []
        self.last_update: Optional[float] = None
        self.device_states: Dict[str, Dict[str, Any]] = defaultdict(dict)
        
    async def initialize(self, pairing: IpPairing):
        """Initialize the API with a HomeKit pairing."""
        self.pairing = pairing
        await self.refresh_accessories()
        await self.setup_event_listeners()
        logger.info("Tado Local API initialized successfully")
        
    async def refresh_accessories(self):
        """Refresh accessories from HomeKit and cache them."""
        if not self.pairing:
            raise HTTPException(status_code=503, detail="Bridge not connected")
            
        try:
            self.accessories_cache = await self.pairing.list_accessories_and_characteristics()
            self.last_update = time.time()
            logger.info(f"Refreshed {len(self.accessories_cache)} accessories")
            return self.accessories_cache
        except Exception as e:
            logger.error(f"Failed to refresh accessories: {e}")
            raise HTTPException(status_code=503, detail=f"Failed to refresh accessories: {e}")
    
    async def setup_event_listeners(self):
        """Setup HomeKit event listeners for real-time updates."""
        if not self.pairing:
            return
            
        try:
            # Subscribe to HomeKit events for real-time updates
            def event_callback(update_data):
                """Handle HomeKit characteristic updates."""
                asyncio.create_task(self.handle_homekit_event(update_data))
            
            # Subscribe to all accessories for updates
            await self.pairing.subscribe(self.accessories_cache)
            logger.info("HomeKit event listeners setup successfully")
            
        except Exception as e:
            logger.warning(f"Failed to setup event listeners: {e}")
    
    async def handle_homekit_event(self, event_data):
        """Handle incoming HomeKit events and update device states."""
        try:
            # Update device states from HomeKit events
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
                        
                logger.debug(f"Updated device state: aid={aid}, iid={iid}, value={value}")
                
        except Exception as e:
            logger.error(f"Error handling HomeKit event: {e}")

# Global API instance
tado_api = TadoLocalAPI()

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
            "accessories": "/accessories", 
            "zones": "/zones",
            "thermostats": "/thermostats",
            "events": "/events",
            "refresh": "/refresh"
        }
    }

@app.get("/status", tags=["Status"])
async def get_status():
    """Get overall system status."""
    if not tado_api.pairing:
        raise HTTPException(status_code=503, detail="Bridge not connected")
    
    try:
        # Test connection
        await tado_api.pairing.list_accessories_and_characteristics()
        
        return {
            "status": "connected",
            "bridge_connected": True,
            "last_update": tado_api.last_update,
            "cached_accessories": len(tado_api.accessories_cache),
            "active_listeners": len(tado_api.event_listeners),
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

@app.get("/homekit/uuid-mappings", tags=["HomeKit"])
async def get_uuid_mappings():
    """
    Get all HomeKit UUID mappings found in the current system.
    Shows which services and characteristics are being used by your Tado devices.
    """
    if not tado_api.accessories_cache:
        await tado_api.refresh_accessories()
    
    # Collect all unique UUIDs from the current accessories
    service_uuids = set()
    characteristic_uuids = set()
    
    for accessory in tado_api.accessories_cache:
        for service in accessory.get('services', []):
            service_uuids.add(service.get('type'))
            for char in service.get('characteristics', []):
                characteristic_uuids.add(char.get('type'))
    
    # Create mappings with names
    services_found = {}
    for uuid in sorted(service_uuids):
        services_found[uuid] = get_service_name(uuid)
    
    characteristics_found = {}
    for uuid in sorted(characteristic_uuids):
        characteristics_found[uuid] = get_characteristic_name(uuid)
    
    return {
        "services": services_found,
        "characteristics": characteristics_found,
        "summary": {
            "total_services": len(services_found),
            "total_characteristics": len(characteristics_found),
            "accessories_scanned": len(tado_api.accessories_cache)
        }
    }

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
    """Get all thermostat devices with current and target temperatures."""
    if not tado_api.accessories_cache:
        await tado_api.refresh_accessories()
    
    thermostats = []
    accessories = tado_api.accessories_cache
    
    for accessory in accessories:
        services = accessory.get('services', [])
        for service in services:
            if service.get('type') == '0000004A-0000-1000-8000-0026BB765291':
                
                thermostat = {
                    'accessory_id': accessory.get('aid'),
                    'service_id': service.get('iid'),
                    'name': accessory.get('name', f"Thermostat {accessory.get('aid')}"),
                    'current_temperature': None,
                    'target_temperature': None,
                    'heating_state': None,
                    'target_heating_state': None,
                    'humidity': None
                }
                
                # Extract thermostat characteristics
                for char in service.get('characteristics', []):
                    char_type = char.get('type')
                    char_value = char.get('value')
                    
                    if char_type == 'public.hap.characteristic.current-temperature':
                        thermostat['current_temperature'] = char_value
                    elif char_type == 'public.hap.characteristic.target-temperature':
                        thermostat['target_temperature'] = char_value
                    elif char_type == 'public.hap.characteristic.current-heating-cooling-state':
                        thermostat['heating_state'] = char_value
                    elif char_type == 'public.hap.characteristic.target-heating-cooling-state':
                        thermostat['target_heating_state'] = char_value
                    elif char_type == 'public.hap.characteristic.current-relative-humidity':
                        thermostat['humidity'] = char_value
                
                thermostats.append(thermostat)
    
    return {"thermostats": thermostats, "count": len(thermostats)}

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
                                    "message": f"Set target temperature to {temperature}°C"
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

bridge_pairing: Optional[IpPairing] = None

async def main(args):
    global bridge_pairing
    
    try:
        # Initialize database and pairing
        db_path = Path(os.path.expanduser(args.state))
        bridge_pairing = await TadoBridge.pair_or_load(args.bridge_ip, args.pin, db_path, args.clear_pairings)
        
        # Initialize the API with the pairing
        await tado_api.initialize(bridge_pairing)
        
        logger.info(f"🎉 Tado Local API ready!")
        logger.info(f"🌐 Bridge IP: {args.bridge_ip}")
        logger.info(f"🔌 API Server: http://0.0.0.0:{args.port}")
        logger.info(f"📚 Documentation: http://0.0.0.0:{args.port}/docs")
        logger.info(f"📊 Status: http://0.0.0.0:{args.port}/status")
        logger.info(f"🌡️  Thermostats: http://0.0.0.0:{args.port}/thermostats")
        logger.info(f"📡 Live Events: http://0.0.0.0:{args.port}/events")
        
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
        
    except Exception as e:
        logger.error(f"❌ Failed to start Tado Local API: {e}")
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

    asyncio.run(main(args))
