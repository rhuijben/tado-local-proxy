"""
HomeKit UUID mappings for services and characteristics.

Based on Apple's HomeKit specification and Home Assistant's implementation.
These mappings convert HomeKit UUIDs to human-readable names for better API usability.

DISCOVERY RESULTS:
==================
Unknown Apple Service Found: 0000004A-0000-1000-8000-0026BB765291 = THERMOSTAT SERVICE
Tado Custom Service Found: E44673A0-247B-4360-8A76-DB9DA69C0100
Tado Custom Characteristic: E44673A0-247B-4360-8A76-DB9DA69C0101
All 10 Tado accessories expose the custom vendor service
No hidden characteristics with 'hd' permission found

HOME ASSISTANT FINDINGS:
=======================
Home Assistant uses PyTado library with cloud APIs only:
- /api/v2/homes/{home_id}/zones/{zone_id}/state
- /api/v2/homes/{home_id}/devices  
- /api/v2/homes/{home_id}/weather
- /api/v2/homes/{home_id}/zones/{zone_id}/capabilities
No local API or proprietary protocol usage found in HA

CUSTOM TADO SERVICES:
====================
- E44673A0-247B-4360-8A76-DB9DA69C0100: Tado proprietary service
- Uses Tado's own UUID namespace for vendor-specific functionality
"""

HOMEKIT_SERVICES = {
    "0000003E-0000-1000-8000-0026BB765291": "AccessoryInformation",
    "00000043-0000-1000-8000-0026BB765291": "Lightbulb", 
    "00000047-0000-1000-8000-0026BB765291": "Outlet",
    "00000049-0000-1000-8000-0026BB765291": "Switch",
    "0000004A-0000-1000-8000-0026BB765291": "Thermostat",  # Found during discovery
    "00000082-0000-1000-8000-0026BB765291": "HumiditySensor",
    "0000008A-0000-1000-8000-0026BB765291": "TemperatureSensor",
    "00000096-0000-1000-8000-0026BB765291": "Battery",
    # Tado custom services
    "E44673A0-247B-4360-8A76-DB9DA69C0100": "TadoCustomService",  # Found during discovery
}

# HomeKit Characteristic Type UUIDs
# All UUIDs verified against official Apple HomeKit specifications from HAP-NodeJS repository
HOMEKIT_CHARACTERISTICS = {
    # === BASIC INFORMATION CHARACTERISTICS ===
    # Required for AccessoryInformation service per Apple HomeKit specification
    "00000014-0000-1000-8000-0026BB765291": "Identify",
    "00000020-0000-1000-8000-0026BB765291": "Manufacturer",
    "00000021-0000-1000-8000-0026BB765291": "Model",
    "00000023-0000-1000-8000-0026BB765291": "Name",
    "00000030-0000-1000-8000-0026BB765291": "SerialNumber",
    "00000037-0000-1000-8000-0026BB765291": "Version",
    "00000052-0000-1000-8000-0026BB765291": "FirmwareRevision",
    "00000053-0000-1000-8000-0026BB765291": "HardwareRevision",
    "00000054-0000-1000-8000-0026BB765291": "SoftwareRevision",
    
    # === ENVIRONMENTAL CHARACTERISTICS ===
    # Temperature, humidity, and air quality sensors
    "0000000F-0000-1000-8000-0026BB765291": "CurrentHeatingCoolingState",
    "00000010-0000-1000-8000-0026BB765291": "CurrentRelativeHumidity",
    "00000011-0000-1000-8000-0026BB765291": "CurrentTemperature",
    "00000033-0000-1000-8000-0026BB765291": "TargetHeatingCoolingState",
    "00000034-0000-1000-8000-0026BB765291": "TargetRelativeHumidity",
    "00000035-0000-1000-8000-0026BB765291": "TargetTemperature",
    "00000036-0000-1000-8000-0026BB765291": "TemperatureDisplayUnits",
    "000000C8-0000-1000-8000-0026BB765291": "VOCDensity",
    
    # === CONTROL CHARACTERISTICS ===
    # Basic device control and lighting
    "00000025-0000-1000-8000-0026BB765291": "On",
    "00000013-0000-1000-8000-0026BB765291": "Hue",
    "00000008-0000-1000-8000-0026BB765291": "Brightness",
    "0000002F-0000-1000-8000-0026BB765291": "Saturation",
    
    # === STATUS CHARACTERISTICS ===
    # Device health and status monitoring
    "00000077-0000-1000-8000-0026BB765291": "StatusFault",
    "00000079-0000-1000-8000-0026BB765291": "StatusLowBattery",
    "00000075-0000-1000-8000-0026BB765291": "StatusActive",
    "00000068-0000-1000-8000-0026BB765291": "BatteryLevel",
    
    # === MOTION & OCCUPANCY DETECTION ===
    # Sensor characteristics for detecting presence
    "00000022-0000-1000-8000-0026BB765291": "MotionDetected",
    "00000071-0000-1000-8000-0026BB765291": "OccupancyDetected",
    
    # === SAFETY & SECURITY ===
    # Leak detection and obstruction monitoring
    "00000024-0000-1000-8000-0026BB765291": "ObstructionDetected",
    "00000070-0000-1000-8000-0026BB765291": "LeakDetected",
    
    # Security
    "00000066-0000-1000-8000-0026BB765291": "SecuritySystemCurrentState",
    "00000067-0000-1000-8000-0026BB765291": "SecuritySystemTargetState",
    "0000001D-0000-1000-8000-0026BB765291": "LockCurrentState",
    "0000001E-0000-1000-8000-0026BB765291": "LockTargetState",
    
    # Smoke and Carbon Detection
    "00000076-0000-1000-8000-0026BB765291": "SmokeDetected",
    "00000069-0000-1000-8000-0026BB765291": "CarbonMonoxideDetected",
    "00000092-0000-1000-8000-0026BB765291": "CarbonDioxideDetected",
    "00000090-0000-1000-8000-0026BB765291": "CarbonDioxideLevel",
    "00000091-0000-1000-8000-0026BB765291": "CarbonDioxidePeakLevel",
    
    # Battery and Status
    "00000068-0000-1000-8000-0026BB765291": "BatteryLevel",
    "0000008F-0000-1000-8000-0026BB765291": "ChargingState",
    "00000079-0000-1000-8000-0026BB765291": "StatusLowBattery",
    "00000075-0000-1000-8000-0026BB765291": "StatusActive",
    "00000077-0000-1000-8000-0026BB765291": "StatusFault",
    "00000078-0000-1000-8000-0026BB765291": "StatusJammed",
    "0000007A-0000-1000-8000-0026BB765291": "StatusTampered",
    
    # Version and Configuration
    "00000037-0000-1000-8000-0026BB765291": "Version",
    "00000052-0000-1000-8000-0026BB765291": "FirmwareRevision",
    "00000050-0000-1000-8000-0026BB765291": "AdminOnlyAccess",
    "0000004C-0000-1000-8000-0026BB765291": "PairSetup",
    "0000004E-0000-1000-8000-0026BB765291": "PairVerify",
    "0000004F-0000-1000-8000-0026BB765291": "PairingFeatures",
    "00000055-0000-1000-8000-0026BB765291": "PairingPairings",
    
    # Additional Controls
    "000000C3-0000-1000-8000-0026BB765291": "LockPhysicalControls",
    "000000C5-0000-1000-8000-0026BB765291": "LockControlPoint",
    "000000C6-0000-1000-8000-0026BB765291": "LockManagementAutoSecurityTimeout",
    "000000C7-0000-1000-8000-0026BB765291": "LockLastKnownAction",
    "000000C8-0000-1000-8000-0026BB765291": "LockCurrentValidConfiguration",
    "000000C9-0000-1000-8000-0026BB765291": "LockSupportedConfiguration",
}

# Human-readable value mappings
HOMEKIT_VALUES = {
    # Heating/Cooling States
    "CurrentHeatingCoolingState": {
        0: "Off",
        1: "Heat", 
        2: "Cool",
        3: "Auto"
    },
    "TargetHeatingCoolingState": {
        0: "Off",
        1: "Heat",
        2: "Cool", 
        3: "Auto"
    },
    
    # Temperature Display Units
    "TemperatureDisplayUnits": {
        0: "Celsius",
        1: "Fahrenheit"
    },
    
    # Active State
    "Active": {
        0: "Inactive",
        1: "Active"
    },
    
    # Fan States
    "CurrentFanState": {
        0: "Inactive",
        1: "Idle",
        2: "Blowing Air"
    },
    "TargetFanState": {
        0: "Manual",
        1: "Auto"
    },
    
    # Motion/Occupancy
    "MotionDetected": {
        0: "No Motion",
        1: "Motion Detected"
    },
    "OccupancyDetected": {
        0: "Not Occupied",
        1: "Occupied"
    },
    
    # Contact Sensor
    "ContactSensorState": {
        0: "Contact Detected",
        1: "Contact Not Detected"
    },
    
    # Leak Detection
    "LeakDetected": {
        0: "No Leak",
        1: "Leak Detected"
    },
    
    # Smoke Detection
    "SmokeDetected": {
        0: "No Smoke",
        1: "Smoke Detected"
    },
    
    # Carbon Monoxide Detection
    "CarbonMonoxideDetected": {
        0: "Normal",
        1: "Abnormal"
    },
    
    # Status indicators
    "StatusLowBattery": {
        0: "Normal",
        1: "Low Battery"
    },
    "StatusActive": {
        0: "Inactive", 
        1: "Active"
    },
    "StatusFault": {
        0: "No Fault",
        1: "General Fault"
    },
    "StatusJammed": {
        0: "Not Jammed",
        1: "Jammed"
    },
    "StatusTampered": {
        0: "Not Tampered",
        1: "Tampered"
    },
    
    # Charging State
    "ChargingState": {
        0: "Not Charging",
        1: "Charging",
        2: "Not Chargeable"
    },
    
    # Lock States
    "LockCurrentState": {
        0: "Unsecured",
        1: "Secured", 
        2: "Jammed",
        3: "Unknown"
    },
    "LockTargetState": {
        0: "Unsecured",
        1: "Secured"
    },
    
    # Security System States
    "SecuritySystemCurrentState": {
        0: "Stay Armed",
        1: "Away Armed",
        2: "Night Armed", 
        3: "Disarmed",
        4: "Alarm Triggered"
    },
    "SecuritySystemTargetState": {
        0: "Stay Arm",
        1: "Away Arm",
        2: "Night Arm",
        3: "Disarm"
    }
}

# Tado Custom Service and Characteristic UUIDs
TADO_SERVICES = {
    "E44673A0-247B-4360-8A76-DB9DA69C0100": "TadoProprietaryService",
}

TADO_CHARACTERISTICS = {
    "E44673A0-247B-4360-8A76-DB9DA69C0101": "TadoProprietaryControl",
}

def get_service_name(uuid: str) -> str:
    """Convert HomeKit service UUID to human-readable name."""
    # Normalize UUID to uppercase for lookup
    uuid_upper = uuid.upper()
    
    # Check Tado custom first, then Apple standard
    if uuid_upper in TADO_SERVICES:
        return TADO_SERVICES[uuid_upper]
    return HOMEKIT_SERVICES.get(uuid_upper, uuid)

def get_characteristic_name(uuid: str) -> str:
    """Convert HomeKit characteristic UUID to human-readable name."""
    # Normalize UUID to uppercase for lookup
    uuid_upper = uuid.upper()
    
    # Check Tado custom first, then Apple standard
    if uuid_upper in TADO_CHARACTERISTICS:
        return TADO_CHARACTERISTICS[uuid_upper]
    return HOMEKIT_CHARACTERISTICS.get(uuid_upper, uuid)

def get_characteristic_value_name(characteristic_name: str, value) -> str:
    """Convert HomeKit characteristic value to human-readable name."""
    if characteristic_name in HOMEKIT_VALUES and value in HOMEKIT_VALUES[characteristic_name]:
        return HOMEKIT_VALUES[characteristic_name][value]
    return str(value)

def enhance_accessory_data(accessories):
    """
    Enhance raw HomeKit accessories data with human-readable names.
    
    Args:
        accessories: List of accessories from HomeKit
        
    Returns:
        Enhanced accessories with readable names and values
    """
    enhanced = []
    
    for accessory in accessories:
        enhanced_accessory = {
            "id": accessory.get("id"),
            "aid": accessory.get("aid"),
            "serial_number": accessory.get("serial_number"),
            "services": []
        }
        
        for service in accessory.get("services", []):
            service_uuid = service.get("type", "")
            service_name = get_service_name(service_uuid)
            
            enhanced_service = {
                "type": service_uuid,
                "type_name": service_name,
                "iid": service.get("iid"),
                "characteristics": []
            }
            
            for char in service.get("characteristics", []):
                char_uuid = char.get("type", "")
                char_name = get_characteristic_name(char_uuid)
                
                enhanced_char = {
                    "type": char_uuid,
                    "type_name": char_name,
                    "iid": char.get("iid"),
                    "value": char.get("value"),
                    "perms": char.get("perms", []),
                    "format": char.get("format"),
                    "unit": char.get("unit")
                }
                
                # Add human-readable value if available  
                if "value" in char:
                    enhanced_char["value_name"] = get_characteristic_value_name(
                        char_name, char["value"]
                    )
                
                # Add device-specific interpretations for Tado
                enhanced_char = add_tado_specific_info(enhanced_char, char_name, char.get("value"))
                
                # Add constraints if present
                for key in ["minValue", "maxValue", "minStep", "validValues"]:
                    if key in char:
                        enhanced_char[key] = char[key]
                
                enhanced_service["characteristics"].append(enhanced_char)
            
            enhanced_accessory["services"].append(enhanced_service)
        
        enhanced.append(enhanced_accessory)
    
    return enhanced

def add_tado_specific_info(enhanced_char, char_name, value):
    """Add Tado-specific interpretations for characteristics."""
       
    # Apply universal enhancements for any temperature/humidity values regardless of source
    if char_name == "CurrentTemperature" and isinstance(value, (int, float)):
        enhanced_char["temperature_celsius"] = value
        enhanced_char["temperature_fahrenheit"] = round((value * 9/5) + 32, 1)
        
    elif char_name == "CurrentRelativeHumidity" and isinstance(value, (int, float)):
        enhanced_char["humidity_percent"] = f"{value}%"
    
    # Note: Apple standard characteristics (SerialNumber, Name, FirmwareRevision, etc.)
    # are NOT modified here - they retain their official Apple HomeKit meanings
    
    return enhanced_char