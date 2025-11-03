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

"""Tado Cloud API integration for supplementary data.

API Call Strategy (optimized for rate limits):
==============================================

Token Management:
-----------------
- Access tokens: Valid for 10 minutes (600s)
- Refresh tokens: Valid for 30 days, rotate on each use
- Strategy: Lazy refresh - only when making API calls (via get_headers)
- Impact: Only refreshes when API is actually used (not continuously)

Data Caching & Sync:
-------------------
- 4 JSON endpoints:
  - zoneStates, deviceList: cached for 4 hours (battery/status data)
  - home, zones: cached for 24 hours (static configuration)
- ETag support: Server returns 304 if data unchanged (minimal rate limit impact)
- Background sync: Differential caching strategy
  - Dynamic data (battery, status): every 4 hours (~6 calls/day)
  - Static data (config, zones): every 24 hours (~1 call/day)
  - Total: ~7 API calls per day (well under 100/day limit)
- Strategy: Fetch once per day automatically, or on-demand with force_refresh=True
- Impact: ~4-8 data calls per day (1 sync cycle, may get 304 cached responses)

Rate Limit Budget:
------------------
- Free tier (post-2024): ~100 calls/day
- Subscriber tier: ~18,000 calls/day
- Current usage: ~4-8 calls/day (1 daily sync, token refresh only when needed)
- Note: Token refresh calls may not count against data call limits
- Recommendation: Monitor rate_limit headers and adjust sync interval if needed

Benefits:
---------
1. Minimal API calls - only when needed or once per day for sync
2. Lazy token refresh - no continuous background polling
3. Automatic updates via differential caching
   - Battery status and device online/offline: Updated every 4 hours
   - Zone names and home configuration: Updated every 24 hours
   - Minimizes API calls while keeping critical data fresh
4. Relies on HomeKit for real-time data (primary data source)
5. Well within free tier limits
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any, TYPE_CHECKING
from datetime import datetime, timedelta
import sqlite3
import json
from .database import CLOUD_SCHEMA
if TYPE_CHECKING:
    import aiohttp

try:
    import aiohttp
except ImportError:
    aiohttp = None

logger = logging.getLogger('tado-local')


class RateLimitInfo:
    """
    Tado API rate limit information parsed from response headers.

    Headers format (based on Tado API documentation):
    - ratelimit-policy: "perday";q=100;w=86400
      - q: quota (granted calls per period)
      - w: window (period in seconds)
    - ratelimit: "perday";r=95;t=3600
      - r: remaining calls in current period
      - t: time in seconds until reset (optional)
    """

    def __init__(
        self,
        granted_calls: Optional[int] = None,
        remaining_calls: Optional[int] = None,
        period_seconds: Optional[int] = None,
        resets_at: Optional[datetime] = None
    ):
        self.granted_calls = granted_calls
        self.remaining_calls = remaining_calls
        self.period_seconds = period_seconds
        self.resets_at = resets_at

    @classmethod
    def from_headers(cls, headers: Dict[str, str]) -> 'RateLimitInfo':
        """
        Parse rate limit info from Tado API response headers.

        Args:
            headers: Response headers dict

        Returns:
            RateLimitInfo instance
        """
        policy_header = headers.get('ratelimit-policy', '')
        limit_header = headers.get('ratelimit', '')

        if not policy_header or not limit_header:
            return cls()

        # Parse policy: "perday";q=100;w=86400
        policy_parts = cls._parse_header(policy_header)
        granted_calls = policy_parts.get('q')
        period_seconds = policy_parts.get('w')

        # Parse limit: "perday";r=95;t=3600
        limit_parts = cls._parse_header(limit_header)
        remaining_calls = limit_parts.get('r')
        reset_seconds = limit_parts.get('t')

        # Calculate reset time if provided
        resets_at = None
        if reset_seconds is not None:
            resets_at = datetime.now() + timedelta(seconds=reset_seconds)

        return cls(
            granted_calls=granted_calls,
            remaining_calls=remaining_calls,
            period_seconds=period_seconds,
            resets_at=resets_at
        )

    @staticmethod
    def _parse_header(header: str) -> Dict[str, Optional[int]]:
        """
        Parse Tado rate limit header format.

        Example: '"perday";q=100;w=86400' -> {'q': 100, 'w': 86400}
        """
        parts = {}
        for part in header.split(';'):
            part = part.strip().strip('"')
            if '=' in part:
                key, value = part.split('=', 1)
                try:
                    parts[key.strip()] = int(value.strip())
                except ValueError:
                    pass
        return parts

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for status reporting."""
        return {
            'granted_calls': self.granted_calls,
            'remaining_calls': self.remaining_calls,
            'period_seconds': self.period_seconds,
            'resets_at': self.resets_at.isoformat() if self.resets_at else None,
            'usage_percent': round((1 - (self.remaining_calls or 0) / (self.granted_calls or 1)) * 100, 1) if self.granted_calls and self.remaining_calls is not None else None
        }

    def __repr__(self) -> str:
        if self.granted_calls and self.remaining_calls is not None:
            return f"<RateLimitInfo: {self.remaining_calls}/{self.granted_calls} remaining>"
        return "<RateLimitInfo: unknown>"


class TadoCloudAPI:
    """
    Tado Cloud API client using OAuth 2.0 Device Authorization Grant.

    Used sparingly to fetch supplementary data not available via HomeKit:
    - Zone mappings and room names
    - Device assignments to zones
    - Home configuration

    Rate-limited by Tado, so only use when necessary (once per day or on-demand).
    """

    # OAuth endpoints (updated 2024 - using new login.tado.com endpoints)
    AUTH_BASE_URL = "https://login.tado.com/oauth2"
    API_BASE_URL = "https://my.tado.com/api/v2"
    CLIENT_ID = "1bb50063-6b0c-4d11-bd99-387f4a91cc46"

    def __init__(self, db_path: str):
        """Initialize Tado Cloud API client.

        Args:
            db_path: Path to SQLite database for token storage
        """
        self.db_path = db_path
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expires_at: Optional[float] = None
        self.home_id: Optional[int] = None
        self._ensure_schema()
        self._load_tokens()

        # Background token refresh task
        self._refresh_task: Optional[asyncio.Task] = None

        # Current authentication state (for status endpoint)
        self.auth_verification_uri: Optional[str] = None
        self.auth_user_code: Optional[str] = None
        self.auth_expires_at: Optional[float] = None
        self.is_authenticating: bool = False

        # Rate limit tracking
        self.rate_limit: RateLimitInfo = RateLimitInfo()

    def _ensure_schema(self):
        """Ensure the cloud API tables exist."""
        conn = sqlite3.connect(self.db_path)
        conn.executescript(CLOUD_SCHEMA)
        conn.commit()
        conn.close()

    def _load_tokens(self):
        """Load stored tokens from database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT access_token, refresh_token, expires_at, home_id
            FROM tado_cloud_tokens
            WHERE id = 1
        """)
        row = cursor.fetchone()
        conn.close()

        if row:
            self.access_token, self.refresh_token, self.token_expires_at, self.home_id = row

            # Check if token is expired
            if self.token_expires_at and time.time() < self.token_expires_at:
                logger.info("Loaded valid Tado Cloud API tokens from database")
                logger.info(f"Token expires in {int(self.token_expires_at - time.time())} seconds")
            else:
                logger.warning("Stored Tado Cloud API token is expired, re-authentication required")
                self.access_token = None

    def _save_tokens(self, token_data: Dict[str, Any]):
        """Save tokens to database.

        Args:
            token_data: Token response from OAuth endpoint

        Notes:
            - Access token: Valid for 10 minutes (600s)
            - Refresh token: Valid for 30 days or until used (with rotation)
        """
        self.access_token = token_data.get('access_token')

        # Only update refresh_token if provided (refresh token rotation)
        if 'refresh_token' in token_data:
            self.refresh_token = token_data.get('refresh_token')

        # Calculate expiration time (subtract 30s buffer for network latency)
        expires_in = token_data.get('expires_in', 600)
        self.token_expires_at = time.time() + expires_in - 30

        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO tado_cloud_tokens
            (id, access_token, refresh_token, token_type, expires_at, home_id, scope, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            self.access_token,
            self.refresh_token,
            token_data.get('token_type', 'Bearer'),
            self.token_expires_at,
            self.home_id,
            token_data.get('scope', '')
        ))
        conn.commit()
        conn.close()

        logger.info(f"Saved Tado Cloud API tokens (access token expires in {expires_in}s)")

    async def authenticate(self) -> bool:
        """
        Perform OAuth 2.0 Device Authorization Grant flow.

        This implements the flow described at:
        https://help.tado.com/en/articles/8565472-how-do-i-authenticate-to-access-the-rest-api

        Returns:
            True if authentication succeeded, False otherwise
        """
        if aiohttp is None:
            logger.error("aiohttp not installed - cannot use Tado Cloud API")
            logger.error("Install with: pip install aiohttp")
            return False

        try:
            self.is_authenticating = True

            async with aiohttp.ClientSession() as session:
                # Step 1: Request device code
                logger.info("Requesting device authorization code from Tado...")

                async with session.post(
                    f"{self.AUTH_BASE_URL}/device_authorize",
                    params={
                        'client_id': self.CLIENT_ID,
                        'scope': 'offline_access'
                    }
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"Failed to request device code: HTTP {resp.status} - {error_text}")
                        self.is_authenticating = False
                        return False

                    device_data = await resp.json()

                device_code = device_data['device_code']
                user_code = device_data['user_code']
                verification_uri = device_data['verification_uri_complete']
                expires_in = device_data['expires_in']
                interval = device_data.get('interval', 5)

                # Store for status endpoint
                self.auth_verification_uri = verification_uri
                self.auth_user_code = user_code
                self.auth_expires_at = time.time() + expires_in

                # Step 2: Display user instructions
                logger.info("")
                logger.info("=" * 70)
                logger.info("TADO CLOUD API AUTHENTICATION REQUIRED")
                logger.info("=" * 70)
                logger.info("")
                logger.info("To connect to Tado Cloud API, please visit this URL:")
                logger.info("")
                logger.info(f"    {verification_uri}")
                logger.info("")
                logger.info(f"Your code: {user_code}")
                logger.info("")
                logger.info("After authorizing, return here. Polling for authorization...")
                logger.info(f"(This code expires in {expires_in} seconds)")
                logger.info("")
                logger.info("Or check the /status endpoint for the URL:")
                logger.info(f"    curl http://localhost:4407/status")
                logger.info("=" * 70)
                logger.info("")

                # Step 3: Poll for token
                poll_start = time.time()
                poll_timeout = poll_start + expires_in

                while time.time() < poll_timeout:
                    await asyncio.sleep(interval)

                    async with session.post(
                        f"{self.AUTH_BASE_URL}/token",
                        params={
                            'client_id': self.CLIENT_ID,
                            'device_code': device_code,
                            'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'
                        }
                    ) as resp:
                        token_data = await resp.json()

                        if resp.status == 200:
                            # Success!
                            logger.info("✓ Successfully authenticated with Tado Cloud API!")
                            self._save_tokens(token_data)

                            # Clear auth state
                            self.auth_verification_uri = None
                            self.auth_user_code = None
                            self.auth_expires_at = None
                            self.is_authenticating = False

                            # Fetch home_id
                            await self._fetch_home_id(session)

                            return True

                        elif resp.status == 400:
                            error = token_data.get('error')

                            if error == 'authorization_pending':
                                # Still waiting for user to authorize
                                elapsed = int(time.time() - poll_start)
                                remaining = int(poll_timeout - time.time())
                                logger.debug(f"Waiting for authorization... ({elapsed}s elapsed, {remaining}s remaining)")
                                continue

                            elif error == 'slow_down':
                                # Increase polling interval
                                interval += 5
                                logger.debug(f"Slowing down polling interval to {interval}s")
                                continue

                            elif error == 'expired_token':
                                logger.error("Device code expired. Will start new authentication.")
                                self.auth_verification_uri = None
                                self.auth_user_code = None
                                self.auth_expires_at = None
                                self.is_authenticating = False
                                return False

                            elif error == 'access_denied':
                                logger.error("Access denied by user.")
                                self.auth_verification_uri = None
                                self.auth_user_code = None
                                self.auth_expires_at = None
                                self.is_authenticating = False
                                return False

                            else:
                                logger.error(f"OAuth error: {error}")
                                self.is_authenticating = False
                                return False

                        else:
                            logger.error(f"Unexpected response: HTTP {resp.status}")
                            self.is_authenticating = False
                            return False

                logger.error("Authentication timeout - device code expired")
                self.auth_verification_uri = None
                self.auth_user_code = None
                self.auth_expires_at = None
                self.is_authenticating = False
                return False

        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            self.is_authenticating = False
            self.auth_verification_uri = None
            self.auth_user_code = None
            self.auth_expires_at = None
            return False

    async def _fetch_home_id(self, session):
        """Fetch and store the home_id after authentication.

        Args:
            session: aiohttp.ClientSession instance
        """
        try:
            async with session.get(
                f"{self.API_BASE_URL}/me",
                headers={'Authorization': f'Bearer {self.access_token}'}
            ) as resp:
                if resp.status == 200:
                    user_data = await resp.json()
                    homes = user_data.get('homes', [])
                    if homes:
                        self.home_id = homes[0]['id']
                        logger.info(f"Detected home_id: {self.home_id}")

                        # Update database with home_id
                        conn = sqlite3.connect(self.db_path)
                        conn.execute(
                            "UPDATE tado_cloud_tokens SET home_id = ? WHERE id = 1",
                            (self.home_id,)
                        )
                        conn.commit()
                        conn.close()
                    else:
                        logger.warning("No homes found in user account")
                else:
                    logger.warning(f"Failed to fetch home_id: HTTP {resp.status}")
        except Exception as e:
            logger.warning(f"Failed to fetch home_id: {e}")

    async def refresh_access_token(self) -> bool:
        """
        Refresh the access token using the refresh token.

        Returns:
            True if refresh succeeded, False otherwise
        """
        if not self.refresh_token:
            logger.warning("No refresh token available")
            return False

        if aiohttp is None:
            logger.error("aiohttp not installed")
            return False

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.AUTH_BASE_URL}/token",
                    params={
                        'client_id': self.CLIENT_ID,
                        'refresh_token': self.refresh_token,
                        'grant_type': 'refresh_token'
                    }
                ) as resp:
                    if resp.status == 200:
                        token_data = await resp.json()
                        self._save_tokens(token_data)
                        logger.info("✓ Successfully refreshed Tado Cloud API token")
                        return True
                    else:
                        error_data = await resp.text()
                        logger.error(f"Failed to refresh token: HTTP {resp.status} - {error_data}")
                        # Clear invalid tokens
                        self.access_token = None
                        self.refresh_token = None
                        return False

        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            return False

    async def ensure_authenticated(self) -> bool:
        """
        Ensure we have a valid access token.

        Token lifetime strategy:
        - Access tokens are valid for 10 minutes (use until expiry)
        - Refresh tokens are valid for 30 days (rotated on each use)
        - If access token expired, refresh it using refresh token
        - If refresh fails (token expired after 30 days), re-authenticate

        Returns:
            True if we have a valid token, False if authentication is pending
        """
        # Check if we have a valid access token
        if self.access_token and self.token_expires_at:
            if time.time() < self.token_expires_at:
                return True
            else:
                logger.debug("Access token expired, will refresh")

        # Try to refresh if we have a refresh token
        if self.refresh_token:
            logger.info("Refreshing access token...")
            if await self.refresh_access_token():
                logger.info("✓ Access token refreshed successfully")
                return True
            else:
                logger.warning("Token refresh failed - refresh token may have expired after 30 days")
                logger.info("Starting new authentication flow...")

        # Need to authenticate - start in background if not already running
        if not self.is_authenticating:
            logger.info("No valid token, starting authentication flow...")
            # Start authentication in background task
            asyncio.create_task(self.authenticate())

        return False

    def start_background_sync(self):
        """Start background task with differential caching (4h battery, 24h config)."""
        if self._refresh_task and not self._refresh_task.done():
            logger.debug("Background sync already running")
            return

        self._refresh_task = asyncio.create_task(self._background_sync_loop())
        logger.info("Started background cloud sync (4h battery, 24h config)")

    async def _background_sync_loop(self):
        """Background task to sync cloud data with differential caching strategy.

        Strategy:
        - Dynamic data (battery status, online/offline): Every 4 hours (6 calls/day)
          - zoneStates (includes battery status)
          - deviceList (includes connection status)
        - Static data (zone names, home config): Every 24 hours (1 call/day)
          - home info
          - zones (room configuration)

        Total: ~7 API calls per day (well under 100/day limit)
        Token refresh happens on-demand when making API calls.
        """
        # Track separate timers for dynamic vs static data
        last_dynamic_sync = 0
        last_static_sync = 0
        dynamic_interval = 4 * 3600  # 4 hours
        static_interval = 24 * 3600  # 24 hours

        while True:
            try:
                # Wait 1 minute on first start to let authentication complete
                if last_dynamic_sync == 0:
                    await asyncio.sleep(60)

                current_time = time.time()

                # Try to sync if authenticated
                if self.is_authenticated() or await self.ensure_authenticated():
                    # Determine what needs syncing
                    sync_dynamic = (current_time - last_dynamic_sync) >= dynamic_interval
                    sync_static = (current_time - last_static_sync) >= static_interval

                    if sync_dynamic or sync_static:
                        sync_type = []
                        if sync_dynamic:
                            sync_type.append("dynamic (battery/status)")
                        if sync_static:
                            sync_type.append("static (config)")

                        logger.info(f"Running cloud sync: {', '.join(sync_type)}...")

                        try:
                            # Always fetch dynamic data when it's time
                            zone_states = None
                            devices = None
                            if sync_dynamic:
                                zone_states = await self.get_zone_states()
                                devices = await self.get_device_list()
                                if devices:
                                    logger.info(f"✓ Synced {len(devices)} devices (battery status)")
                                last_dynamic_sync = current_time

                            # Only fetch static data every 24 hours
                            home_info = None
                            zones = None
                            if sync_static:
                                home_info = await self.get_home_info()
                                zones = await self.get_zones()
                                if home_info:
                                    logger.info(f"✓ Synced home info: {home_info.get('name', 'unknown')}")
                                if zones:
                                    logger.info(f"✓ Synced {len(zones)} zones (configuration)")
                                last_static_sync = current_time

                            # Sync to database
                            from .sync import TadoCloudSync
                            sync = TadoCloudSync(self.db_path)

                            # Pass what we fetched (None values will be skipped in sync)
                            if await sync.sync_all(self,
                                                   home_data=home_info,
                                                   zones_data=zones,
                                                   zone_states_data=zone_states,
                                                   devices_data=devices):
                                # Calculate next sync time (use shorter of the two intervals)
                                next_dynamic_in = dynamic_interval - (time.time() - last_dynamic_sync)
                                next_static_in = static_interval - (time.time() - last_static_sync)
                                sleep_time = max(60, min(next_dynamic_in, next_static_in))
                            else:
                                logger.error("Cloud sync failed")
                                # Sync failed - retry in 1 hour
                                sleep_time = 3600

                        except Exception as e:
                            logger.error(f"Error during cloud sync: {e}")
                            # Error during sync - retry in 1 hour
                            sleep_time = 3600
                    else:
                        # Calculate next sync time
                        next_dynamic_in = dynamic_interval - (current_time - last_dynamic_sync)
                        next_static_in = static_interval - (current_time - last_static_sync)
                        sleep_time = max(60, min(next_dynamic_in, next_static_in))
                else:
                    logger.warning("Skipping cloud sync - not authenticated")
                    # Not authenticated - retry in 5 minutes
                    sleep_time = 300

                # Sleep until next sync
                if sleep_time >= 3600:
                    hours = sleep_time / 3600
                    logger.info(f"Next cloud sync in {hours:.1f} hour(s)")
                else:
                    minutes = sleep_time / 60
                    logger.info(f"Next cloud sync in {minutes:.0f} minute(s)")
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                logger.info("Background cloud sync task cancelled")
                break
            except Exception as e:
                logger.error(f"Error in background sync loop: {e}")
                await asyncio.sleep(3600)  # Wait 1 hour before retrying

    async def stop_background_sync(self):
        """Stop the background sync task."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped background cloud sync task")

    def is_authenticated(self) -> bool:
        """Check if we have refresh token (may need access token refresh)."""
        return self.refresh_token is not None

    def has_valid_access_token(self) -> bool:
        """Check if we have a valid access token right now."""
        return (
            self.access_token is not None
            and self.token_expires_at is not None
            and time.time() < self.token_expires_at
        )

    async def get_headers(self) -> Dict[str, str]:
        """
        Get authenticated request headers.

        Ensures token is valid before returning (lazy refresh).

        Returns:
            Headers dict with Authorization bearer token
        """
        if not await self.ensure_authenticated():
            raise RuntimeError("Failed to authenticate with Tado Cloud API")

        return {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }

    def _update_rate_limit(self, headers: Dict[str, str]):
        """
        Update rate limit info from response headers.

        Args:
            headers: Response headers dict
        """
        new_limit = RateLimitInfo.from_headers(headers)
        if new_limit.granted_calls:
            self.rate_limit = new_limit

            # Log warning if getting close to limit
            if new_limit.remaining_calls is not None and new_limit.granted_calls:
                usage_pct = (1 - new_limit.remaining_calls / new_limit.granted_calls) * 100
                if usage_pct > 80:
                    logger.warning(
                        f"Tado API usage high: {new_limit.remaining_calls}/{new_limit.granted_calls} "
                        f"calls remaining ({usage_pct:.1f}% used)"
                    )

    # ========================================================================
    # Cloud API Caching Infrastructure
    # ========================================================================

    def _get_cache(self, endpoint: str) -> Optional[Dict[str, Any]]:
        """
        Get cached response for an endpoint.

        Args:
            endpoint: API endpoint path (e.g., 'zones', 'deviceList')

        Returns:
            Cached data dict or None if not cached or expired
        """
        if not self.home_id:
            return None

        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT response_data, etag, expires_at
            FROM tado_cloud_cache
            WHERE home_id = ? AND endpoint = ?
        """, (self.home_id, endpoint))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        response_data, etag, expires_at = row

        # Check if expired
        from datetime import datetime
        expires_dt = datetime.fromisoformat(expires_at)
        if datetime.now() >= expires_dt:
            logger.debug(f"Cache expired for endpoint '{endpoint}'")
            return None

        logger.debug(f"Cache hit for endpoint '{endpoint}' (expires: {expires_at})")
        return {
            'data': json.loads(response_data),
            'etag': etag
        }

    def _set_cache(self, endpoint: str, response_data: Any, etag: Optional[str],
                   cache_lifetime_hours: float = 4.0):
        """
        Store response in cache.

        Args:
            endpoint: API endpoint path
            response_data: Response data to cache (will be JSON serialized)
            etag: ETag header from response (for conditional requests)
            cache_lifetime_hours: How long to cache (default: 4 hours)
        """
        if not self.home_id:
            logger.warning("Cannot cache: no home_id set")
            return

        from datetime import datetime, timedelta

        expires_at = datetime.now() + timedelta(hours=cache_lifetime_hours)
        response_json = json.dumps(response_data)

        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO tado_cloud_cache
            (home_id, endpoint, response_data, etag, fetched_at, expires_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        """, (self.home_id, endpoint, response_json, etag, expires_at.isoformat()))
        conn.commit()
        conn.close()

        logger.debug(f"Cached endpoint '{endpoint}' (expires: {expires_at.isoformat()})")

    def _clear_cache(self, endpoint: Optional[str] = None):
        """
        Clear cached data.

        Args:
            endpoint: Specific endpoint to clear, or None to clear all for home
        """
        if not self.home_id:
            return

        conn = sqlite3.connect(self.db_path)
        if endpoint:
            conn.execute("""
                DELETE FROM tado_cloud_cache
                WHERE home_id = ? AND endpoint = ?
            """, (self.home_id, endpoint))
            logger.debug(f"Cleared cache for endpoint '{endpoint}'")
        else:
            conn.execute("""
                DELETE FROM tado_cloud_cache
                WHERE home_id = ?
            """, (self.home_id,))
            logger.debug(f"Cleared all cache for home_id {self.home_id}")
        conn.commit()
        conn.close()

    async def _fetch_with_cache(
        self,
        endpoint: str,
        cache_lifetime_hours: float = 24.0,
        force_refresh: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch data from Tado Cloud API with caching and ETag support.

        Args:
            endpoint: API endpoint path (e.g., 'zones', 'deviceList')
            cache_lifetime_hours: How long to cache the response
            force_refresh: Force fetch from API, ignoring cache

        Returns:
            Response data dict or None on error
        """
        if aiohttp is None:
            logger.error("aiohttp not installed")
            return None

        if not self.home_id:
            logger.error("Cannot fetch: no home_id set")
            return None

        # Check cache first (unless force refresh)
        if not force_refresh:
            cached = self._get_cache(endpoint)
            if cached:
                return cached['data']

        # Fetch from API
        try:
            headers = await self.get_headers()

            # Add If-None-Match header if we have an ETag
            cached = self._get_cache(endpoint)
            if cached and cached.get('etag'):
                headers['If-None-Match'] = cached['etag']

            url = f"{self.API_BASE_URL}/homes/{self.home_id}/{endpoint}"

            async with aiohttp.ClientSession() as session:
                logger.debug(f"Fetching {url}")
                async with session.get(url, headers=headers) as resp:
                    # Update rate limit tracking from response headers
                    self._update_rate_limit(resp.headers)

                    # 304 Not Modified - use cached data
                    if resp.status == 304:
                        logger.info(f"API returned 304 Not Modified for '{endpoint}' - using cached data")
                        if cached:
                            # Update expiry time
                            self._set_cache(endpoint, cached['data'], cached['etag'], cache_lifetime_hours)
                            return cached['data']
                        else:
                            logger.warning(f"Got 304 but no cache available for '{endpoint}'")
                            return None

                    # Success
                    elif resp.status == 200:
                        data = await resp.json()
                        etag = resp.headers.get('ETag')

                        # Cache the response
                        self._set_cache(endpoint, data, etag, cache_lifetime_hours)

                        logger.info(f"Fetched '{endpoint}' from API (ETag: {etag})")
                        return data

                    # Rate limit exceeded
                    elif resp.status == 429:
                        error_text = await resp.text()
                        logger.error(f"Rate limit exceeded for '{endpoint}': {error_text}")
                        logger.warning(f"Tado API rate limit: {self.rate_limit.remaining_calls}/{self.rate_limit.granted_calls} calls remaining")
                        return None

                    # Error
                    else:
                        error_text = await resp.text()
                        logger.error(f"Failed to fetch '{endpoint}': HTTP {resp.status} - {error_text}")
                        return None

        except Exception as e:
            logger.error(f"Error fetching '{endpoint}': {e}")
            return None

    # ========================================================================
    # Tado Cloud API Methods
    # ========================================================================

    async def get_home_info(self, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get home information from Tado Cloud API.

        Args:
            force_refresh: Force fetch from API, ignoring cache

        Returns:
            Home info dict or None on error

        Note: Cached for 24 hours (static configuration data)
        """
        return await self._fetch_with_cache('', cache_lifetime_hours=24.0, force_refresh=force_refresh)

    async def get_zones(self, force_refresh: bool = False) -> Optional[list]:
        """
        Get zone list from Tado Cloud API.

        Args:
            force_refresh: Force fetch from API, ignoring cache

        Returns:
            List of zone dicts or None on error

        Note: Cached for 24 hours (static configuration data)
        """
        return await self._fetch_with_cache('zones', cache_lifetime_hours=24.0, force_refresh=force_refresh)

    async def get_zone_states(self, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get zone states from Tado Cloud API.

        Args:
            force_refresh: Force fetch from API, ignoring cache

        Returns:
            Zone states dict or None on error

        Note: Cached for 4 hours (includes battery status, changes frequently)
        """
        return await self._fetch_with_cache('zoneStates', cache_lifetime_hours=4.0, force_refresh=force_refresh)

    async def get_device_list(self, force_refresh: bool = False) -> Optional[list]:
        """
        Get device list from Tado Cloud API.

        Includes battery states and other metadata not available via HomeKit.

        Args:
            force_refresh: Force fetch from API, ignoring cache

        Returns:
            List of device dicts or None on error

        Note: Cached for 4 hours (includes battery status, changes frequently)
        """
        return await self._fetch_with_cache('deviceList', cache_lifetime_hours=4.0, force_refresh=force_refresh)

    async def refresh_all_cache(self):
        """Refresh all cached endpoints from Tado Cloud API."""
        logger.info("Refreshing all Tado Cloud API cache...")

        endpoints = [
            ('home info', self.get_home_info),
            ('zones', self.get_zones),
            ('zone states', self.get_zone_states),
            ('device list', self.get_device_list)
        ]

        results = {}
        for name, method in endpoints:
            try:
                data = await method(force_refresh=True)
                results[name] = 'success' if data else 'failed'
            except Exception as e:
                logger.error(f"Failed to refresh {name}: {e}")
                results[name] = f'error: {e}'

        logger.info(f"Cache refresh complete: {results}")
        return results
