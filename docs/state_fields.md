# TadoLocal API State Fields

This document describes the standard state fields returned by the TadoLocal API for zones and devices. These definitions are used by both the Domoticz plugin and other integrations.

## Zone State Fields

- **cur_temp_c**: Current measured temperature in Celsius (float)
- **hum_perc**: Current measured relative humidity in percent (int)
- **target_temp_c**: Target temperature setpoint in Celsius (float)
- **mode**: Operating mode (int)
    - 0 = Off
    - 1 = Manual
    - 2 = Auto/Schedule
- **cur_heating**: Current heating/cooling state (int)
    - 0 = Off
    - 1 = Heat (zone is actively heating)
    - 2 = Cool (zone is actively cooling)
- **battery_low**: Battery status (bool)
    - true = Battery low
    - false = Battery OK

## Device State Fields

- **cur_temp_c**: Current measured temperature in Celsius (float)
- **hum_perc**: Current measured relative humidity in percent (int)
- **battery_low**: Battery status (bool)
    - true = Battery low
    - false = Battery OK

## Example JSON

```json
{
  "zone_id": 1,
  "zone_name": "Living Room",
  "state": {
    "cur_temp_c": 21.5,
    "hum_perc": 45,
    "target_temp_c": 22.0,
    "mode": 1,
    "cur_heating": 1,
    "battery_low": false
  }
}
```

## Notes
- The `cur_heating` field is used for the heating status selector in Domoticz and other integrations. Its values are:
    - 0: Off
    - 1: Heat
    - 2: Cool
- The meaning of "Cool" depends on the HomeKit accessory and Tado zone configuration. Most heating-only zones will not report "Cool".
- Battery status is always reported from the leader thermostat in each zone.

---
For further details, see the main project README or API documentation.
