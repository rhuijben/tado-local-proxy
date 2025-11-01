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
"""HomeKit bridge pairing and connection management."""

import asyncio
import json
import logging
import sqlite3
import traceback
import uuid
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from aiohomekit.controller.ip.connection import HomeKitConnection
from aiohomekit.controller.ip.pairing import IpPairing
from aiohomekit.controller.ip.controller import IpController
from aiohomekit.protocol import perform_pair_setup_part1, perform_pair_setup_part2
from aiohomekit.utils import check_pin_format, pair_with_auth
from aiohomekit.controller import Controller

# zeroconf import kept for initial pairing only - not needed for normal operation
from zeroconf.asyncio import AsyncZeroconf

from .cache import CharacteristicCacheSQLite
from .database import DB_SCHEMA

logger = logging.getLogger('tado-local')


class TadoBridge:
    """Manages HomeKit bridge pairing and connection."""

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
            logger.debug(f"Using existing controller identity: {controller_id}")

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

            logger.info(f"Created new controller identity: {controller_id}")
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
        logger.debug("Saved Part 1 pairing session for potential resumption")

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
            logger.debug("Found saved Part 1 pairing session")
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
                logger.info("=> Found saved Part 1 session, attempting to resume with Part 2...")
                try:
                    result = await TadoBridge.perform_part2_only(host, port, pin, controller_id, salt, part1_public_key, db_path)
                    if result:
                        await TadoBridge.clear_pairing_session(db_path, host)
                        return result
                except Exception as e:
                    logger.warning(f"Failed to resume from saved session: {e}")
                    logger.info("Will start fresh pairing...")

        # Perform fresh pairing with persistent identity
        return await TadoBridge.perform_fresh_pairing(host, port, pin, controller_id, db_path)

    @staticmethod
    async def perform_part2_only(host: str, port: int, pin: str, controller_id: str, salt: bytes, part1_public_key: bytes, db_path: str):
        """Resume pairing from Part 2 using saved Part 1 state."""

        check_pin_format(pin)

        logger.info(f"Resuming Part 2 with saved state for controller {controller_id[:8]}...")

        connection = HomeKitConnection(owner=None, hosts=[host], port=port)

        try:
            await connection.ensure_connection()
            logger.debug("Connection established for Part 2")

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
                logger.info("Part 2 successful - pairing complete using saved session!")

                pairing_data["AccessoryIP"] = host
                pairing_data["AccessoryPort"] = port
                pairing_data["Connection"] = "IP"

                return pairing_data

        except Exception as e:
            logger.error(f"Part 2 resumption failed: {e}")
            raise
        finally:
            await connection.close()

    @staticmethod
    async def perform_fresh_pairing(host: str, port: int, pin: str, controller_id: str, db_path: str):
        """Perform fresh pairing with persistent controller identity."""

        check_pin_format(pin)

        logger.info(f"Starting fresh pairing with persistent controller {controller_id[:8]}...")

        # Try different approaches as before, but with persistent controller ID
        approaches = [
            ("single_connection", "Keep connection open throughout"),
            ("reconnect_between_parts", "Reconnect between Part 1 and Part 2"),
        ]

        for approach_name, approach_desc in approaches:
            logger.info(f"--- Trying approach: {approach_name} ---")
            logger.info(f"Description: {approach_desc}")

            # Try different feature flag values for each approach
            feature_flag_variations = [0, 1]

            for feature_flags in feature_flag_variations:
                connection = None
                try:
                    logger.debug(f"Trying feature flags: {feature_flags}")

                    # Create initial connection
                    logger.debug(f"Connecting to {host}:{port}...")
                    connection = HomeKitConnection(owner=None, hosts=[host], port=port)
                    await connection.ensure_connection()
                    logger.debug("Connection established")

                    # Part 1: Start pairing
                    logger.debug(f"Starting Part 1 with feature flags {feature_flags}...")
                    state_machine = perform_pair_setup_part1(pair_with_auth(feature_flags))
                    request, expected = state_machine.send(None)

                    logger.debug(f"Sending pair-setup request...")
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
                        logger.info(f"Part 1 successful with feature flags {feature_flags}")

                        # Save Part 1 state in case Part 2 fails
                        await TadoBridge.save_pairing_session(db_path, host, controller_id, salt, part1_public_key)

                        # Handle connection between parts based on approach
                        if approach_name == "reconnect_between_parts":
                            logger.debug("Closing and reopening connection between parts...")
                            await connection.close()
                            await asyncio.sleep(0.5)
                            connection = HomeKitConnection(owner=None, hosts=[host], port=port)
                            await connection.ensure_connection()
                            logger.debug("Reconnected")

                        # Part 2: Complete pairing with PIN
                        logger.debug("Starting Part 2 with PIN...")
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
                                logger.info(f"Part 2 successful with approach {approach_name}, feature flags {feature_flags}")

                                # If we get here, pairing succeeded!
                                logger.info(f"*** PAIRING SUCCESS! ***")
                                logger.info(f"Successful approach: {approach_name}")
                                logger.info(f"Feature flags: {feature_flags}")
                                logger.info(f"Controller ID: {controller_id}")

                                pairing_data["AccessoryIP"] = host
                                pairing_data["AccessoryPort"] = port
                                pairing_data["Connection"] = "IP"

                                # Clear the saved session since we completed successfully
                                await TadoBridge.clear_pairing_session(db_path, host)

                                await connection.close()
                                return pairing_data

                        except Exception as e:
                            logger.warning(f"Part 2 failed with approach {approach_name}, feature flags {feature_flags}: {e}")
                            logger.debug(f"Error type: {type(e)}")
                            logger.info("Part 1 state saved - you can retry and we'll attempt to resume from Part 2")
                            await connection.close()
                            continue

                except Exception as e:
                    logger.warning(f"Overall attempt failed with approach {approach_name}, feature flags {feature_flags}: {e}")
                    if connection:
                        await connection.close()
                    continue

            logger.info(f"--- Approach {approach_name} completed, trying next approach ---")

        # If we get here, all attempts failed
        logger.error("============================================================")
        logger.error("ALL PAIRING ATTEMPTS FAILED")
        logger.error("============================================================")
        logger.error("Tried approaches:")
        for approach_name, approach_desc in approaches:
            logger.error(f"- {approach_name}: {approach_desc}")
        logger.error(f"Controller identity persisted: {controller_id}")
        logger.error("Part 1 state may be saved - retry might resume from Part 2")
        logger.error("Possible issues:")
        logger.error("1. Device is already paired to another HomeKit controller")
        logger.error("2. Device is not in pairing mode")
        logger.error("3. Wrong PIN code")
        logger.error("4. Device needs to be reset/factory reset")
        logger.error("5. Network connectivity issues")
        logger.error("6. Device-specific pairing behavior not yet understood")
        logger.error("Troubleshooting steps:")
        logger.error("- Check if device is showing on other HomeKit controllers")
        logger.error("- Try factory resetting the device")
        logger.error("- Verify the PIN is correct from device label")
        logger.error("- Ensure device is in pairing mode")
        logger.error("- Retry - we'll attempt to resume from saved Part 1 state")
        logger.error("============================================================")
        raise Exception("All pairing attempts failed - see troubleshooting info above")

    @staticmethod
    async def pair_or_load(bridge_ip: Optional[str], pin: Optional[str], db_path: Path, clear_pairings: bool = False):
        """Load existing pairing or perform new pairing."""
        conn = sqlite3.connect(db_path)
        conn.executescript(DB_SCHEMA)  # Use executescript for multiple statements
        conn.commit()

        # Clear existing pairings if requested
        if clear_pairings:
            conn.execute("DELETE FROM pairings")
            conn.commit()
            logger.info("Cleared all existing pairings as requested")

        # Get all existing pairings
        all_pairings = conn.execute("SELECT bridge_ip, pairing_data FROM pairings").fetchall()

        if all_pairings:
            logger.info(f"Found {len(all_pairings)} existing pairing(s):")
            for i, (ip, _) in enumerate(all_pairings):
                logger.info(f"  {i+1}. {ip}")

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
                logger.info(f"Found existing pairing for specified IP: {bridge_ip}")
            else:
                logger.info(f"No existing pairing found for specified IP: {bridge_ip}")
        else:
            # No bridge IP specified, auto-select if only one pairing exists
            if len(all_pairings) == 1:
                pairing_data = json.loads(all_pairings[0][1])
                selected_bridge_ip = all_pairings[0][0]
                logger.info(f"Auto-selected the only existing pairing: {selected_bridge_ip}")
            elif len(all_pairings) > 1:
                logger.error(f"Multiple pairings found. Please specify --bridge-ip with one of:")
                for ip, _ in all_pairings:
                    logger.error(f"   --bridge-ip {ip}")
                raise RuntimeError("Multiple pairings available. Please specify --bridge-ip.")
            else:
                logger.info(f"No existing pairings found.")

        # If we have existing pairing data, test it first
        if pairing_data is not None:
            logger.info(f"=> Testing existing pairing for {selected_bridge_ip}...")

            # Create a controller with proper async context
            try:
                if False:
                    zeroconf_instance = AsyncZeroconf()
                else:
                    zeroconf_instance = None

                # Create SQLite-backed characteristic cache
                char_cache = CharacteristicCacheSQLite(str(db_path))

                # Create controller with proper dependencies
                controller = IpController(char_cache=char_cache, zeroconf_instance=zeroconf_instance)

                # Create pairing with controller instance
                pairing = IpPairing(controller, pairing_data)

                # Test connection
                await pairing._ensure_connected()
                accessories = await pairing.list_accessories_and_characteristics()
                logger.info(f"Successfully connected to {selected_bridge_ip}!")
                logger.info(f"Found {len(accessories)} accessories")

                return pairing, selected_bridge_ip

            except Exception as e:
                logger.error(f"Failed to connect to existing pairing: {e}")
                logger.warning(f"Connection failed, but keeping pairing data (may be temporary network issue)")
                logger.info(f"To force re-pairing, delete the pairing manually or use --pin to create a new one")

                # DO NOT remove the pairing data automatically - it might just be a temporary issue
                # conn.execute("DELETE FROM pairings WHERE bridge_ip = ?", (selected_bridge_ip,))
                # conn.commit()

                # Still raise the error so we don't try to continue with a broken connection
                raise RuntimeError(f"Failed to connect to existing pairing for {selected_bridge_ip}: {e}")

        # Need to pair
        if pin:
            if not bridge_ip:
                raise RuntimeError("Bridge IP required for initial pairing with PIN")
            logger.info(f"Starting fresh pairing with {bridge_ip} using PIN {pin}...")

            try:
                if False:
                    zeroconf_instance = AsyncZeroconf()
                else:
                    zeroconf_instance = None

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
                logger.info("Pairing successful and saved to database!")

                # Create pairing instance with the new data
                # Create a controller instance for the pairing

                char_cache = CharacteristicCacheSQLite(str(db_path))
                controller = IpController(char_cache=char_cache, zeroconf_instance=zeroconf_instance)
                pairing = IpPairing(controller, pairing_data)
                await pairing._ensure_connected()
                await pairing.list_accessories_and_characteristics()
                logger.info("Connected and fetched accessories!")

                return pairing, bridge_ip

            except Exception as e:
                logger.error(f"Pairing failed: {e}")

                # Provide enhanced error messages based on Home Assistant's approach
                if "UnavailableError" in str(type(e)) or "Unavailable" in str(e):
                    logger.error("" + "="*60)
                    logger.error("DEVICE REPORTS 'UNAVAILABLE' FOR PAIRING")
                    logger.error("="*60)
                    logger.error("Based on Home Assistant's approach, this typically means:")
                    logger.error("1. Device is already paired to another HomeKit controller")
                    logger.error("2. Device needs to be reset to clear existing pairings")
                    logger.error("3. Device might be paired to iPhone/iPad/Mac HomeKit")
                    logger.error("4. Device might be paired to another Home Assistant instance")
                    logger.error("")
                    logger.error("SOLUTIONS TO TRY:")
                    logger.error("1. Check if device appears in:")
                    logger.error("   - iPhone/iPad Home app")
                    logger.error("   - Other Home Assistant instances")
                    logger.error("   - HomeKit-enabled apps")
                    logger.error("")
                    logger.error("2. If device is paired elsewhere:")
                    logger.error("   - Remove it from that HomeKit controller first")
                    logger.error("   - OR factory reset the device")
                    logger.error("")
                    logger.error("3. For Tado devices specifically:")
                    logger.error("   - Try holding reset button for 10+ seconds")
                    logger.error("   - Look for factory reset procedure in manual")
                    logger.error("   - Some Tado devices require power cycling after reset")
                    logger.error("")
                    logger.error("4. Advanced troubleshooting:")
                    logger.error("   - Check device status flags in mDNS browser")
                    logger.error("   - Look for 'sf=1' (unpaired) vs 'sf=0' (paired)")
                    logger.error("   - Verify device is actually advertising for pairing")
                    logger.error("="*60 + "")
                elif "Already" in str(e):
                    logger.error("Device appears to already be paired.")
                elif "Authentication" in str(type(e)) or "Authentication" in str(e):
                    logger.error("Authentication error - check PIN or try resetting device.")
                elif "BusyError" in str(type(e)) or "Busy" in str(e):
                    logger.error("Device is busy - wait a moment and try again.")

                raise

        raise RuntimeError("No pairing data found and no PIN provided. Provide --pin to pair first.")

    @staticmethod
    async def perform_pairing_with_controller(host: str, port: int = 80, hap_pin: str = "557-15-876", db_path: str = None):
        """
        Perform HomeKit pairing using Controller.start_pairing() method.
        """
        try:
            logger.info(f"Starting controller-based pairing with {host}:{port} using PIN: {hap_pin}")

            # Default db path if not provided
            if not db_path:
                db_path = str(Path.home() / ".tado-local.db")

            # Get or create persistent controller identity
            controller_id, private_key, public_key = await TadoBridge.get_or_create_controller_identity(db_path)
            logger.info(f"Using Controller ID: {controller_id}")

            try:
                # Create required dependencies for Controller
                # Note: zeroconf is only needed for discovery, not for known IP pairing
                # However, the Controller.start_pairing() method may require it temporarily

                # Create AsyncZeroconf instance (needed for initial pairing only)
                zeroconf_instance = AsyncZeroconf()

                # Create SQLite-backed characteristic cache
                char_cache = CharacteristicCacheSQLite(db_path)

                # Create the main Controller (not IpController)
                # This is only used for initial pairing - subsequent connections use IpController without zeroconf
                controller = Controller(
                    async_zeroconf_instance=zeroconf_instance,
                    char_cache=char_cache
                )

                logger.debug(f"Created controller with proper dependencies")

                # Start pairing using the controller's built-in method
                logger.info(f"Starting pairing process...")

                # This should use the controller's pairing method which returns an IpPairing
                pairing = await controller.start_pairing(host, hap_pin)

                logger.info(f"Pairing completed successfully!")

                # Clean up zeroconf instance after pairing
                await zeroconf_instance.async_close()

                # Extract pairing data in the correct format
                pairing_data = pairing.pairing_data

                logger.info(f"PAIRING SUCCESS! Controller-based approach, Controller ID: {controller_id}")

                return pairing_data

            except Exception as e:
                logger.error(f"Controller-based pairing failed: {e}")
                traceback.print_exc()
                raise

        except Exception as e:
            logger.error(f"Pairing failed with error: {e}")
            traceback.print_exc()
            raise
