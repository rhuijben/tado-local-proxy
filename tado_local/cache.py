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
"""SQLite-backed HomeKit characteristic cache."""

import logging
import sqlite3

from aiohomekit.characteristic_cache import CharacteristicCacheMemory
from aiohomekit import hkjson
from .database import HOMEKIT_SCHEMA

logger = logging.getLogger(__name__)


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
        # Ensure overall DB schema/migrations are applied first
        from .database import ensure_schema_and_migrate
        ensure_schema_and_migrate(self.db_path)

        conn = sqlite3.connect(self.db_path)
        conn.execute(HOMEKIT_SCHEMA)
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
