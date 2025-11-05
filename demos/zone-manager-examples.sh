#!/bin/bash
# zone-manager.py - Usage Examples
# 
# Replacement for old td-mgr script that used Tado cloud API
# This version uses the local TadoLocal API for faster, unlimited control

# Configuration
ZONE_MGR="python3 /path/to/zone-manager.py"

# Optional: Set API key if authentication is enabled
# export TADO_API_KEYS="your-api-key-here"

# Optional: Set custom API URL
# export TADO_LOCAL_API="http://localhost:4407"

# Example 1: List all zones with status
$ZONE_MGR --list

# Example 2: Set specific zone to temperature by name
$ZONE_MGR --zone Son --temperature 19

# Example 3: Set zone by ID
$ZONE_MGR -z 1 -t 21

# Example 4: Turn off multiple zones
$ZONE_MGR --zone Son --zone Daughter --disable

# Example 5: Limit temperature (like old td-mgr -t 8)
# Only lowers temperature if zone is ON and above the limit
# Does nothing if zone is OFF or already below limit
$ZONE_MGR --zone Son --limit-temp 8

# Example 6: Limit with quiet mode (for cron jobs)
$ZONE_MGR --zone Son --limit 8 --quiet

# Example 7: Reset to schedule (re-enable at current target)
$ZONE_MGR --zone Son --reset

# ============================================================
# Cron Job Examples (equivalent to old athome script logic)
# ============================================================

# Check if user is home, otherwise limit heating to 8°C
# This replaces: $TADO -z J -x 720 -t 8

# Option 1: Using external "athome" check script
ATHOME="/home/user/bin/check-athome"

$ATHOME Son > /dev/null || $ZONE_MGR --zone Son --limit 8 --quiet
$ATHOME Daughter > /dev/null || $ZONE_MGR --zone Daughter --limit 8 --quiet

# Option 2: Simple cron job (run every hour)
# Limit Son's room to 8°C during work hours (9-17) on weekdays
# Add to crontab:
# 0 9-17 * * 1-5 /usr/bin/python3 /path/to/zone-manager.py --zone Son --limit 8 --quiet

# Option 3: Night mode - limit all zones to 16°C at night
# Add to crontab:
# 0 23 * * * /usr/bin/python3 /path/to/zone-manager.py --zone Son --zone Daughter --limit 16 --quiet

# ============================================================
# Systemd Timer Example (alternative to cron)
# ============================================================

# File: /etc/systemd/system/tado-limit-son.service
# [Unit]
# Description=Limit Tado temperature for Son's room
# 
# [Service]
# Type=oneshot
# Environment="TADO_API_KEYS=your-api-key-here"
# ExecStart=/usr/bin/python3 /path/to/zone-manager.py --zone Son --limit 8 --quiet
# User=youruser

# File: /etc/systemd/system/tado-limit-son.timer
# [Unit]
# Description=Run Tado limit check every hour
# 
# [Timer]
# OnCalendar=hourly
# Persistent=true
# 
# [Install]
# WantedBy=timers.target

# Enable: sudo systemctl enable --now tado-limit-son.timer
