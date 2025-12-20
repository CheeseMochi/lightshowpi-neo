# LightShowPi Neo - Systemd Service Units

Systemd service unit files for auto-starting LightShowPi Neo components on boot.

## Available Services

- **lightshowpi-api.service** - FastAPI backend server
- **lightshowpi-buttons.service** - Physical button manager (optional)

## Installation

### Prerequisites

1. **Update paths in service files** if your installation is not in `/home/pi/lightshowpi`:
   - Edit service files and replace `/home/pi/lightshowpi` with your path
   - Replace `/home/pi/miniconda3` with your conda installation path
   - Update `User=` if not using the `pi` user

2. **Ensure conda environment exists:**
   ```bash
   conda env list | grep lightshowpi-neo
   ```

3. **Set up environment variable** (if not already done):
   ```bash
   echo "export SYNCHRONIZED_LIGHTS_HOME=/home/pi/lightshowpi" >> ~/.bashrc
   source ~/.bashrc
   ```

### Install Services

1. **Copy service files to systemd directory:**
   ```bash
   sudo cp systemd/*.service /etc/systemd/system/
   ```

2. **Reload systemd daemon:**
   ```bash
   sudo systemctl daemon-reload
   ```

3. **Enable services to start on boot:**
   ```bash
   # API server (required)
   sudo systemctl enable lightshowpi-api.service

   # Button manager (optional - only if you have physical buttons)
   sudo systemctl enable lightshowpi-buttons.service
   ```

## Managing Services

### Start Services

```bash
# Start API server
sudo systemctl start lightshowpi-api.service

# Start button manager
sudo systemctl start lightshowpi-buttons.service
```

### Stop Services

```bash
# Stop API server
sudo systemctl stop lightshowpi-api.service

# Stop button manager
sudo systemctl stop lightshowpi-buttons.service
```

### Check Status

```bash
# Check API status
sudo systemctl status lightshowpi-api.service

# Check button manager status
sudo systemctl status lightshowpi-buttons.service
```

### View Logs

```bash
# API logs (real-time)
sudo journalctl -u lightshowpi-api.service -f

# Button manager logs (real-time)
sudo journalctl -u lightshowpi-buttons.service -f

# Last 50 lines
sudo journalctl -u lightshowpi-api.service -n 50

# Logs since boot
sudo journalctl -u lightshowpi-api.service -b

# Logs for specific time range
sudo journalctl -u lightshowpi-api.service --since "2025-12-18 14:00" --until "2025-12-18 15:00"
```

### Restart Services

```bash
# Restart after code changes
sudo systemctl restart lightshowpi-api.service
sudo systemctl restart lightshowpi-buttons.service
```

### Disable Services

```bash
# Disable auto-start on boot
sudo systemctl disable lightshowpi-api.service
sudo systemctl disable lightshowpi-buttons.service
```

## Service Configuration

### API Server

- **User:** `pi` (or configured user)
- **Port:** 5000 (configured in api/main.py)
- **Logs:** `journalctl -u lightshowpi-api.service`
- **Auto-restart:** Yes (on failure, 10s delay)

### Button Manager

- **User:** `root` (requires GPIO access)
- **Mode:** `auto` (try API, fall back to direct mode)
- **Log Level:** `INFO` (change to `DEBUG` in service file for troubleshooting)
- **Logs:** `journalctl -u lightshowpi-buttons.service`
- **Auto-restart:** Yes (on failure, 10s delay)
- **GPIO Cleanup:** Automatic on stop

## Customization

### Change Button Manager Mode

Edit `/etc/systemd/system/lightshowpi-buttons.service`:

```ini
# API mode only (exit if API unavailable)
ExecStart=/home/pi/miniconda3/envs/lightshowpi-neo/bin/python py/buttonmanager.py --mode api --log-level INFO

# Direct mode only (never try API)
ExecStart=/home/pi/miniconda3/envs/lightshowpi-neo/bin/python py/buttonmanager.py --mode direct --log-level INFO

# Auto mode (default - try API, fall back to direct)
ExecStart=/home/pi/miniconda3/envs/lightshowpi-neo/bin/python py/buttonmanager.py --mode auto --log-level INFO
```

After editing, reload and restart:
```bash
sudo systemctl daemon-reload
sudo systemctl restart lightshowpi-buttons.service
```

### Change Log Level

Edit service file and change `--log-level INFO` to:
- `DEBUG` - Verbose logging for troubleshooting
- `INFO` - Normal operation (default)
- `WARNING` - Only warnings and errors
- `ERROR` - Only errors

### Add Email Alerts on Failure

Install mail support:
```bash
sudo apt-get install mailutils
```

Edit service file and add:
```ini
[Service]
OnFailure=status-email@%n.service
```

## Troubleshooting

### Service won't start

1. **Check service status:**
   ```bash
   sudo systemctl status lightshowpi-api.service
   ```

2. **View detailed logs:**
   ```bash
   sudo journalctl -u lightshowpi-api.service -n 100 --no-pager
   ```

3. **Verify paths in service file:**
   ```bash
   cat /etc/systemd/system/lightshowpi-api.service
   ```

4. **Test command manually:**
   ```bash
   cd /home/pi/lightshowpi
   /home/pi/miniconda3/envs/lightshowpi-neo/bin/python -m api.main
   ```

### Button manager GPIO permission denied

1. **Add user to gpio group:**
   ```bash
   sudo usermod -a -G gpio pi
   ```

2. **Or run button manager as root** (already configured in service file)

3. **Check GPIO device permissions:**
   ```bash
   ls -l /dev/gpiochip*
   ```

### API service fails to bind to port 5000

1. **Check if port is already in use:**
   ```bash
   sudo netstat -tulpn | grep :5000
   ```

2. **Kill existing process:**
   ```bash
   sudo kill -9 $(lsof -t -i:5000)
   ```

### Services start but don't work correctly

1. **Check environment variables:**
   ```bash
   sudo systemctl show lightshowpi-api.service | grep Environment
   ```

2. **Verify conda environment:**
   ```bash
   /home/pi/miniconda3/envs/lightshowpi-neo/bin/python --version
   ```

3. **Check application logs for errors:**
   ```bash
   sudo journalctl -u lightshowpi-api.service -n 100
   ```

## Security Considerations

### API Service

- Runs as non-root user (`pi`)
- `NoNewPrivileges=true` prevents privilege escalation
- `PrivateTmp=true` isolates /tmp directory
- Consider adding firewall rules to restrict API access

### Button Manager

- Runs as `root` for GPIO access
- Alternative: Add user to `gpio` group and run as non-root
- Consider using GPIO capabilities instead of full root access

## Production Recommendations

1. **Use a reverse proxy (nginx)** for the API:
   ```nginx
   server {
       listen 80;
       server_name lightshow.local;

       location /api/ {
           proxy_pass http://localhost:5000/api/;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
       }
   }
   ```

2. **Enable log rotation** to prevent disk space issues:
   ```bash
   sudo journalctl --vacuum-time=7d
   ```

3. **Set up monitoring** with systemd watchdog

4. **Configure firewall:**
   ```bash
   sudo ufw allow 5000/tcp  # API
   sudo ufw enable
   ```

5. **Regular backups** of configuration and database

## Uninstallation

```bash
# Stop services
sudo systemctl stop lightshowpi-api.service
sudo systemctl stop lightshowpi-buttons.service

# Disable services
sudo systemctl disable lightshowpi-api.service
sudo systemctl disable lightshowpi-buttons.service

# Remove service files
sudo rm /etc/systemd/system/lightshowpi-api.service
sudo rm /etc/systemd/system/lightshowpi-buttons.service

# Reload systemd
sudo systemctl daemon-reload
```
