#!/bin/bash
# LightShowPi Neo - Button Manager Startup Script
#
# This script starts the button manager with proper permissions
# and environment activation.

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Default mode is auto (try API, fall back to direct)
MODE="${1:-auto}"
LOG_LEVEL="${2:-INFO}"

# Validate mode
if [[ ! "$MODE" =~ ^(api|direct|auto)$ ]]; then
    echo "ERROR: Invalid mode '$MODE'"
    echo "Usage: $0 [mode] [log_level]"
    echo ""
    echo "Modes:"
    echo "  api    - Require API (exit if unavailable)"
    echo "  direct - State file only (never try API)"
    echo "  auto   - Try API, fall back to direct (default)"
    echo ""
    echo "Log Levels: DEBUG, INFO, WARNING, ERROR"
    exit 1
fi

cd "$PROJECT_DIR"

echo "Starting LightShowPi Neo Button Manager..."
echo "Project directory: $PROJECT_DIR"
echo "Mode: $MODE"
echo "Log Level: $LOG_LEVEL"
echo ""

# Check if SYNCHRONIZED_LIGHTS_HOME is set
if [ -z "$SYNCHRONIZED_LIGHTS_HOME" ]; then
    echo "WARNING: SYNCHRONIZED_LIGHTS_HOME not set"
    echo "Setting to project directory: $PROJECT_DIR"
    export SYNCHRONIZED_LIGHTS_HOME="$PROJECT_DIR"
fi

# Check if conda environment exists
if ! conda env list | grep -q lightshowpi-neo; then
    echo "ERROR: Conda environment 'lightshowpi-neo' not found!"
    echo "Please create it first with: conda env create -f environment.yml"
    exit 1
fi

# Run with sudo while preserving environment variables
echo "Starting button manager (requires sudo for GPIO access)..."
echo "Press Ctrl+C to stop"
echo ""

sudo -E bash -c "source $HOME/miniconda3/bin/activate lightshowpi-neo && \
    cd $PROJECT_DIR && \
    python py/buttonmanager.py --mode $MODE --log-level $LOG_LEVEL"
