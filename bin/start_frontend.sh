#!/bin/bash
# LightShowPi Neo - Frontend Startup Script
#
# This script starts the Vite dev server for the React frontend.
# Note: Frontend does NOT need sudo (it just proxies to the API)

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
WEB_DIR="$PROJECT_DIR/web"

echo "Starting LightShowPi Neo Frontend..."
echo "Web directory: $WEB_DIR"
echo ""

# Check if web directory exists
if [ ! -d "$WEB_DIR" ]; then
    echo "ERROR: Web directory not found at $WEB_DIR"
    exit 1
fi

cd "$WEB_DIR"

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo "Installing npm dependencies (first time only)..."
    npm install
    echo ""
fi

# Start Vite dev server
echo "Starting Vite dev server on http://0.0.0.0:3000"
echo "Press Ctrl+C to stop"
echo ""
npm run dev
