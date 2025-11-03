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

zone_info = {}
home_info = {}
verbose = 0


def get_zones_and_homes():
    """Get all zones and homes from Tado Local API"""
    try:
        response = requests.get(f"{API_BASE}/zones")
        response.raise_for_status()
        data = response.json()
        
        zones = data.get('zones', [])
        homes = data.get('homes', [])
        
        return zones, homes
    except Exception as e:
        print(f"Error fetching zones: {e}")
        sys.exit(1)


def create_parser():
    """Create and configure argument parser"""
    parser = argparse.ArgumentParser(
        description='Tado Zone Manager - Control Tado heating zones via Tado Local API',
        epilog=f'''
Examples:
  %(prog)s                    List all zones with status
  %(prog)s -z 1 -t 21         Set zone 1 to 21°C
  %(prog)s -z 1 -z 2 -d       Turn off zones 1 and 2
  %(prog)s -z 1 -r            Re-enable zone 1 at target temperature
  %(prog)s -l -v              List zones with verbose output

Environment variables:
  TADO_LOCAL_API  API base URL (default: http://localhost:4407)

Current API: {API_BASE}
''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('-l', '--list', action='store_true',
                        help='list zone status (default if no action specified)')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='increase verbosity (can be specified multiple times)')
    parser.add_argument('-z', '--zone', type=int, action='append', dest='zones',
                        metavar='ZONE_ID',
                        help='select specific zone(s) by ID (can be specified multiple times)')
    
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument('-t', '--temperature', type=float, metavar='CELSIUS',
                              help='set temperature in zone(s) (>= 5 to enable heating)')
    action_group.add_argument('-d', '--disable', action='store_true',
                              help='disable heating (turn off)')
    action_group.add_argument('-r', '--reset', action='store_true',
                              help='reset to schedule (re-enable heating at current target temp)')
    
    return parser


def show_help():
    """Deprecated: help is now handled by argparse"""
    pass
def show_list(verbose, zones):
    """Display zone information"""
    global zone_info, home_info

    if not zone_info:
        zone_info, home_info = get_zones_and_homes()

    # Filter zones if specific ones requested
    display_zones = zone_info
    if zones:
        display_zones = [z for z in zone_info if z['zone_id'] in zones]

    if not display_zones:
        print("No zones found")
        return

    # Display home information if available
    if home_info:
        for home in home_info:
            home_name = home.get('name', 'Unknown Home')
            print(f"== {home_name} ==")
    
    # Display header
    if verbose == 0:
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
    
    print()


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
                    json=payload
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
                    json=payload
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
                    json=payload
                )
                response.raise_for_status()
                
                if verbose > 0:
                    print(f"  Response: {response.json()}")
            else:
                print(f"Warning: {zone_name} (ID: {zone_id}) has no valid target temperature, skipping")
                
        except Exception as e:
            print(f"Error resetting {zone_name}: {e}")
            sys.exit(1)


def main(argv):
    global zone_info, home_info, verbose

    # Parse arguments
    parser = create_parser()
    args = parser.parse_args(argv)
    
    verbose = args.verbose
    zones = args.zones or []
    
    # Determine if we should list zones
    do_list = args.list
    
    # If no action specified, default to listing
    if not any([args.temperature is not None, args.disable, args.reset, do_list]):
        do_list = True
    
    # If action specified without zones, require zones
    if (args.temperature is not None or args.disable or args.reset) and not zones:
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
    
    if do_list:
        show_list(verbose, zones)


if __name__ == "__main__":
    main(sys.argv[1:])
