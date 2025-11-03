# API Key Authentication Implementation Summary

## Overview
Implemented optional multi-key authentication for the TadoLocal REST API. The system supports zero or multiple API keys separated by whitespace, providing flexible access control without breaking backward compatibility.

## Implementation Details

### 1. Server-Side Authentication (`tado_local/routes.py`)

**Configuration:**
- Environment variable: `TADO_API_KEYS`
- Format: Space-separated API keys
- Example: `TADO_API_KEYS="key1 key2 key3"`
- If not set or empty: authentication is disabled (backward compatible)

**Key Components:**
```python
# Parse environment variable into a set of valid keys
API_KEYS = set(key.strip() for key in API_KEYS_RAW.split() if key.strip())

# Authentication dependency for FastAPI
def get_api_key(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    # If no keys configured, disable authentication
    if not API_KEYS:
        return None
    
    # If keys are configured, require valid Bearer token
    if not credentials or credentials.credentials not in API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid authentication")
    
    return credentials.credentials
```

**Protected Endpoints:**
All API endpoints except web UI are protected:
- `/api` - API info
- `/status` - System status
- `/zones`, `/zones/{id}`, `/zones/{id}/set` - Zone management
- `/devices`, `/devices/{id}`, `/devices/{id}/set` - Device management
- `/thermostats`, `/thermostats/{id}`, `/thermostats/{id}/set` - Thermostat control
- `/accessories` - HomeKit accessories
- `/events` - Server-Sent Events stream
- `/refresh`, `/refresh/cloud` - Admin operations

**Unprotected Endpoints:**
- `/` - Web UI (serves static/index.html)
- `/static/*` - Static files (CSS, JS, images)

**Logging:**
Server logs authentication status at startup:
- `✓ API authentication enabled (N key(s) configured)`
- `⚠ API authentication disabled (no TADO_API_KEYS configured)`

### 2. Client-Side Support (Domoticz Plugin)

**Already Implemented:**
The Domoticz plugin (`domoticz/plugin.py`) already had API key support:
- Mode3 parameter: "API Key" (password field)
- `getAuthHeaders()` method: Returns headers with optional Authorization
- All HTTP requests include authentication headers when configured

**No changes needed** - existing plugin implementation is compatible with multi-key server.

### 3. Documentation Updates

**Main README (`README.md`):**
- Removed single-key command-line option documentation
- Added multi-key environment variable documentation
- Clarified that multiple keys can be space-separated
- Explained use cases (different clients, key rotation, testing)
- Noted that web UI remains accessible without auth

**Domoticz README (`domoticz/README.md`):**
- Updated to reference multi-key server capability
- Clarified that server accepts multiple keys but client only needs one
- Documented that web UI remains accessible

### 4. Testing

**Test Script (`test_auth.py`):**
Created comprehensive test script that validates:
1. Server connection
2. Authentication status detection
3. Rejection of requests without credentials (when auth enabled)
4. Rejection of requests with wrong credentials
5. Acceptance of each configured valid key
6. Web UI accessibility without authentication

**Usage:**
```bash
# Test with authentication enabled
export TADO_API_KEYS="key1 key2 key3"
python test_auth.py

# Test with authentication disabled
unset TADO_API_KEYS
python test_auth.py
```

## Security Model

**Design Philosophy:**
- **Optional by default** - No authentication required if not configured
- **Backward compatible** - Existing deployments continue to work
- **Flexible** - Multiple keys for different clients/purposes
- **Pragmatic** - Basic protection for local networks, not enterprise-grade

**Security Characteristics:**
- ✅ Prevents accidental access from unauthorized clients
- ✅ Allows key rotation (add new, remove old)
- ✅ Supports multiple clients with distinct keys
- ✅ Web UI remains accessible for troubleshooting
- ⚠️ HTTP-based (not encrypted in transit)
- ⚠️ Not suitable for internet-exposed endpoints
- ⚠️ Basic authentication, not OAuth/JWT

**Intended Use Cases:**
- Home lab environments on trusted networks
- Distinguishing between different automation systems
- Preventing accidental API calls from development tools
- Simple access logging/auditing

## Configuration Examples

### Single Key (Simple)
```bash
export TADO_API_KEYS="my-secret-key"
tado-local
```

### Multiple Keys (Multi-Client)
```bash
export TADO_API_KEYS="domoticz-key homeassistant-key nodered-key"
tado-local
```

### Windows (PowerShell)
```powershell
$env:TADO_API_KEYS="key1 key2"
tado-local
```

### Systemd Service
```ini
[Service]
Environment="TADO_API_KEYS=production-key backup-key"
ExecStart=/usr/local/bin/tado-local
```

## Client Usage

### cURL
```bash
# With authentication
curl -H "Authorization: Bearer my-secret-key" http://localhost:4407/zones

# Without authentication (if server has no keys configured)
curl http://localhost:4407/zones
```

### Python Requests
```python
import requests

# Configure headers
headers = {"Authorization": "Bearer my-secret-key"}

# Make authenticated request
response = requests.get("http://localhost:4407/zones", headers=headers)
zones = response.json()
```

### Domoticz Plugin
Configure in web UI:
1. Setup → Hardware → Add Hardware
2. Type: Tado Local
3. API URL: http://localhost:4407
4. API Key: my-secret-key
5. Add

## Implementation Notes

### Why Space-Separated?
- Simple to read/write in shell
- Natural for environment variables
- Easy to parse in Python: `str.split()`
- Avoids escaping issues with special characters

### Why Not Semicolon-Separated?
Initial consideration was semicolon (`;`), but:
- Requires escaping in some shells
- Less common for environment variables
- Whitespace is more standard (PATH, PYTHONPATH pattern)

### FastAPI Integration
Used FastAPI's built-in security features:
- `HTTPBearer` - Standard OAuth2 Bearer token scheme
- `Depends()` - Dependency injection for authentication
- `auto_error=False` - Allows optional authentication
- Proper HTTP 401 responses with WWW-Authenticate header

### Error Handling
- 401 Unauthorized: Invalid or missing credentials (when auth enabled)
- No error: Authentication disabled (backward compatible)
- Web UI always accessible (never returns 401)

## Testing Checklist

- [x] Server starts with no API keys (authentication disabled)
- [x] Server starts with one API key (single-key mode)
- [x] Server starts with multiple API keys (multi-key mode)
- [x] Server logs authentication status at startup
- [x] API endpoints accept valid keys
- [x] API endpoints reject invalid keys (401)
- [x] API endpoints reject missing keys when auth enabled (401)
- [x] Web UI accessible without authentication
- [x] Static files accessible without authentication
- [x] Domoticz plugin works with API key configured
- [x] Domoticz plugin works without API key (auth disabled)
- [x] Test script validates all scenarios
- [x] Documentation updated (main README, Domoticz README)
- [x] Code compiles without syntax errors

## Future Enhancements (Not Implemented)

Potential improvements for future consideration:
- Key management API (add/remove keys at runtime)
- Key rotation with grace period
- Per-key permissions (read-only vs. read-write)
- Request logging with key identification
- Rate limiting per key
- HTTPS support for production deployments
- Integration with external auth providers (OAuth, LDAP)

## Backward Compatibility

**100% Backward Compatible:**
- Existing deployments: No configuration change needed
- Default behavior: Authentication disabled
- Existing clients: Continue to work without modification
- Web UI: Always accessible
- Domoticz plugin: Works with or without authentication

**Migration Path:**
1. Update server code (this implementation)
2. No changes needed initially (auth disabled by default)
3. When ready, set `TADO_API_KEYS` environment variable
4. Configure clients with API keys (Domoticz, scripts, etc.)
5. Keys are validated, unauthorized access blocked
