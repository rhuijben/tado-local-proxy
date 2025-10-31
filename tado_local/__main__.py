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

"""Command-line interface for Tado Local API."""

import asyncio
import argparse
import logging
import os
from pathlib import Path
from typing import Optional

import uvicorn
from aiohomekit.controller.ip.pairing import IpPairing

from .bridge import TadoBridge
from .api import TadoLocalAPI
from .routes import create_app, register_routes

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('tado-local')

# Global variables
bridge_pairing: Optional[IpPairing] = None
tado_api: Optional[TadoLocalAPI] = None

async def run_server(args):
    """Run the Tado Local API server."""
    global bridge_pairing, tado_api
    
    try:
        # Initialize database and pairing
        db_path = Path(os.path.expanduser(args.state))
        
        # Initialize the API with database path
        tado_api = TadoLocalAPI(str(db_path))
        
        # Create the FastAPI app
        app = create_app()
        register_routes(app, lambda: tado_api)
        
        # Set up pairing
        bridge_pairing, bridge_ip = await TadoBridge.pair_or_load(
            args.bridge_ip, args.pin, db_path, args.clear_pairings
        )
        
        # Initialize the API with the pairing
        await tado_api.initialize(bridge_pairing)
        
        logger.info(f"*** Tado Local API ready! ***")
        logger.info(f"Bridge IP: {bridge_ip}")
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

def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description="Tado Local API - REST API for Tado devices via HomeKit bridge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Initial pairing (first time setup)
  python -m tado_local --bridge-ip 192.168.1.100 --pin 123-45-678
  tado-local --bridge-ip 192.168.1.100 --pin 123-45-678
  
  # Start API server with existing pairing
  python -m tado_local --bridge-ip 192.168.1.100
  tado-local --bridge-ip 192.168.1.100
  
  # Custom port and database location
  python -m tado_local --bridge-ip 192.168.1.100 --port 8080 --state ./my-tado.db
  
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
        asyncio.run(run_server(args))
    except KeyboardInterrupt:
        logger.info("*** Shutdown complete ***")
    except Exception as e:
        logger.error(f"ERROR: {e}")
        exit(1)

if __name__ == "__main__":
    main()
