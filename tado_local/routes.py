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

"""FastAPI route handlers for Tado Local API."""

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .homekit_uuids import enhance_accessory_data, get_service_name, get_characteristic_name
from .state import DeviceStateManager

# Configure logging
logger = logging.getLogger('tado-local')


def create_app():
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Tado Local API",
        description="Local REST API for Tado devices via HomeKit bridge",
        version="1.0.0"
    )
    
    # Mount static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    
    return app


def register_routes(app: FastAPI, get_tado_api):
    """Register all API routes.
    
    Args:
        app: FastAPI application instance
        get_tado_api: Callable that returns the current TadoLocalAPI instance
    """
    
    @app.get("/", tags=["Web UI"])
    async def root():
        """Serve the web UI."""
        static_dir = Path(__file__).parent / "static"
        index_file = static_dir / "index.html"
        
        if index_file.exists():
            return FileResponse(index_file, media_type="text/html")
        else:
            # Fallback to API info if web UI not found
            return {
                "service": "Tado Local API",
                "description": "Local REST API for Tado devices via HomeKit bridge",
                "version": "1.0.0",
                "documentation": "/docs",
                "api_info": "/api",
                "note": "Web UI not found. Install static/index.html or visit /api for API details"
            }
    
    @app.get("/api", tags=["Info"])
    async def api_info():
        """API root with diagnostics and navigation."""
        return {
            "service": "Tado Local API",
            "description": "Local REST API for Tado devices via HomeKit bridge",
            "version": "1.0.0",
            "documentation": "/docs",
            "web_ui": "/",
            "endpoints": {
                "status": "/status",
                "devices": "/devices",
                "zones": "/zones",
                "thermostats": "/thermostats",
                "events": "/events",
                "accessories": "/accessories",
                "refresh": "/refresh",
                "refresh_cloud": "/refresh/cloud",
                "debug": "/debug/characteristics"
            }
        }
    
    # Web UI routes (under /ui prefix)
    @app.get("/ui", tags=["Web UI"])
    async def web_ui():
        """Serve the web UI."""
        static_dir = Path(__file__).parent / "static"
        index_file = static_dir / "index.html"
        
        if index_file.exists():
            return FileResponse(index_file, media_type="text/html")
        else:
            raise HTTPException(status_code=404, detail="Web UI not found. Install static/index.html")
    
    @app.get("/ui/status", tags=["Web UI"])
    async def get_ui_status():
        """Get overall system status (Web UI route)."""
        return await get_status()
    
    @app.get("/ui/zones", tags=["Web UI"])
    async def get_ui_zones():
        """Get all zones (Web UI route)."""
        return await get_zones()
    
    @app.post("/ui/zones/{zone_id}/control", tags=["Web UI"])
    async def control_ui_zone(
        zone_id: int,
        target_temperature: Optional[float] = None,
        heating_enabled: Optional[bool] = None
    ):
        """Control a zone (Web UI route)."""
        return await control_zone(zone_id, target_temperature, heating_enabled)
    
    @app.get("/ui/cloud/home", tags=["Web UI"])
    async def get_ui_cloud_home():
        """Get home info from cloud API (Web UI route)."""
        tado_api = get_tado_api()
        
        if not hasattr(tado_api, 'cloud_api') or not tado_api.cloud_api:
            raise HTTPException(status_code=503, detail="Cloud API not available")
        
        cloud_api = tado_api.cloud_api
        
        if not cloud_api.is_authenticated():
            raise HTTPException(status_code=401, detail="Not authenticated with Tado Cloud API")
        
        try:
            home_info = await cloud_api.get_home_info()
            if not home_info:
                raise HTTPException(status_code=404, detail="Home info not found")
            return home_info
        except Exception as e:
            logger.error(f"Error fetching home info: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch home info: {str(e)}")

    @app.get("/status", tags=["Status"])
    async def get_status():
        """Get overall system status."""
        tado_api = get_tado_api()
        if not tado_api or not tado_api.pairing:
            raise HTTPException(status_code=503, detail="Bridge not connected")
        
        try:
            # Test connection
            await tado_api.pairing.list_accessories_and_characteristics()
            
            devices = tado_api.state_manager.get_all_devices()
            
            status = {
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
            
            # Add cloud API status if available
            if hasattr(tado_api, 'cloud_api') and tado_api.cloud_api:
                cloud = tado_api.cloud_api
                cloud_status = {
                    "enabled": True,
                    "authenticated": cloud.is_authenticated(),
                    "home_id": cloud.home_id,
                }
                
                # Add token expiry info if authenticated
                if cloud.is_authenticated():
                    cloud_status["token_expires_at"] = cloud.token_expires_at
                    cloud_status["token_expires_in"] = int(cloud.token_expires_at - time.time()) if cloud.token_expires_at else None
                
                # Add rate limit info if available
                if cloud.rate_limit and cloud.rate_limit.granted_calls:
                    cloud_status["rate_limit"] = cloud.rate_limit.to_dict()
                
                # Add authentication info if currently authenticating
                if cloud.is_authenticating and cloud.auth_verification_uri:
                    cloud_status["authentication_required"] = True
                    cloud_status["verification_uri"] = cloud.auth_verification_uri
                    cloud_status["user_code"] = cloud.auth_user_code
                    cloud_status["auth_expires_at"] = cloud.auth_expires_at
                    cloud_status["auth_expires_in"] = int(cloud.auth_expires_at - time.time()) if cloud.auth_expires_at else None
                    cloud_status["message"] = f"Visit {cloud.auth_verification_uri} to authenticate"
                elif not cloud.is_authenticated():
                    cloud_status["authentication_required"] = True
                    cloud_status["message"] = "Authentication will start automatically"
                
                status["cloud_api"] = cloud_status
            else:
                status["cloud_api"] = {
                    "enabled": False,
                    "authenticated": False
                }
            
            return status
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
        tado_api = get_tado_api()
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
        tado_api = get_tado_api()
        if not tado_api.accessories_cache:
            await tado_api.refresh_accessories()
        
        accessories = tado_api.accessories_cache
        for accessory in accessories:
            if accessory.get('id') == accessory_id:
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

    @app.get("/thermostats", tags=["Tado"])
    async def get_thermostats():
        """
        Get all thermostat devices with standardized state.
        
        Returns temperature, humidity, mode, and heating status for each thermostat.
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        
        if not tado_api.accessories_cache:
            await tado_api.refresh_accessories()
        
        thermostats = []
        accessories = tado_api.accessories_cache
        
        for accessory in accessories:
            services = accessory.get('services', [])
            for service in services:
                if service.get('type') == '0000004A-0000-1000-8000-0026BB765291':  # Thermostat service
                    
                    device_id = accessory.get('id')
                    if not device_id:
                        continue
                    
                    # Get device info from cache
                    device_info = tado_api.state_manager.device_info_cache.get(device_id, {})
                    
                    # Build standardized state
                    state = tado_api.state_manager.get_current_state(device_id)
                    cur_temp_c = state.get('current_temperature')
                    target_temp_c = state.get('target_temperature')
                    
                    # Determine battery_low from Cloud API (cached)
                    battery_state = device_info.get('battery_state')
                    battery_low = battery_state is not None and battery_state != 'NORMAL'
                    
                    thermostat = {
                        'device_id': device_id,
                        'aid': accessory.get('aid'),
                        'serial_number': accessory.get('serial_number'),
                        'zone_name': device_info.get('zone_name'),
                        'device_type': device_info.get('device_type'),
                        'state': {
                            'cur_temp_c': cur_temp_c,
                            'cur_temp_f': round(cur_temp_c * 9/5 + 32, 1) if cur_temp_c is not None else None,
                            'hum_perc': state.get('humidity'),
                            'target_temp_c': target_temp_c,
                            'target_temp_f': round(target_temp_c * 9/5 + 32, 1) if target_temp_c is not None else None,
                            'mode': state.get('target_heating_cooling_state', 0),
                            'cur_heating': 1 if state.get('current_heating_cooling_state') == 1 else 0,
                            'valve_position': state.get('valve_position'),
                            'battery_low': battery_low,
                        }
                    }
                    
                    thermostats.append(thermostat)
        
        return {"thermostats": thermostats, "count": len(thermostats)}

    @app.get("/thermostats/{id}", tags=["Tado"])
    async def get_thermostat(id: int):
        """Get specific thermostat by device ID with standardized state."""
        tado_api = get_tado_api()
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
        
        # Get device info from cache
        device_info = tado_api.state_manager.device_info_cache.get(id, {})
        
        # Build standardized state
        state = tado_api.state_manager.get_current_state(id)
        cur_temp_c = state.get('current_temperature')
        target_temp_c = state.get('target_temperature')
        
        # Determine battery_low from Cloud API (cached)
        battery_state = device_info.get('battery_state')
        battery_low = battery_state is not None and battery_state != 'NORMAL'
        
        thermostat = {
            'device_id': id,
            'aid': accessory.get('aid'),
            'serial_number': accessory.get('serial_number'),
            'zone_name': device_info.get('zone_name'),
            'device_type': device_info.get('device_type'),
            'is_zone_leader': device_info.get('is_zone_leader'),
            'is_circuit_driver': device_info.get('is_circuit_driver'),
            'state': {
                'cur_temp_c': cur_temp_c,
                'cur_temp_f': round(cur_temp_c * 9/5 + 32, 1) if cur_temp_c is not None else None,
                'hum_perc': state.get('humidity'),
                'target_temp_c': target_temp_c,
                'target_temp_f': round(target_temp_c * 9/5 + 32, 1) if target_temp_c is not None else None,
                'mode': state.get('target_heating_cooling_state', 0),
                'cur_heating': 1 if state.get('current_heating_cooling_state') == 1 else 0,
                'valve_position': state.get('valve_position'),
                'battery_low': battery_low,
            }
        }
        
        return thermostat

    @app.get("/zones", tags=["Zones"])
    async def get_zones():
        """
        Get all zones with aggregated state (no per-device details).
        
        Returns zone-level information:
        - Current temperature (°C and °F)
        - Current humidity (%)
        - Target temperature (°C and °F)
        - Mode (0=Off, 1=Heat) - TargetHeatingCoolingState
        - Currently heating (0=Off, 1=Heating, 2=Cooling) - CurrentHeatingCoolingState
        
        Note: Mode values depend on device capabilities. Heating-only devices typically
        support 0 (Off) and 1 (Heat). Devices with cooling may support additional values.
        
        For individual device details, use /thermostats or /devices endpoints.
        
        Note: For zones where the leader is a circuit driver (e.g., RU02 controlling
        multiple rooms), the "cur_heating" status reflects the actual heating
        state from radiator valves in the zone, not the circuit driver state.
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        
        zones = []
        
        # Use cached zone info (no DB query)
        # Sort by order_id (treating None as 999, but 0 is valid), then by name
        for zone_id, zone_info in sorted(tado_api.state_manager.zone_cache.items(), 
                                          key=lambda x: (999 if x[1].get('order_id') is None else x[1].get('order_id'), x[1].get('name'))):
            name = zone_info['name']
            leader_device_id = zone_info['leader_device_id']
            order_id = zone_info['order_id']
            leader_serial = zone_info['leader_serial']
            leader_type = zone_info['leader_type']
            is_circuit_driver = zone_info['is_circuit_driver']
            
            # Get device count for this zone (quick loop through device cache)
            device_count = sum(1 for dev_info in tado_api.state_manager.device_info_cache.values() 
                              if dev_info.get('zone_id') == zone_id)
            
            # Get zone state from leader or first device
            zone_state = None
            if leader_device_id:
                zone_state = tado_api.state_manager.get_current_state(leader_device_id)
            
            # If no leader state, try first device in zone
            if not zone_state:
                for dev_id, dev_info in tado_api.state_manager.device_info_cache.items():
                    if dev_info.get('zone_id') == zone_id:
                        zone_state = tado_api.state_manager.get_current_state(dev_id)
                        break
            
            # Build zone summary state
            if zone_state:
                current_temp = zone_state.get('current_temperature')
                humidity = zone_state.get('humidity')
                target_temp = zone_state.get('target_temperature')
                target_heating_cooling_state = zone_state.get('target_heating_cooling_state', 0)
                
                # Mode: Use target_heating_cooling_state
                # For circuit drivers, check actual devices in zone
                mode = 0
                if is_circuit_driver:
                    # Circuit driver - check if any radiator valves are requesting heat (use cache)
                    other_devices = [dev_id for dev_id, dev_info in tado_api.state_manager.device_info_cache.items()
                                    if dev_info.get('zone_id') == zone_id and not dev_info.get('is_circuit_driver')]
                    
                    if other_devices:
                        # Circuit driver with other devices - check radiator valves
                        for dev_id in other_devices:
                            dev_state = tado_api.state_manager.get_current_state(dev_id)
                            if dev_state and dev_state.get('target_heating_cooling_state') == 1:
                                mode = 1
                                break
                    else:
                        # Circuit driver alone in zone - use its own state
                        mode = target_heating_cooling_state
                else:
                    # Not a circuit driver - use zone state
                    mode = target_heating_cooling_state
                
                # Currently heating logic
                cur_heating = 0
                if is_circuit_driver:
                    # Circuit driver - check if any radiator valves in zone are heating (use cache)
                    other_devices = [dev_id for dev_id, dev_info in tado_api.state_manager.device_info_cache.items()
                                    if dev_info.get('zone_id') == zone_id and not dev_info.get('is_circuit_driver')]
                    
                    if other_devices:
                        # Circuit driver with other devices - check radiator valves
                        for dev_id in other_devices:
                            dev_state = tado_api.state_manager.get_current_state(dev_id)
                            if dev_state and dev_state.get('current_heating_cooling_state') == 1:
                                cur_heating = 1
                                break
                    else:
                        # Circuit driver alone in zone - use its own state
                        cur_heating = 1 if zone_state.get('current_heating_cooling_state') == 1 else 0
                else:
                    # Not a circuit driver - use zone state
                    cur_heating = 1 if zone_state.get('current_heating_cooling_state') == 1 else 0
                
                # Convert temperatures to Fahrenheit
                cur_temp_f = round(current_temp * 9/5 + 32, 1) if current_temp is not None else None
                target_temp_f = round(target_temp * 9/5 + 32, 1) if target_temp is not None else None
                
                state_summary = {
                    'cur_temp_c': current_temp,
                    'cur_temp_f': cur_temp_f,
                    'hum_perc': humidity,
                    'target_temp_c': target_temp,
                    'target_temp_f': target_temp_f,
                    'mode': mode,
                    'cur_heating': cur_heating,
                }
            else:
                state_summary = {
                    'cur_temp_c': None,
                    'cur_temp_f': None,
                    'hum_perc': None,
                    'target_temp_c': None,
                    'target_temp_f': None,
                    'mode': 0,
                    'cur_heating': 0,
                }
            
            zones.append({
                'zone_id': zone_id,
                'name': name,
                'leader_device_id': leader_device_id,
                'leader_serial': leader_serial,
                'leader_type': leader_type,
                'is_circuit_driver': bool(is_circuit_driver),
                'order_id': order_id,
                'device_count': device_count,
                'state': state_summary
            })
        
        # Get home info if cloud API is available and authenticated
        homes = []
        if hasattr(tado_api, 'cloud_api') and tado_api.cloud_api and tado_api.cloud_api.is_authenticated():
            try:
                home_data = await tado_api.cloud_api.get_home_info()
                if home_data:
                    homes.append({
                        'id': home_data.get('id'),
                        'name': home_data.get('name')
                    })
            except Exception as e:
                logger.debug(f"Could not fetch home info: {e}")
        
        # Add home_id reference to each zone (from first/only home for now)
        home_id = homes[0]['id'] if homes else None
        for zone in zones:
            zone['home_id'] = home_id
        
        return {
            'homes': homes,
            'zones': zones,
            'count': len(zones)
        }

    @app.post("/zones", tags=["Zones"])
    async def create_zone(name: str, leader_device_id: Optional[int] = None, order_id: Optional[int] = None):
        """Create a new zone."""
        tado_api = get_tado_api()
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
        tado_api = get_tado_api()
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

    @app.post("/zones/{zone_id}/control", tags=["Zones"])
    async def control_zone(
        zone_id: int, 
        target_temperature: Optional[float] = None,
        heating_enabled: Optional[bool] = None
    ):
        """
        Control a zone's heating via its leader device.
        
        Args:
            zone_id: Zone ID to control
            target_temperature: Target temperature in °C (e.g., 21.0)
            heating_enabled: Enable/disable heating mode (true/false)
        
        Returns:
            Success status and applied values
            
        Notes:
            - Commands are sent to the zone's leader device
            - The leader propagates changes to other devices as needed
            - heating_enabled controls the heat mode (OFF=0, HEAT=1)
            - target_temperature persists even when heating is disabled
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        
        if not tado_api.pairing:
            raise HTTPException(status_code=503, detail="Bridge not connected")
        
        # Get zone info
        conn = sqlite3.connect(tado_api.state_manager.db_path)
        cursor = conn.execute("""
            SELECT z.name, z.leader_device_id, d.serial_number
            FROM zones z
            LEFT JOIN devices d ON z.leader_device_id = d.device_id
            WHERE z.zone_id = ?
        """, (zone_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")
        
        zone_name, leader_device_id, leader_serial = row
        
        if not leader_device_id:
            raise HTTPException(status_code=400, detail=f"Zone '{zone_name}' has no leader device assigned")
        
        # Build characteristic updates
        char_updates = {}
        
        if target_temperature is not None:
            # Validate temperature range (5-30°C is typical for Tado)
            if target_temperature < 5.0 or target_temperature > 30.0:
                raise HTTPException(status_code=400, detail="Temperature must be between 5 and 30°C")
            char_updates['target_temperature'] = target_temperature
            logger.info(f"Zone {zone_id} ({zone_name}): Setting target_temperature to {target_temperature}°C")
        
        if heating_enabled is not None:
            # 0 = OFF, 1 = HEAT
            char_updates['target_heating_cooling_state'] = 1 if heating_enabled else 0
            logger.info(f"Zone {zone_id} ({zone_name}): Setting heating_enabled to {heating_enabled}")
        
        if not char_updates:
            raise HTTPException(status_code=400, detail="No control parameters provided")
        
        # Set the characteristics on the leader device
        try:
            await tado_api.set_device_characteristics(leader_device_id, char_updates)
            
            return {
                'success': True,
                'zone_id': zone_id,
                'zone_name': zone_name,
                'leader_device_id': leader_device_id,
                'leader_serial': leader_serial,
                'applied': {
                    'target_temperature': target_temperature,
                    'heating_enabled': heating_enabled
                }
            }
        
        except Exception as e:
            logger.error(f"Failed to control zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to set zone control: {str(e)}")

    @app.put("/devices/{device_id}/zone", tags=["Devices"])
    async def assign_device_to_zone(device_id: int, zone_id: Optional[int] = None):
        """Assign a device to a zone (or remove from zone if zone_id is None)."""
        tado_api = get_tado_api()
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
        """
        Get all registered devices with standardized state.
        
        Returns all devices (thermostats, valves, bridges, etc.) with:
        - Device metadata (serial, type, zone)
        - Standardized state format
        - Battery status (for battery-powered devices)
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        
        all_devices = tado_api.state_manager.get_all_devices()
        
        devices = []
        for device_info in all_devices:
            device_id = device_info['device_id']
            state = tado_api.state_manager.get_current_state(device_id)
            
            # Build standardized state
            cur_temp_c = state.get('current_temperature')
            target_temp_c = state.get('target_temperature')
            
            # Determine battery_low from Cloud API (cached in device_info, no extra DB query)
            battery_state = device_info.get('battery_state')
            battery_low = battery_state is not None and battery_state != 'NORMAL'
            
            device = {
                'device_id': device_id,
                'serial_number': device_info.get('serial_number'),
                'aid': device_info.get('aid'),
                'zone_id': device_info.get('zone_id'),
                'zone_name': device_info.get('zone_name'),
                'device_type': device_info.get('device_type'),
                'is_zone_leader': device_info.get('is_zone_leader'),
                'is_circuit_driver': device_info.get('is_circuit_driver'),
                'state': {
                    'cur_temp_c': cur_temp_c,
                    'cur_temp_f': round(cur_temp_c * 9/5 + 32, 1) if cur_temp_c is not None else None,
                    'hum_perc': state.get('humidity'),
                    'target_temp_c': target_temp_c,
                    'target_temp_f': round(target_temp_c * 9/5 + 32, 1) if target_temp_c is not None else None,
                    'mode': state.get('target_heating_cooling_state', 0),
                    'cur_heating': 1 if state.get('current_heating_cooling_state') == 1 else 0,
                    'valve_position': state.get('valve_position'),
                    'battery_low': battery_low,
                }
            }
            
            devices.append(device)
        
        return {
            "devices": devices,
            "count": len(devices)
        }

    @app.get("/devices/{id}", tags=["Devices"])
    async def get_device(id: int):
        """
        Get specific device with standardized state by ID (database device_id).
        
        Args:
            id: Device ID (database ID)
        """
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        
        all_devices = tado_api.state_manager.get_all_devices()
        device_info = next((d for d in all_devices if d['device_id'] == id), None)
        
        if not device_info:
            raise HTTPException(status_code=404, detail=f"Device {id} not found")
        
        state = tado_api.state_manager.get_current_state(id)
        
        # Build standardized state
        cur_temp_c = state.get('current_temperature')
        target_temp_c = state.get('target_temperature')
        
        # Determine battery_low from Cloud API (cached)
        battery_state = device_info.get('battery_state')
        battery_low = battery_state is not None and battery_state != 'NORMAL'
        
        device = {
            'device_id': id,
            'serial_number': device_info.get('serial_number'),
            'aid': device_info.get('aid'),
            'zone_id': device_info.get('zone_id'),
            'zone_name': device_info.get('zone_name'),
            'device_type': device_info.get('device_type'),
            'is_zone_leader': device_info.get('is_zone_leader'),
            'is_circuit_driver': device_info.get('is_circuit_driver'),
            'state': {
                'cur_temp_c': cur_temp_c,
                'cur_temp_f': round(cur_temp_c * 9/5 + 32, 1) if cur_temp_c is not None else None,
                'hum_perc': state.get('humidity'),
                'target_temp_c': target_temp_c,
                'target_temp_f': round(target_temp_c * 9/5 + 32, 1) if target_temp_c is not None else None,
                'mode': state.get('target_heating_cooling_state', 0),
                'cur_heating': 1 if state.get('current_heating_cooling_state') == 1 else 0,
                'valve_position': state.get('valve_position'),
                'battery_low': battery_low,
            }
        }
        
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
        tado_api = get_tado_api()
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

    @app.get("/devices/{id}/current-state", tags=["Devices"])
    async def get_all_current_states(id: int):
        """Get current state for all devices."""
        tado_api = get_tado_api()
        if not tado_api:
            raise HTTPException(status_code=503, detail="API not initialized")
        
        return {
            "states": tado_api.state_manager.get_current_state(id),
            "timestamp": time.time()
        }

    @app.post("/thermostats/{accessory_id}/set_temperature", tags=["Tado"])
    async def set_thermostat_temperature(accessory_id: int, temperature: float):
        """Set target temperature for a specific thermostat."""
        tado_api = get_tado_api()
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
    async def get_events(refresh_interval: Optional[int] = None):
        """
        Server-Sent Events (SSE) endpoint for real-time updates.
        
        Args:
            refresh_interval: Optional interval in seconds to send periodic refresh updates
                            even when state hasn't changed. Useful for clients like Domoticz
                            that need regular updates for statistics and "last seen" tracking.
                            Recommended: 300 (5 minutes). Default: None (only send on changes).
        
        Clients can maintain a persistent connection to receive live updates without polling.
        
        Event Types:
        
        1. Device State Change:
           {
               "type": "device",
               "device_id": 4,
               "serial": "RU1372921856",
               "zone_name": "Studeerkamer",
               "state": {
                   "cur_temp_c": 21.5,
                   "cur_temp_f": 70.7,
                   "hum_perc": 55,
                   "target_temp_c": 21.0,
                   "target_temp_f": 69.8,
                   "mode": 1,
                   "cur_heating": 1,
                   "valve_position": 45,
                   "battery_low": false
               },
               "timestamp": 1730477890.123
           }
        
        2. Zone State Change:
           {
               "type": "zone",
               "zone_id": 4,
               "zone_name": "Studeerkamer",
               "state": {
                   "cur_temp_c": 21.5,
                   "cur_temp_f": 70.7,
                   "hum_perc": 55,
                   "target_temp_c": 21.0,
                   "target_temp_f": 69.8,
                   "mode": 1,
                   "cur_heating": 1
               },
               "timestamp": 1730477890.123
           }
        
        3. Keepalive (every 30 seconds):
           {
               "type": "keepalive",
               "timestamp": 1730477890.123
           }
        
        State Field Reference:
            mode: 0=Off, 1=Heat, 2=Cool, 3=Auto (TargetHeatingCoolingState)
            cur_heating: 0=not heating, 1=actively heating (CurrentHeatingCoolingState)
            battery_low: true if Cloud API battery_state != "NORMAL" (cached, no DB queries)
            Temperatures provided in both Celsius (_c) and Fahrenheit (_f)
        
        Usage Example:
            const eventSource = new EventSource('/events');
            eventSource.onmessage = (event) => {
                const data = JSON.parse(event.data);
                
                if (data.type === 'zone') {
                    // Update zone UI
                    updateZone(data.zone_id, data.state);
                } else if (data.type === 'device') {
                    // Update device UI (if showing device details)
                    updateDevice(data.device_id, data.state);
                }
            };
        """
        tado_api = get_tado_api()
        
        async def event_publisher():
            # Create a queue for this client
            client_queue = asyncio.Queue()
            tado_api.event_listeners.append(client_queue)
            
            last_refresh = time.time() if refresh_interval else None
            
            try:
                while True:
                    # Calculate timeout based on refresh_interval
                    if refresh_interval and last_refresh:
                        time_since_refresh = time.time() - last_refresh
                        timeout = max(1, refresh_interval - time_since_refresh)
                    else:
                        timeout = 30
                    
                    # Wait for events
                    try:
                        event_data = await asyncio.wait_for(client_queue.get(), timeout=timeout)
                        
                        # Check for shutdown signal
                        if event_data is None:
                            logger.debug("SSE stream received shutdown signal")
                            break
                        
                        yield event_data
                        
                        # Reset refresh timer on any event
                        if refresh_interval:
                            last_refresh = time.time()
                            
                    except asyncio.TimeoutError:
                        # Check if we should send a refresh update
                        if refresh_interval and last_refresh and (time.time() - last_refresh >= refresh_interval):
                            # Send refresh updates for all zones
                            conn = sqlite3.connect(tado_api.state_manager.db_path)
                            cursor = conn.execute("""
                                SELECT zone_id, name 
                                FROM zones 
                                WHERE zone_id IS NOT NULL
                                ORDER BY zone_id
                            """)
                            zones = cursor.fetchall()
                            conn.close()
                            
                            for zone_id, zone_name in zones:
                                zone_state = tado_api.state_manager.get_zone_state(zone_id)
                                if zone_state:
                                    event_obj = {
                                        'type': 'zone',
                                        'zone_id': zone_id,
                                        'zone_name': zone_name,
                                        'state': zone_state,
                                        'timestamp': time.time(),
                                        'refresh': True
                                    }
                                    yield f"data: {json.dumps(event_obj)}\n\n"
                            
                            last_refresh = time.time()
                        else:
                            # Send keepalive
                            keepalive_obj = {'type': 'keepalive', 'timestamp': time.time()}
                            yield f"data: {json.dumps(keepalive_obj)}\n\n"
                        
            except asyncio.CancelledError:
                logger.debug("SSE stream cancelled")
                pass
            finally:
                # Remove this client's queue
                if client_queue in tado_api.event_listeners:
                    tado_api.event_listeners.remove(client_queue)
        
        return StreamingResponse(
            event_publisher(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream"
            }
        )

    @app.get("/events/zones", tags=["Events"])
    async def get_zone_events(refresh_interval: Optional[int] = None):
        """
        Server-Sent Events (SSE) endpoint for zone-only updates.
        
        Args:
            refresh_interval: Optional interval in seconds to send periodic refresh updates
                            even when state hasn't changed. Useful for clients like Domoticz
                            that need regular updates for statistics and "last seen" tracking.
                            Recommended: 300 (5 minutes). Default: None (only send on changes).
        
        This endpoint only sends zone-level state changes, not individual device events.
        Useful for clients that only need zone aggregation without device details.
        
        Event Types:
        
        1. Zone State Change:
           {
               "type": "zone",
               "zone_id": 4,
               "zone_name": "Studeerkamer",
               "state": {
                   "cur_temp_c": 21.5,
                   "cur_temp_f": 70.7,
                   "hum_perc": 55,
                   "target_temp_c": 21.0,
                   "target_temp_f": 69.8,
                   "mode": 1,
                   "cur_heating": 1
               },
               "timestamp": 1730477890.123
           }
        
        2. Keepalive (every 30 seconds):
           {
               "type": "keepalive",
               "timestamp": 1730477890.123
           }
        
        State Field Reference:
            mode: 0=Off, 1=Heat (TargetHeatingCoolingState)
            cur_heating: 0=Off, 1=Heating, 2=Cooling (CurrentHeatingCoolingState)
            Temperatures provided in both Celsius (_c) and Fahrenheit (_f)
        
        Usage Example:
            const eventSource = new EventSource('/events/zones');
            eventSource.onmessage = (event) => {
                const data = JSON.parse(event.data);
                
                if (data.type === 'zone') {
                    updateZone(data.zone_id, data.state);
                }
            };
        """
        tado_api = get_tado_api()
        
        async def event_publisher():
            # Create a queue for this client
            client_queue = asyncio.Queue()
            tado_api.zone_event_listeners.append(client_queue)
            
            last_refresh = time.time() if refresh_interval else None
            
            try:
                while True:
                    # Calculate timeout based on refresh_interval
                    if refresh_interval and last_refresh:
                        time_since_refresh = time.time() - last_refresh
                        timeout = max(1, refresh_interval - time_since_refresh)
                    else:
                        timeout = 30
                    
                    # Wait for events
                    try:
                        event_data = await asyncio.wait_for(client_queue.get(), timeout=timeout)
                        
                        # Check for shutdown signal
                        if event_data is None:
                            logger.debug("Zone SSE stream received shutdown signal")
                            break
                        
                        yield event_data
                        
                        # Reset refresh timer on any event
                        if refresh_interval:
                            last_refresh = time.time()
                            
                    except asyncio.TimeoutError:
                        # Check if we should send a refresh update
                        if refresh_interval and last_refresh and (time.time() - last_refresh >= refresh_interval):
                            # Send refresh updates for all zones
                            conn = sqlite3.connect(tado_api.state_manager.db_path)
                            cursor = conn.execute("""
                                SELECT zone_id, name 
                                FROM zones 
                                WHERE zone_id IS NOT NULL
                                ORDER BY zone_id
                            """)
                            zones = cursor.fetchall()
                            conn.close()
                            
                            for zone_id, zone_name in zones:
                                zone_state = tado_api.state_manager.get_zone_state(zone_id)
                                if zone_state:
                                    event_obj = {
                                        'type': 'zone',
                                        'zone_id': zone_id,
                                        'zone_name': zone_name,
                                        'state': zone_state,
                                        'timestamp': time.time(),
                                        'refresh': True
                                    }
                                    yield f"data: {json.dumps(event_obj)}\n\n"
                            
                            last_refresh = time.time()
                        else:
                            # Send keepalive
                            keepalive_obj = {'type': 'keepalive', 'timestamp': time.time()}
                            yield f"data: {json.dumps(keepalive_obj)}\n\n"
                        
            except asyncio.CancelledError:
                logger.debug("Zone SSE stream cancelled")
                pass
            finally:
                # Remove this client's queue
                if client_queue in tado_api.zone_event_listeners:
                    tado_api.zone_event_listeners.remove(client_queue)
        
        return StreamingResponse(
            event_publisher(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream"
            }
        )

    @app.post("/refresh", tags=["Admin"])
    async def refresh_data():
        """Manually refresh accessories data from HomeKit bridge."""
        tado_api = get_tado_api()
        return await tado_api.refresh_accessories()
    
    @app.post("/refresh/cloud", tags=["Admin"])
    async def refresh_cloud_data(battery_only: bool = False):
        """
        Manually refresh data from Tado Cloud API.
        
        Args:
            battery_only: If true, only refresh battery/status data (fast).
                         If false, refresh all data including config (slower).
        
        Returns:
            Summary of refreshed data
            
        This bypasses the cache and fetches fresh data from Tado's servers.
        Useful when you need immediate battery status updates or after making
        changes in the Tado app.
        """
        tado_api = get_tado_api()
        
        if not hasattr(tado_api, 'cloud_api') or not tado_api.cloud_api:
            raise HTTPException(status_code=503, detail="Cloud API not available")
        
        cloud_api = tado_api.cloud_api
        
        if not cloud_api.is_authenticated():
            raise HTTPException(status_code=401, detail="Not authenticated with Tado Cloud API")
        
        try:
            # Import sync module
            from .sync import TadoCloudSync
            sync = TadoCloudSync(tado_api.state_manager.db_path)
            
            result = {}
            
            if battery_only:
                # Fast refresh: battery and status data only
                logger.info("Refreshing battery/status data from cloud...")
                zone_states = await cloud_api.get_zone_states(force_refresh=True)
                devices = await cloud_api.get_device_list(force_refresh=True)
                
                if devices:
                    result['devices_synced'] = len(devices)
                    logger.info(f"✓ Synced {len(devices)} devices (battery status)")
                
                # Sync to database
                await sync.sync_all(cloud_api, 
                                  home_data=False,  # Skip
                                  zones_data=False,  # Skip
                                  zone_states_data=zone_states,
                                  devices_data=devices)
                
                result['refreshed'] = ['battery_status', 'device_status']
            else:
                # Full refresh: all data
                logger.info("Refreshing all cloud data...")
                home_info = await cloud_api.get_home_info(force_refresh=True)
                zones = await cloud_api.get_zones(force_refresh=True)
                zone_states = await cloud_api.get_zone_states(force_refresh=True)
                devices = await cloud_api.get_device_list(force_refresh=True)
                
                if home_info:
                    result['home_name'] = home_info.get('name')
                if zones:
                    result['zones_synced'] = len(zones)
                if devices:
                    result['devices_synced'] = len(devices)
                
                # Sync to database
                await sync.sync_all(cloud_api,
                                  home_data=home_info,
                                  zones_data=zones,
                                  zone_states_data=zone_states,
                                  devices_data=devices)
                
                result['refreshed'] = ['home_info', 'zones', 'battery_status', 'device_status']
            
            # Reload device cache to pick up changes
            tado_api.state_manager._load_device_cache()
            
            result['success'] = True
            result['timestamp'] = time.time()
            return result
            
        except Exception as e:
            logger.error(f"Error refreshing cloud data: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to refresh cloud data: {str(e)}")

    @app.get("/debug/characteristics", tags=["Debug"])
    async def debug_characteristics():
        """
        Compare cached values vs live polled values to identify characteristics that don't send events.
        This helps diagnose why some values (like humidity) aren't updating via events.
        """
        tado_api = get_tado_api()
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
        tado_api = get_tado_api()
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
    
    return app
