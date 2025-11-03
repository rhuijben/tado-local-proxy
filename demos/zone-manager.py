#!/usr/bin/env python3
"""
Tado Zone Manager - Demo CLI tool for managing Tado zones via Tado Local API

A command-line utility for viewing and controlling Tado heating zones.
"""

import sys
import argparse
import os
import requests

# Configuration
API_BASE = os.environ.get('TADO_LOCAL_API', 'http://localhost:4407')

# Authentication - use first API key from TADO_API_KEYS if available
API_KEYS_RAW = os.environ.get('TADO_API_KEYS', '').strip()
API_KEY = API_KEYS_RAW.split()[0] if API_KEYS_RAW else None

zone_info = {}
home_info = {}
verbose = 0


def get_headers():
    """Get HTTP headers including optional Bearer token authentication"""
    headers = {'Content-Type': 'application/json'}
    if API_KEY:
        headers['Authorization'] = f'Bearer {API_KEY}'
    return headers


def get_zones_and_homes():
    """Get all zones and homes from Tado Local API"""
    try:
        response = requests.get(f"{API_BASE}/zones", headers=get_headers())
        response.raise_for_status()
        data = response.json()

        zones = data.get('zones', [])
        homes = data.get('homes', [])

        return zones, homes
    except Exception as e:
        print(f"Error fetching zones: {e}")
        sys.exit(1)


def resolve_zone_names(zone_specs):
    """
    Resolve zone specifications (IDs or names) to zone IDs.
    
    Args:
        zone_specs: List of zone IDs (int) or zone names (str)
        
    Returns:
        List of zone IDs (int)
    """
    global zone_info
    
    if not zone_info:
        zone_info, _ = get_zones_and_homes()
    
    resolved = []
    
    for spec in zone_specs:
        if isinstance(spec, int):
            # Already an ID
            resolved.append(spec)
        else:
            # Try to find by name (case-insensitive partial match)
            spec_lower = spec.lower()
            matched = False
            
            for zone in zone_info:
                zone_name_lower = zone['name'].lower()
                if spec_lower in zone_name_lower or zone_name_lower in spec_lower:
                    resolved.append(zone['zone_id'])
                    matched = True
                    if verbose > 0:
                        print(f"Matched zone name '{spec}' to '{zone['name']}' (ID: {zone['zone_id']})")
                    break
            
            if not matched:
                print(f"Error: No zone found matching '{spec}'")
                sys.exit(1)
    
    return resolved


def create_parser():
    """Create and configure argument parser"""
    parser = argparse.ArgumentParser(
        description='Tado Zone Manager - Control Tado heating zones via Tado Local API',
        epilog=f'''
Examples:
  %(prog)s                          List all zones with status
  %(prog)s -z 1 -t 21               Set zone 1 to 21°C
  %(prog)s --zone Living -t 19      Set zone "Living" to 19°C (matches by name)
  %(prog)s -z 1 -z 2 -d             Turn off zones 1 and 2
  %(prog)s --zone Living --limit 8  Set zone "Living" to max 8°C if enabled and higher
  %(prog)s -z 1 -r                  Re-enable zone 1 at target temperature
  %(prog)s -l -v                    List zones with verbose output
  %(prog)s --zone Living --limit 8 --quiet  Limit temp silently (for cron jobs)

Environment variables:
  TADO_LOCAL_API   API base URL (default: http://localhost:4407)
  TADO_API_KEYS    Space-separated API keys (uses first key if multiple)

Current API: {API_BASE}
Authentication: {'Enabled (Bearer token)' if API_KEY else 'Disabled (no API key)'}
''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('-l', '--list', action='store_true',
                        help='list zone status (default if no action specified)')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='increase verbosity (can be specified multiple times)')
    parser.add_argument('-q', '--quiet', action='store_true',
                        help='suppress all output (for cron jobs)')
    parser.add_argument('-z', '--zone', action='append', dest='zones',
                        metavar='ZONE_ID_OR_NAME',
                        help='select zone(s) by ID (integer) or name (string, case-insensitive partial match)')

    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument('-t', '--temperature', type=float, metavar='CELSIUS',
                              help='set temperature in zone(s) (>= 5 to enable heating)')
    action_group.add_argument('--limit-temp', '--limit', type=float, metavar='CELSIUS', dest='limit_temp',
                              help='limit temperature: only lower if zone is ON and above this value')
    action_group.add_argument('-d', '--disable', action='store_true',
                              help='disable heating (turn off)')
    action_group.add_argument('-r', '--reset', action='store_true',
                              help='reset to schedule (re-enable heating at current target temp)')

    return parser


def show_list(verbose, zones, quiet=False):
    """Display zone information"""
    global zone_info, home_info

    if not zone_info:
        zone_info, home_info = get_zones_and_homes()

    # Filter zones if specific ones requested
    display_zones = zone_info
    if zones:
        display_zones = [z for z in zone_info if z['zone_id'] in zones]

    if not display_zones:
        if not quiet:
            print("No zones found")
        return

    # Display home information if available (skip in quiet mode)
    if home_info and not quiet:
        for home in home_info:
            home_name = home.get('name', 'Unknown Home')
            print(f"== {home_name} ==")

    # Display header (skip in quiet mode)
    if verbose == 0 and not quiet:
        print("ID  Zone Name       Heat  Target   Status    Current  Humidity")
        print("--  --------------  ----  -------  --------  -------  --------")

    for zone_data in display_zones:
        zone_id = zone_data['zone_id']

        if verbose > 0:
            print(f"\nZone {zone_id}:")
            print(zone_data)
            print()

        # Extract data from Tado Local format (state is nested)
        state = zone_data.get('state', {})
        cur_temp = state.get('cur_temp_c')
        cur_hum = state.get('hum_perc')
        cur_heating = state.get('cur_heating', 0)  # 0=off, 1=heating, 2=cooling
        mode = state.get('mode', 0)  # mode: 0=OFF, 1=ON (enabled)
        target_temp = state.get('target_temp_c')

        # Format target temperature
        if mode == 0:
            setting = 'OFF'
        elif target_temp is not None:
            setting = f'{target_temp:4.1f}°C'
        else:
            setting = 'AUTO'

        # Activity indicator (for Heat column)
        heat_s = 'ON' if cur_heating > 0 else ''

        # Activity status (what the system is doing)
        if mode == 0:
            status_str = 'OFF'
        elif cur_heating == 1:
            status_str = 'HEAT'
        elif cur_heating == 2:
            status_str = 'COOL'
        else:
            status_str = 'IDLE'

        # Format output
        if cur_temp is not None and cur_hum is not None:
            print(f'{zone_id:<2}  {zone_data["name"]:<14}  {heat_s:>4}  {setting:>7}  {status_str:<8}  {cur_temp:5.1f}°C  {cur_hum:5.1f}%')
        else:
            print(f'{zone_id:<2}  {zone_data["name"]:<14}  {"":>4}  {"":>7}  {"-":<8}  {"":>5}     {"":>5}')


def set_temperature(zones, temp):
    """Set temperature for specified zones"""
    global zone_info

    if not zone_info:
        zone_info, _ = get_zones_and_homes()

    # Get zone names for display
    zone_names = {z['zone_id']: z['name'] for z in zone_info}

    for zone_id in zones:
        zone_name = zone_names.get(zone_id, f'Zone {zone_id}')

        try:
            if temp is not None and temp >= 1:
                # Set temperature (heating is auto-enabled for temp >= 5)
                print(f"Setting {zone_name} (ID: {zone_id}) to {temp:.1f}°C")

                payload = {"temperature": temp}

                response = requests.post(
                    f"{API_BASE}/zones/{zone_id}/set",
                    json=payload,
                    headers=get_headers()
                )
                response.raise_for_status()
                result = response.json()

                if verbose > 0:
                    print(f"  Response: {result}")

            elif temp is not None and temp == 0:
                # Turn off (set temperature to 0)
                print(f"Turning OFF {zone_name} (ID: {zone_id})")

                payload = {"temperature": 0}

                response = requests.post(
                    f"{API_BASE}/zones/{zone_id}/set",
                    json=payload,
                    headers=get_headers()
                )
                response.raise_for_status()

        except Exception as e:
            print(f"Error setting temperature for {zone_name}: {e}")
            sys.exit(1)


def reset_to_schedule(zones):
    """Reset zones to schedule by re-enabling heating at current target temperature"""
    global zone_info

    if not zone_info:
        zone_info, _ = get_zones_and_homes()

    # Get zone info for selected zones
    zone_data_map = {z['zone_id']: z for z in zone_info}

    for zone_id in zones:
        zone_data = zone_data_map.get(zone_id)
        if not zone_data:
            print(f"Warning: Zone {zone_id} not found")
            continue

        zone_name = zone_data['name']
        state = zone_data.get('state', {})
        target_temp = state.get('target_temp_c')

        try:
            if target_temp and target_temp >= 5:
                # Re-enable heating at the target temperature
                print(f"Re-enabling {zone_name} (ID: {zone_id}) at {target_temp:.1f}°C")

                payload = {"temperature": target_temp}

                response = requests.post(
                    f"{API_BASE}/zones/{zone_id}/set",
                    json=payload,
                    headers=get_headers()
                )
                response.raise_for_status()

                if verbose > 0:
                    print(f"  Response: {response.json()}")
            else:
                print(f"Warning: {zone_name} (ID: {zone_id}) has no valid target temperature, skipping")

        except Exception as e:
            print(f"Error resetting {zone_name}: {e}")
            sys.exit(1)


def limit_temperature(zones, limit_temp, quiet=False):
    """
    Limit temperature in zones: only lower if zone is ON and above the limit.
    Does nothing if zone is OFF or already at/below the limit.
    
    Args:
        zones: List of zone IDs
        limit_temp: Maximum allowed temperature
        quiet: Suppress output messages
    """
    global zone_info

    if not zone_info:
        zone_info, _ = get_zones_and_homes()

    # Get zone info for selected zones
    zone_data_map = {z['zone_id']: z for z in zone_info}

    for zone_id in zones:
        zone_data = zone_data_map.get(zone_id)
        if not zone_data:
            if not quiet:
                print(f"Warning: Zone {zone_id} not found")
            continue

        zone_name = zone_data['name']
        state = zone_data.get('state', {})
        mode = state.get('mode', 0)  # 0=OFF, 1=ON
        target_temp = state.get('target_temp_c')

        # Check if zone is OFF
        if mode == 0:
            if verbose > 0 and not quiet:
                print(f"Zone {zone_name} (ID: {zone_id}) is OFF, skipping")
            continue

        # Check if target temp is valid
        if target_temp is None:
            if not quiet:
                print(f"Warning: Zone {zone_name} (ID: {zone_id}) has no target temperature, skipping")
            continue

        # Check if target temp is already at or below limit
        if target_temp <= limit_temp:
            if verbose > 0 and not quiet:
                print(f"Zone {zone_name} (ID: {zone_id}) already at {target_temp:.1f}°C (<= {limit_temp:.1f}°C), no action needed")
            continue

        # Zone is ON and above limit - lower it
        try:
            if not quiet:
                print(f"Limiting {zone_name} (ID: {zone_id}) from {target_temp:.1f}°C to {limit_temp:.1f}°C")

            payload = {"temperature": limit_temp}

            response = requests.post(
                f"{API_BASE}/zones/{zone_id}/set",
                json=payload,
                headers=get_headers()
            )
            response.raise_for_status()

            if verbose > 0 and not quiet:
                print(f"  Response: {response.json()}")

        except Exception as e:
            if not quiet:
                print(f"Error limiting temperature for {zone_name}: {e}")
            sys.exit(1)


def main(argv):
    global zone_info, home_info, verbose

    # Parse arguments
    parser = create_parser()
    args = parser.parse_args(argv)

    verbose = args.verbose if not args.quiet else 0
    zone_specs = args.zones or []

    # Resolve zone names to IDs
    zones = []
    if zone_specs:
        # Convert string IDs to integers, keep strings as names
        parsed_specs = []
        for spec in zone_specs:
            try:
                parsed_specs.append(int(spec))
            except ValueError:
                parsed_specs.append(spec)
        
        zones = resolve_zone_names(parsed_specs)

    # Determine if we should list zones
    do_list = args.list

    # If no action specified, default to listing
    if not any([args.temperature is not None, args.limit_temp is not None, args.disable, args.reset, do_list]):
        do_list = True

    # If action specified without zones, require zones
    if (args.temperature is not None or args.limit_temp is not None or args.disable or args.reset) and not zones:
        parser.error("You must specify zone(s) with -z when setting temperature or changing state")

    # If listing without specific zones, get all zones
    if do_list and not zones:
        if not zone_info:
            zone_info, home_info = get_zones_and_homes()
        zones = [z['zone_id'] for z in zone_info]

    # Execute commands
    if args.disable:
        set_temperature(zones, 0)
    elif args.reset:
        reset_to_schedule(zones)
    elif args.temperature is not None:
        set_temperature(zones, args.temperature)
    elif args.limit_temp is not None:
        limit_temperature(zones, args.limit_temp, quiet=args.quiet)

    if do_list:
        show_list(verbose, zones, quiet=args.quiet)


if __name__ == "__main__":
    main(sys.argv[1:])
