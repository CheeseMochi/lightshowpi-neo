#!/bin/bash
# LightShowPi Neo - API Startup Script
#
# This script starts the FastAPI backend with proper permissions
# and environment activation.

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Activate conda environment and run API
cd "$PROJECT_DIR"

echo "Starting LightShowPi Neo API..."
echo "Project directory: $PROJECT_DIR"
echo ""

# Check if conda environment exists
if ! conda env list | grep -q lightshowpi-neo; then
    echo "ERROR: Conda environment 'lightshowpi-neo' not found!"
    echo "Please create it first with: conda env create -f environment.yml"
    exit 1
fi

# The API needs JWT_SECRET_KEY (and ADMIN_PASSWORD on a fresh database). Ansible
# writes both to /etc/lightshowpi/api.env; sudo drops our environment, so load
# that file inside the sudo shell. LSP_DEV=1 (passed through) allows a throwaway
# key and turns on auto-reload for development.
sudo LSP_DEV="${LSP_DEV:-}" bash -c "set -a; [ -r /etc/lightshowpi/api.env ] && . /etc/lightshowpi/api.env; set +a; source $HOME/miniconda3/bin/activate lightshowpi-neo && cd $PROJECT_DIR && python -m api.main"
