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
"""Tado Local - REST API for Tado devices via HomeKit bridge."""

from .__version__ import __version__

__author__ = "Tado Local Contributors"
__description__ = "REST API for Tado devices via HomeKit bridge"

# Import modules that are ready
from .cache import CharacteristicCacheSQLite
from .database import DB_SCHEMA, HOMEKIT_SCHEMA
from .bridge import TadoBridge
from .state import DeviceStateManager
from .api import TadoLocalAPI
from .cloud import TadoCloudAPI, RateLimitInfo
from .sync import TadoCloudSync
from . import homekit_uuids

__all__ = [
    "__version__",
    "CharacteristicCacheSQLite",
    "DB_SCHEMA",
    "HOMEKIT_SCHEMA",
    "TadoBridge",
    "DeviceStateManager",
    "TadoLocalAPI",
    "TadoCloudAPI",
    "RateLimitInfo",
    "TadoCloudSync",
    "homekit_uuids",
]
