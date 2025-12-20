#!/bin/bash
# LightShowPi Neo - Button Manager Stop Script
#
# This script stops the button manager and cleans up GPIO resources.

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "Stopping LightShowPi Neo Button Manager..."
echo ""

# Check if SYNCHRONIZED_LIGHTS_HOME is set
if [ -z "$SYNCHRONIZED_LIGHTS_HOME" ]; then
    export SYNCHRONIZED_LIGHTS_HOME="$PROJECT_DIR"
fi

# Find and kill button manager process
BUTTON_PID=$(pgrep -f "python.*buttonmanager.py")

if [ -n "$BUTTON_PID" ]; then
    echo "Found button manager process: $BUTTON_PID"
    echo "Sending SIGTERM..."
    sudo kill -SIGTERM $BUTTON_PID

    # Wait up to 5 seconds for graceful shutdown
    for i in {1..5}; do
        if ! ps -p $BUTTON_PID > /dev/null 2>&1; then
            echo "✓ Button manager stopped gracefully"
            break
        fi
        sleep 1
    done

    # Force kill if still running
    if ps -p $BUTTON_PID > /dev/null 2>&1; then
        echo "Process still running, sending SIGKILL..."
        sudo kill -9 $BUTTON_PID
        echo "✓ Button manager force stopped"
    fi
else
    echo "Button manager is not running"
fi

# Clean up GPIO resources
echo ""
echo "Cleaning up GPIO resources..."

# Check if conda environment exists
if conda env list | grep -q lightshowpi-neo; then
    sudo -E bash -c "source $HOME/miniconda3/bin/activate lightshowpi-neo && \
        cd $PROJECT_DIR && \
        python py/buttonmanager.py --cleanup"
    echo "✓ GPIO cleanup complete"
else
    echo "WARNING: Conda environment not found, skipping GPIO cleanup"
fi

echo ""
echo "Button manager stopped"
