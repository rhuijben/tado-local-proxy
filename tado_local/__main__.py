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

"""Command-line interface for Tado Local."""

import asyncio
import argparse
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

import uvicorn
from aiohomekit.controller.ip.pairing import IpPairing

from .bridge import TadoBridge
from .api import TadoLocalAPI
from .cloud import TadoCloudAPI
from .routes import create_app, register_routes

# Configure logging with timestamps
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d %(levelname)-8s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout
)
logger = logging.getLogger('tado-local')

# Global variables
bridge_pairing: Optional[IpPairing] = None
tado_api: Optional[TadoLocalAPI] = None
server: Optional[uvicorn.Server] = None
shutdown_event: Optional[asyncio.Event] = None

async def run_server(args):
    """Run the Tado Local server."""
    global bridge_pairing, tado_api, server, shutdown_event

    shutdown_event = asyncio.Event()

    def handle_signal(signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, initiating immediate shutdown...")
        shutdown_event.set()

        # Immediately close SSE streams
        if tado_api:
            logger.info("Closing SSE event streams immediately...")
            if tado_api.event_listeners:
                for queue in list(tado_api.event_listeners):
                    try:
                        queue.put_nowait(None)
                    except:
                        pass

            if tado_api.zone_event_listeners:
                for queue in list(tado_api.zone_event_listeners):
                    try:
                        queue.put_nowait(None)
                    except:
                        pass

        if server:
            server.should_exit = True

    # Register signal handlers
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        # Initialize database and pairing
        db_path = Path(os.path.expanduser(args.state))

        # Initialize the API with database path
        tado_api = TadoLocalAPI(str(db_path))

        # Initialize Tado Cloud API (always enabled)
        cloud_api = TadoCloudAPI(str(db_path))

        # Check if already authenticated
        if not cloud_api.is_authenticated():
            logger.info("Tado Cloud API: Starting authentication flow...")
            # Start authentication in background (non-blocking)
            asyncio.create_task(cloud_api.authenticate())
        else:
            logger.info("✓ Tado Cloud API: Already authenticated")
            logger.info(f"  Home ID: {cloud_api.home_id}")

            # Verify token is still valid at startup
            if cloud_api.has_valid_access_token():
                logger.info("✓ Access token is valid")
            else:
                logger.info("Access token expired, will refresh on first API call")

        # Start background 4-hour sync task (replaces continuous token refresh)
        cloud_api.start_background_sync()

        # Store cloud_api reference in tado_api for use by routes
        tado_api.cloud_api = cloud_api

        # Create the FastAPI app
        app = create_app()
        register_routes(app, lambda: tado_api)

        # Set up pairing
        bridge_pairing, bridge_ip = await TadoBridge.pair_or_load(
            args.bridge_ip, args.pin, db_path, args.clear_pairings
        )

        # Initialize the API with the pairing
        await tado_api.initialize(bridge_pairing)

        logger.info(f"*** Tado Local ready! ***")
        logger.info(f"Bridge IP: {bridge_ip}")
        logger.info(f"API Server: http://0.0.0.0:{args.port}")
        logger.info(f"Documentation: http://0.0.0.0:{args.port}/docs")
        logger.info(f"Status: http://0.0.0.0:{args.port}/status")
        logger.info(f"Thermostats: http://0.0.0.0:{args.port}/thermostats")
        logger.info(f"Live Events: http://0.0.0.0:{args.port}/events")

        # Configure uvicorn logging
        # Set access log to WARNING level to reduce noise (only errors/warnings shown)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        
        # Start the FastAPI server
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=args.port,
            log_level="info",
            access_log=True  # Keep enabled but at WARNING level
        )
        server = uvicorn.Server(config)
        await server.serve()

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down gracefully...")
    except Exception as e:
        logger.error(f"ERROR: Failed to start Tado Local: {e}")
        raise
    finally:
        # Clean up resources
        if tado_api:
            logger.info("Performing cleanup...")

            # Close all SSE event streams (if not already closed by signal handler)
            if tado_api.event_listeners or tado_api.zone_event_listeners:
                logger.info("Closing remaining SSE event streams...")
                if tado_api.event_listeners:
                    logger.info(f"Closing {len(tado_api.event_listeners)} event listener queues")
                    for queue in tado_api.event_listeners[:]:
                        try:
                            await queue.put(None)
                        except:
                            pass

                if tado_api.zone_event_listeners:
                    logger.info(f"Closing {len(tado_api.zone_event_listeners)} zone event listener queues")
                    for queue in tado_api.zone_event_listeners[:]:
                        try:
                            await queue.put(None)
                        except:
                            pass

                # Give clients a moment to receive the close signal
                await asyncio.sleep(0.3)

            # Stop cloud API background sync if running
            if hasattr(tado_api, 'cloud_api') and tado_api.cloud_api:
                logger.info("Stopping Tado Cloud API background tasks...")
                await tado_api.cloud_api.stop_background_sync()

            # Full cleanup
            await tado_api.cleanup()

        # Clean up PID file
        if args.pid_file:
            pid_path = Path(args.pid_file)
            try:
                if pid_path.exists():
                    pid_path.unlink()
                    logger.info(f"PID file removed: {pid_path}")
            except Exception as e:
                logger.warning(f"Failed to remove PID file: {e}")

def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description="Tado Local - REST API for Tado devices via HomeKit bridge",
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
    parser.add_argument("--verbose", action="store_true",
                       help="Enable verbose logging (DEBUG level)")
    parser.add_argument("--pid-file",
                       help="Write process ID to specified file (useful for daemon mode)")

    args = parser.parse_args()

    # Apply verbose logging if requested
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.info("Verbose logging enabled")

    # Write PID file if requested
    if args.pid_file:
        pid_path = Path(args.pid_file)
        try:
            pid_path.write_text(str(os.getpid()))
            logger.info(f"PID file written: {pid_path}")
        except Exception as e:
            logger.error(f"Failed to write PID file: {e}")
            exit(1)

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
