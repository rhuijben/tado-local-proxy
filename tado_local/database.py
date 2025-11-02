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

"""Database schema for Tado Local."""

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

CREATE TABLE IF NOT EXISTS tado_homes (
    tado_home_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    timezone TEXT,
    temperature_unit TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zones (
    zone_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tado_zone_id INTEGER,
    tado_home_id INTEGER,
    name TEXT NOT NULL,
    zone_type TEXT,
    leader_device_id INTEGER,
    order_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tado_home_id) REFERENCES tado_homes(tado_home_id) ON DELETE CASCADE,
    FOREIGN KEY (leader_device_id) REFERENCES devices(device_id) ON DELETE SET NULL,
    UNIQUE(tado_home_id, tado_zone_id)
);

CREATE INDEX IF NOT EXISTS idx_zones_order ON zones(order_id);
CREATE INDEX IF NOT EXISTS idx_zones_tado ON zones(tado_home_id, tado_zone_id);

CREATE TABLE IF NOT EXISTS devices (
    device_id INTEGER PRIMARY KEY AUTOINCREMENT,
    serial_number TEXT UNIQUE NOT NULL,
    aid INTEGER,
    zone_id INTEGER,
    tado_zone_id INTEGER,
    device_type TEXT,
    name TEXT,
    model TEXT,
    manufacturer TEXT,
    battery_state TEXT,
    firmware_version TEXT,
    is_zone_leader BOOLEAN DEFAULT 0,
    is_circuit_driver BOOLEAN DEFAULT 0,
    is_zone_driver BOOLEAN DEFAULT 0,
    duties TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (zone_id) REFERENCES zones(zone_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_devices_serial ON devices(serial_number);
CREATE INDEX IF NOT EXISTS idx_devices_zone ON devices(zone_id);
CREATE INDEX IF NOT EXISTS idx_devices_tado_zone ON devices(tado_zone_id);

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

HOMEKIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS homekit_cache (
    homekit_id TEXT PRIMARY KEY,
    config_num INTEGER NOT NULL,
    accessories TEXT NOT NULL,
    broadcast_key TEXT,
    state_num INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

CLOUD_SCHEMA = """
CREATE TABLE IF NOT EXISTS tado_cloud_tokens (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    access_token TEXT,
    refresh_token TEXT,
    token_type TEXT,
    expires_at REAL,
    home_id INTEGER,
    scope TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tado_cloud_cache (
    home_id INTEGER NOT NULL,
    endpoint TEXT NOT NULL,
    response_data TEXT NOT NULL,
    etag TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    PRIMARY KEY (home_id, endpoint)
);

CREATE INDEX IF NOT EXISTS idx_cloud_cache_expiry ON tado_cloud_cache(home_id, expires_at);
"""
