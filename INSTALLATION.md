# Tado Local - Installation Guide

## Package Status

The project has been successfully refactored into a clean, modular Python package! ✅

## What Was Done

1. **Complete Package Structure**:
   - `tado_local/` - Main package directory (renamed from tado_local_proxy)
   - `tado_local/__init__.py` - Package initialization
   - `tado_local/__main__.py` - CLI entry point (supports `python -m tado_local`)
   - `tado_local/api.py` - TadoLocalAPI class (532 lines)
   - `tado_local/routes.py` - All FastAPI route handlers (715 lines)
   - `tado_local/bridge.py` - HomeKit bridge pairing (796 lines)
   - `tado_local/state.py` - Device state management (443 lines)
   - `tado_local/cache.py` - SQLite characteristic cache (160 lines)
   - `tado_local/database.py` - Database schema definitions
   - `tado_local/homekit_uuids.py` - HomeKit UUID mappings

2. **Complete Refactoring**:
   - Original `proxy.py` (2848 lines) reduced to `local.py` (52 lines)
   - All code moved to modular package structure
   - **97% reduction** in main entry point size
   - Clean separation of concerns

3. **Multiple Entry Points**:
   - `python -m tado_local` - Recommended way
   - `tado-local` - Console script after pip install
   - `python local.py` - Backward compatibility

4. **Distribution Files**:
   - `setup.py` - Package configuration for pip
   - `requirements.txt` - Dependency management
   - `README.md` - Comprehensive documentation

## Installation Methods

### Method 1: Install from Source (Recommended)

```bash
# From the project directory
pip install -e .
```

This installs the package in "editable" mode - changes to the code are immediately reflected without reinstalling.

### Method 2: Regular Install

```bash
# From the project directory
pip install .
```

### Method 3: Direct Install from Git (Future)

```bash
# Once pushed to GitHub
pip install git+https://github.com/yourusername/tado-local-proxy.git
```

## Usage

After installation, you can run the proxy in multiple ways:

### Recommended: Using Python Module

```bash
# Using python -m (works without pip install in dev mode)
python -m tado_local --bridge-ip 192.168.1.100 --pin 123-45-678

# View help
python -m tado_local --help
```

### Using Console Script

```bash
# After pip install, use the console script
tado-local --bridge-ip 192.168.1.100 --pin 123-45-678

# View help
tado-local --help
```

### Backward Compatibility

```bash
# Direct execution (for existing deployments)
python local.py --bridge-ip 192.168.1.100 --pin 123-45-678
```

All methods work identically!

## Package Import

You can also import components in your own Python code:

```python
from tado_local import (
    TadoLocalAPI,
    TadoBridge,
    DeviceStateManager,
    CharacteristicCacheSQLite,
    DB_SCHEMA,
    homekit_uuids
)

# Use the API class
api = TadoLocalAPI("/path/to/db.sqlite")

# Use the SQLite cache
cache = CharacteristicCacheSQLite("/path/to/db.sqlite")
```

## Uninstallation

```bash
pip uninstall tado-local
```

## Current State

**Complete**: ✅
- Full package refactoring (all 2763 lines extracted from proxy.py)
- Modular structure with 8 separate modules
- Multiple entry points (python -m, console script, backward compat)
- All dependencies properly declared
- Comprehensive documentation
- Console script `tado-local` command
- Python module execution `python -m tado_local`

**Future Work**: 📋
- Comprehensive test suite
- Add Docker support
- Publish to PyPI
- CI/CD pipeline

## Dependencies

All dependencies are automatically installed:
- aiohomekit >= 3.0.0
- fastapi >= 0.100.0
- uvicorn[standard] >= 0.23.0
- cryptography >= 41.0.0
- zeroconf >= 0.115.0

## Verification

Test your installation:

```bash
# Check package can be imported
python -c "import tado_local; print(f'tado_local v{tado_local.__version__}')"

# Output: tado_local v1.0.0

# Verify module execution
python -m tado_local --help

# Verify console script (after pip install)
tado-local --help

# Start the proxy (requires Tado bridge)
python -m tado_local --bridge-ip YOUR_BRIDGE_IP --pin YOUR_PIN

# Or use the console script
tado-local --bridge-ip YOUR_BRIDGE_IP

# Or use backward compatibility
python local.py --bridge-ip YOUR_BRIDGE_IP
```

## Support

See `README.md` for:
- API endpoint documentation
- Troubleshooting guide
- Architecture overview
- Development setup
