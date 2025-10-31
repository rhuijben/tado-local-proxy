#!/usr/bin/env python3

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

"""
Tado Local API - Backward compatibility entry point.

This file provides backward compatibility for existing deployments.
New deployments should use:
    python -m tado_local
or
    tado-local
"""

import logging
from typing import Optional

# Import from tado_local package
from tado_local.api import TadoLocalAPI
from tado_local.routes import create_app, register_routes

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('tado-local')

# Create FastAPI app and register all routes from the package
app = create_app()

# Global API instance - will be initialized in main()
tado_api: Optional[TadoLocalAPI] = None

# Register all routes with a getter function for tado_api
register_routes(app, lambda: tado_api)

if __name__ == "__main__":
    # Call the package main function
    from tado_local.__main__ import main
    main()
