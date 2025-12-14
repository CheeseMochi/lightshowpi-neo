#!/bin/bash
#
# LightShowPi Neo - System Dependencies Installer
#
# This script installs ONLY the system-level dependencies required for LightShowPi Neo.
# For complete installation instructions, see README.md
#
# This script must be run as root (it will use sudo if needed)

INSTALL_DIR="$( cd $(dirname $0) ; pwd -P )"

# Set up file-based logging
exec 1> >(tee install.log)

# Root check
if [ "$EUID" -ne 0 ]; then
    echo 'Install script requires root privileges!'
    if [ -x /usr/bin/sudo ]; then
        echo 'Switching now, enter the password for "'$USER'", if prompted.'
        sudo su -c "$0 $*"
    else
        echo 'Switching now, enter the password for "root"!'
        su root -c "$0 $*"
    fi
    exit $?
fi

# Wrapper for informational logging
log() {
    echo -ne "\e[1;34mlightshowpi-neo \e[m" >&2
    echo -e "[`date`] $@"
}

# Checks the return value of the last command to run
verify() {
    if [ $? -ne 0 ]; then
        echo "Encountered a fatal error: $@"
        exit 1
    fi
}

log "LightShowPi Neo - System Dependencies Installer"
log "================================================"
echo ""

# Check for supported Raspberry Pi version (3, 4, or 5)
log "Checking for supported Raspberry Pi hardware..."
if [ -f /proc/cpuinfo ]; then
    # Try new format first (Model)
    MODEL=$(grep -E "^Model" /proc/cpuinfo)

    if echo "$MODEL" | grep -qi "Raspberry Pi 5"; then
        log "Detected Raspberry Pi 5 - supported!"
    elif echo "$MODEL" | grep -qi "Raspberry Pi 4"; then
        log "Detected Raspberry Pi 4 - supported!"
    elif echo "$MODEL" | grep -qi "Raspberry Pi 3"; then
        log "Detected Raspberry Pi 3 - supported!"
    else
        # Try old format (Hardware)
        HARDWARE=$(grep -E "^Hardware" /proc/cpuinfo | awk '{print $3}')
        case $HARDWARE in
            BCM2711)
                log "Detected Raspberry Pi 4 (via Hardware field) - supported!"
                ;;
            BCM2712)
                log "Detected Raspberry Pi 5 (via Hardware field) - supported!"
                ;;
            BCM2837)
                log "Detected Raspberry Pi 3 (via Hardware field) - supported!"
                ;;
            BCM2835|BCM2836)
                echo "ERROR: This version of LightShowPi requires Raspberry Pi 3, 4, or 5"
                echo "Detected: Pi 1, 2, or Zero (not supported)"
                echo "Please upgrade to a Raspberry Pi 3 or newer"
                exit 1
                ;;
            *)
                echo "WARNING: Could not definitively detect Pi model"
                echo "Hardware: $HARDWARE, Model: $MODEL"
                echo "Proceeding anyway, but this may not work on unsupported hardware"
                ;;
        esac
    fi
else
    echo "WARNING: Could not detect hardware - /proc/cpuinfo not found"
    echo "Proceeding anyway, but this may not work on unsupported hardware"
fi

echo ""
log "Installing system dependencies..."
log "This will install: python3-dev, python3-lgpio, libasound2-dev, alsa-lib, git"
echo ""

# Update package list
apt-get update
verify "apt-get update failed"

# Install system dependencies
log "Installing python3-lgpio (GPIO library for Pi 3/4/5)..."
apt-get install -y python3-lgpio
verify "Installation of python3-lgpio failed"

log "Installing libasound2-dev (ALSA development files)..."
apt-get install -y libasound2-dev
verify "Installation of libasound2-dev failed"

# Install ALSA lib packages for conda compatibility
log "Installing ALSA library packages for conda compatibility..."
apt-get install -y libasound2 alsa-utils
verify "Installation of ALSA packages failed"

echo ""
log "System dependencies installed successfully!"
echo ""
echo "========================================================================"
echo "  NEXT STEPS - Complete Installation"
echo "========================================================================"
echo ""
echo "System dependencies are now installed."
echo "To complete the installation, follow these steps from README.md:"
echo ""
echo "1. Install Miniconda (if not already installed):"
echo "   wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh"
echo "   bash Miniconda3-latest-Linux-aarch64.sh"
echo ""
echo "2. Create conda environment:"
echo "   conda env create -f environment.yml"
echo "   conda activate lightshowpi-neo"
echo ""
echo "3. Link system lgpio to conda environment:"
echo "   ./bin/link_lgpio.sh"
echo ""
echo "4. Set environment variable (add to ~/.bashrc):"
echo "   export SYNCHRONIZED_LIGHTS_HOME=$INSTALL_DIR"
echo ""
echo "5. Configure your setup:"
echo "   cp config/defaults.cfg config/overrides.cfg"
echo "   nano config/overrides.cfg"
echo ""
echo "6. Test your installation:"
echo "   pytest -v"
echo ""
echo "For detailed instructions, see README.md"
echo "For API/Web UI setup, see RELEASE_NOTES.md"
echo ""
echo "========================================================================"
exit 0
