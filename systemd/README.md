# Tado Local - System Service Installation

This directory contains service scripts for running Tado Local as a system service on various operating systems.

## Best Practices

All service configurations follow these security principles:

- **Non-root user**: Runs as dedicated `tado-local` user (no root privileges)
- **Minimal permissions**: Only network access required
- **Isolated directories**: Separate state and runtime directories
- **Hardened security**: Uses systemd security features where available
- **Syslog integration**: Proper logging for system monitoring

## Supported Systems

- **systemd** (Ubuntu, Debian, Fedora, RHEL, Arch, etc.)
- **FreeBSD** (rc.d)
- **OpenRC** (Alpine Linux, Gentoo)

---

## systemd (Ubuntu, Debian, Fedora, Arch, RHEL, etc.)

### Installation

```bash
# 1. Install Tado Local
sudo pip install tado-local
# or: sudo python setup.py install

# 2. Create dedicated user (no login, no home directory)
sudo useradd --system --no-create-home --shell /sbin/nologin tado-local

# 3. Copy service file
sudo cp systemd/tado-local.service /etc/systemd/system/

# 4. Edit service file with your bridge IP
sudo nano /etc/systemd/system/tado-local.service
# Change: --bridge-ip 192.168.1.100

# 5. Reload systemd and enable service
sudo systemctl daemon-reload
sudo systemctl enable tado-local
sudo systemctl start tado-local

# 6. Check status
sudo systemctl status tado-local
sudo journalctl -u tado-local -f
```

### Configuration

Edit `/etc/systemd/system/tado-local.service` and modify the `ExecStart` line:

```ini
ExecStart=/usr/local/bin/tado-local \
    --bridge-ip YOUR_BRIDGE_IP \
    --state /var/lib/tado-local/tado-local.db \
    --port 4407 \
    --syslog /dev/log \
    --pid-file /run/tado-local/tado-local.pid
```

### Management

```bash
sudo systemctl start tado-local      # Start service
sudo systemctl stop tado-local       # Stop service
sudo systemctl restart tado-local    # Restart service
sudo systemctl status tado-local     # Check status
sudo journalctl -u tado-local -f     # View logs
```

---

## FreeBSD (rc.d)

### Installation

```bash
# 1. Install Tado Local
sudo pip install tado-local
# or: sudo python setup.py install

# 2. Create dedicated user
sudo pw useradd tado-local -d /var/db/tado-local -s /usr/sbin/nologin -c "Tado Local Service"

# 3. Create directories
sudo mkdir -p /var/db/tado-local /var/run/tado-local
sudo chown tado-local:tado-local /var/db/tado-local /var/run/tado-local

# 4. Copy rc.d script
sudo cp systemd/tado-local.freebsd /usr/local/etc/rc.d/tado_local
sudo chmod +x /usr/local/etc/rc.d/tado_local

# 5. Configure in /etc/rc.conf
echo 'tado_local_enable="YES"' | sudo tee -a /etc/rc.conf
echo 'tado_local_bridge_ip="192.168.1.100"' | sudo tee -a /etc/rc.conf

# 6. Start service
sudo service tado_local start
```

### Configuration

Add to `/etc/rc.conf`:

```sh
tado_local_enable="YES"
tado_local_bridge_ip="192.168.1.100"  # Required
tado_local_port="4407"                # Optional
tado_local_flags="--verbose"          # Optional: additional flags
```

### Management

```bash
sudo service tado_local start        # Start service
sudo service tado_local stop         # Stop service
sudo service tado_local restart      # Restart service
sudo service tado_local status       # Check status
tail -f /var/log/messages            # View logs
```

---

## OpenRC (Alpine Linux, Gentoo)

### Installation

```bash
# 1. Install Tado Local
sudo apk add py3-pip  # Alpine
sudo pip install tado-local

# 2. Create dedicated user
sudo adduser -D -H -s /sbin/nologin tado-local

# 3. Create directories
sudo mkdir -p /var/lib/tado-local /run/tado-local
sudo chown tado-local:tado-local /var/lib/tado-local /run/tado-local

# 4. Copy service script and config
sudo cp systemd/tado-local.openrc /etc/init.d/tado-local
sudo cp systemd/tado-local.conf.openrc /etc/conf.d/tado-local
sudo chmod +x /etc/init.d/tado-local

# 5. Configure bridge IP
sudo nano /etc/conf.d/tado-local
# Set: BRIDGE_IP="192.168.1.100"

# 6. Enable and start service
sudo rc-update add tado-local default
sudo rc-service tado-local start
```

### Configuration

Edit `/etc/conf.d/tado-local`:

```sh
BRIDGE_IP="192.168.1.100"  # Required
PORT="4407"                # Optional
EXTRA_ARGS=""              # Optional: additional flags
```

### Management

```bash
sudo rc-service tado-local start     # Start service
sudo rc-service tado-local stop      # Stop service
sudo rc-service tado-local restart   # Restart service
sudo rc-service tado-local status    # Check status
tail -f /var/log/messages            # View logs
```

---

## Raspberry Pi Specific Notes

Raspberry Pi OS (based on Debian) uses systemd, so follow the **systemd** instructions above.

### Performance Tips for Raspberry Pi

1. **Use a Raspberry Pi 3 or newer** for best performance
2. **SD card**: Use a good quality SD card (Class 10 or UHS-1)
3. **Network**: Wired Ethernet recommended for reliability
4. **Memory**: 512MB+ RAM sufficient

### Raspberry Pi OS Installation

```bash
# Install Python dependencies
sudo apt update
sudo apt install python3-pip python3-dev

# Install Tado Local
sudo pip3 install tado-local

# Follow systemd installation steps above
```

---

## Security Features

### systemd Security Hardening

The systemd service includes extensive security hardening:

- `NoNewPrivileges=true` - Prevents privilege escalation
- `PrivateTmp=true` - Isolated /tmp directory
- `ProtectSystem=strict` - Read-only system directories
- `ProtectHome=true` - No access to user home directories
- `RestrictAddressFamilies` - Only IPv4/IPv6/Unix sockets
- `ProtectKernelTunables=true` - No kernel modification
- `RestrictSUIDSGID=true` - No SUID/SGID execution

### File Permissions

All service configurations use:

- **User**: `tado-local` (non-root, no login shell)
- **State directory**: `/var/lib/tado-local` (mode 0750, owner: tado-local)
- **Runtime directory**: `/run/tado-local` (mode 0755, owner: tado-local)
- **Database**: Owned by `tado-local` user

---

## Troubleshooting

### Service won't start

```bash
# Check service status
sudo systemctl status tado-local  # systemd
sudo service tado_local status    # FreeBSD
sudo rc-service tado-local status # OpenRC

# Check logs
sudo journalctl -u tado-local -n 50  # systemd
tail -50 /var/log/messages           # FreeBSD/OpenRC

# Test manually
sudo -u tado-local tado-local --bridge-ip 192.168.1.100 --state /tmp/test.db
```

### Permission issues

```bash
# Fix state directory permissions
sudo chown -R tado-local:tado-local /var/lib/tado-local
sudo chmod 750 /var/lib/tado-local

# Fix runtime directory permissions
sudo chown tado-local:tado-local /run/tado-local
sudo chmod 755 /run/tado-local
```

### Can't connect to bridge

```bash
# Test network connectivity
ping YOUR_BRIDGE_IP

# Check if port is accessible
telnet YOUR_BRIDGE_IP 80

# Verify bridge IP in configuration
sudo systemctl cat tado-local  # systemd
cat /etc/rc.conf | grep tado   # FreeBSD
cat /etc/conf.d/tado-local     # OpenRC
```

---

## Uninstallation

### systemd

```bash
sudo systemctl stop tado-local
sudo systemctl disable tado-local
sudo rm /etc/systemd/system/tado-local.service
sudo systemctl daemon-reload
sudo userdel tado-local
sudo rm -rf /var/lib/tado-local
```

### FreeBSD

```bash
sudo service tado_local stop
sudo sysrc tado_local_enable=NO
sudo rm /usr/local/etc/rc.d/tado_local
sudo pw userdel tado-local
sudo rm -rf /var/db/tado-local /var/run/tado-local
```

### OpenRC

```bash
sudo rc-service tado-local stop
sudo rc-update del tado-local
sudo rm /etc/init.d/tado-local /etc/conf.d/tado-local
sudo deluser tado-local
sudo rm -rf /var/lib/tado-local /run/tado-local
```

---

## API Access

By default, the service listens on `http://0.0.0.0:4407`:

- **API Documentation**: http://YOUR_SERVER_IP:4407/docs
- **Status**: http://YOUR_SERVER_IP:4407/status
- **Zones**: http://YOUR_SERVER_IP:4407/zones
- **Live Events**: http://YOUR_SERVER_IP:4407/events

For security, consider:
1. Setting `TADO_API_KEYS` environment variable to require authentication
2. Using a reverse proxy (nginx, caddy) with HTTPS
3. Configuring firewall rules to restrict access

---

## Support

For issues or questions:
- GitHub Issues: https://github.com/AmpScm/TadoLocal/issues
- Documentation: https://github.com/AmpScm/TadoLocal
