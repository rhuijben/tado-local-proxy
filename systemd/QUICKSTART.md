# Quick Start - System Service Installation

## One-Command Install (Copy & Paste)

### Ubuntu/Debian/Raspberry Pi OS

```bash
# Install and configure as system service
sudo pip3 install tado-local && \
sudo useradd --system --no-create-home --shell /sbin/nologin tado-local && \
sudo cp systemd/tado-local.service /etc/systemd/system/ && \
sudo sed -i 's/192.168.1.100/YOUR_BRIDGE_IP/' /etc/systemd/system/tado-local.service && \
sudo systemctl daemon-reload && \
sudo systemctl enable tado-local && \
sudo systemctl start tado-local && \
sudo systemctl status tado-local
```

**Replace `YOUR_BRIDGE_IP` with your actual Tado bridge IP address!**

---

### Alpine Linux

```bash
# Install and configure as system service
sudo apk add py3-pip && \
sudo pip install tado-local && \
sudo adduser -D -H -s /sbin/nologin tado-local && \
sudo mkdir -p /var/lib/tado-local /run/tado-local && \
sudo chown tado-local:tado-local /var/lib/tado-local /run/tado-local && \
sudo cp systemd/tado-local.openrc /etc/init.d/tado-local && \
sudo cp systemd/tado-local.conf.openrc /etc/conf.d/tado-local && \
sudo chmod +x /etc/init.d/tado-local && \
sudo sed -i 's/192.168.1.100/YOUR_BRIDGE_IP/' /etc/conf.d/tado-local && \
sudo rc-update add tado-local default && \
sudo rc-service tado-local start
```

**Replace `YOUR_BRIDGE_IP` with your actual Tado bridge IP address!**

---

### FreeBSD

```bash
# Install and configure as system service
sudo pip install tado-local && \
sudo pw useradd tado-local -d /var/db/tado-local -s /usr/sbin/nologin -c "Tado Local Service" && \
sudo mkdir -p /var/db/tado-local /var/run/tado-local && \
sudo chown tado-local:tado-local /var/db/tado-local /var/run/tado-local && \
sudo cp systemd/tado-local.freebsd /usr/local/etc/rc.d/tado_local && \
sudo chmod +x /usr/local/etc/rc.d/tado_local && \
echo 'tado_local_enable="YES"' | sudo tee -a /etc/rc.conf && \
echo 'tado_local_bridge_ip="192.168.1.100"' | sudo tee -a /etc/rc.conf && \
sudo service tado_local start
```

**Edit `/etc/rc.conf` and change `192.168.1.100` to your actual bridge IP!**

---

## Check Service Status

```bash
# systemd (Ubuntu/Debian/Raspberry Pi)
sudo systemctl status tado-local
sudo journalctl -u tado-local -f

# FreeBSD
sudo service tado_local status
tail -f /var/log/messages

# Alpine/OpenRC
sudo rc-service tado-local status
tail -f /var/log/messages
```

---

## Access the API

Once running, visit:
- **http://your-server:4407/docs** - API documentation
- **http://your-server:4407/status** - Service status
- **http://your-server:4407/zones** - Tado zones

---

## Common Management Commands

### Start/Stop/Restart

```bash
# systemd
sudo systemctl start|stop|restart tado-local

# FreeBSD
sudo service tado_local start|stop|restart

# OpenRC
sudo rc-service tado-local start|stop|restart
```

### View Logs

```bash
# systemd
sudo journalctl -u tado-local -f

# FreeBSD/OpenRC
tail -f /var/log/messages | grep tado-local
```

### Enable/Disable Auto-start

```bash
# systemd
sudo systemctl enable|disable tado-local

# FreeBSD
sudo sysrc tado_local_enable="YES|NO"

# OpenRC
sudo rc-update add|del tado-local default
```

---

See [README.md](README.md) for detailed installation instructions and troubleshooting.
