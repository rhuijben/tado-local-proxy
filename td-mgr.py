#!/usr/bin/env python3

import sys
import getopt
import os
from datetime import datetime
from dateutil.parser import parse
from dateutil import tz
import requests

# Configuration
API_BASE = os.environ.get('TADO_LOCAL_API', 'http://localhost:4407')

zone_map = {'H': 1, 'J': 2, 'P': 3, 'S': 4, 'B': 5, 'T': 6, 'D': 7}
reverse_zone_map = {}
zone_info = {}


def time_str(time_str):
    given_time = parse(time_str).astimezone(tz.tzlocal())
    now = datetime.now().replace(tzinfo=tz.tzlocal())

    if (given_time - now).days < 1:
        return given_time.strftime('%H:%M')  # As Time
    elif (given_time - now).days < 7:
        return given_time.strftime('%A')  # Monday,..
    else:
        return given_time.strftime('%Y-%m-%d')


def zone_name(zone):
    global zone_map, zone_info

    if not zone_info:
        zone_info = get_zones()

    if zone in zone_map:
        zone = zone_map[zone]

    for z in zone_info:
        if int(z['zone_id']) == zone:
            return z['name']
    
    return f"Zone {zone}"


def get_zones():
    """Get all zones from Tado Local API"""
    try:
        response = requests.get(f"{API_BASE}/zones")
        response.raise_for_status()
        data = response.json()
        # API returns {"zones": [...], "homes": [...]}
        return data.get('zones', [])
    except Exception as e:
        print(f"Error fetching zones: {e}")
        sys.exit(1)


def show_help():
    print('My Tado Manager 0.0.2 (Tado Local Edition)')
    print(f'Using API: {API_BASE}')
    print('Usage: td-mgr-local <args>')
    print('-l               List status (when done processing)')
    print('-v               Turn up verbose level (can be specified more than once)')
    print('-z <nr|letter>   Select specific zone(s)')
    print()
    print('-t <centigrade>  Set temperature in zone(s) to specific temperature')
    print('                 (Use temp = 0 to turn OFF, >= 5 to enable heating)')
    print('-r               Turn off heating (same as -t 0)')
    print()
    print('Note: The -k and -x options from the original script are not')
    print('      supported by Tado Local API at this time.')
    print()
    print('Environment variables:')
    print('  TADO_LOCAL_API  API base URL (default: http://localhost:4407)')
    print()


def show_list(verbose, zones):
    global zone_info

    if not zone_info:
        zone_info = get_zones()

    if zones:
        zi = zone_info
        zone_info = []
        
        for z in zones:
            for i in zi:
                if z == i['zone_id']:
                    zone_info.append(i)

    for zone_data in zone_info:
        zone_id = zone_data['zone_id']
        zone_abbr = zone_id
        
        if zone_id in reverse_zone_map:
            zone_abbr = reverse_zone_map[zone_id]

        if verbose > 0:
            print(zone_data)

        # Extract data from Tado Local format (state is nested)
        state = zone_data.get('state', {})
        cur_temp = state.get('cur_temp_c')
        cur_hum = state.get('hum_perc')
        heating_active = state.get('cur_heating') == 1
        mode = state.get('mode', 0)
        target_temp = state.get('target_temp_c')
        
        # Format setting
        if mode == 0:
            setting = 'OFF'
        elif target_temp is not None:
            setting = f'{target_temp:4.1f}C'
        else:
            setting = 'AUTO'

        # Heating power
        heat_s = ''
        if heating_active:
            heat_s = 'ON'

        # Mode/termination type
        mode_str = ''
        if mode == 1:
            mode_str = 'HEAT'
        elif mode == 0:
            mode_str = 'OFF'

        # Next schedule (placeholder - not available in current API)
        next_s = ''

        # Extra info (placeholder - device details not in zone endpoint)
        extra = ''

        # Format output
        if cur_temp is not None and cur_hum is not None:
            print('%s %-12s %4s %5s %-8s %6s  %3.2fC %3.1f%%%s' % (
                zone_abbr, 
                zone_data['name'], 
                heat_s, 
                setting, 
                next_s, 
                mode_str, 
                cur_temp, 
                cur_hum, 
                extra
            ))
        else:
            print('%s %-12s - No data available' % (zone_abbr, zone_data['name']))


def set_temperature(zones, temp, mode):
    """Set temperature for specified zones"""
    for zone_id in zones:
        zone_n = zone_name(zone_id)
        
        try:
            if temp >= 1:
                # Set temperature (heating is auto-enabled for temp >= 5)
                print(f"{zone_n} set to {temp:0.1f}C")
                
                payload = {"temperature": temp}
                
                response = requests.post(
                    f"{API_BASE}/zones/{zone_id}/set",
                    json=payload
                )
                response.raise_for_status()
                result = response.json()
                
                if verbose > 0:
                    print(f"Response: {result}")
                    
            else:
                # Turn off (set temperature to 0)
                print(f"{zone_n} set to OFF")
                
                payload = {"temperature": 0}
                
                response = requests.post(
                    f"{API_BASE}/zones/{zone_id}/set",
                    json=payload
                )
                response.raise_for_status()
                
        except Exception as e:
            print(f"Error setting temperature for {zone_n}: {e}")
            sys.exit(1)


def main(argv):
    global zone_info, reverse_zone_map, verbose

    # Build reverse zone map
    for i in zone_map:
        reverse_zone_map[zone_map[i]] = i

    try:
        opts, args = getopt.getopt(argv, "hvlkrz:t:x:")
    except getopt.GetoptError as e:
        print(f"Error: {e}")
        show_help()
        sys.exit(2)

    verbose = 0
    zones = []
    do_list = False
    set_temp = None

    for opt, arg in opts:
        if opt == '-h':
            show_help()
            sys.exit()
        elif opt == '-v':
            verbose += 1
        elif opt == '-z':
            if arg in zone_map:
                arg = zone_map[arg]
            zones += [int(arg)]
        elif opt == '-k':
            # Not supported - ignore with warning
            print("Warning: -k (manual mode) not supported in Tado Local API")
        elif opt == '-x':
            # Not supported - ignore with warning
            print("Warning: -x (timed overrides) not supported in Tado Local API")
        elif opt == '-l':
            do_list = True
        elif opt == '-t':
            set_temp = float(arg)
            if set_temp < 0:
                set_temp = 0
        elif opt == '-r':
            set_temp = 0

    # If no zones specified, get all zones
    if (do_list or set_temp is not None) and not zones:
        if not zone_info:
            zone_info = get_zones()
        for z in zone_info:
            zones += [int(z['zone_id'])]

    # Execute commands
    if set_temp is not None:
        set_temperature(zones, set_temp, None)

    if do_list:
        show_list(verbose, zones)


if __name__ == "__main__":
    main(sys.argv[1:])
