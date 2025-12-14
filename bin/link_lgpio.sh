#!/bin/bash
#
# Helper script to symlink system lgpio into virtual environment
# Works with both venv and conda environments
#

set -e

echo "LightShowPi Neo - lgpio Symlink Helper"
echo "======================================="
echo ""

# Check if we're in a virtual environment
if [ -z "$VIRTUAL_ENV" ] && [ -z "$CONDA_PREFIX" ]; then
    echo "ERROR: No virtual environment detected!"
    echo ""
    echo "Please activate your environment first:"
    echo "  venv:  source venv/bin/activate"
    echo "  conda: conda activate lightshowpi-neo"
    exit 1
fi

# Determine environment type and site-packages location
if [ -n "$CONDA_PREFIX" ]; then
    ENV_TYPE="conda"
    ENV_NAME="$CONDA_DEFAULT_ENV"
    SITE_PACKAGES=$(python -c "import site; print([p for p in site.getsitepackages() if 'site-packages' in p][0])")
elif [ -n "$VIRTUAL_ENV" ]; then
    ENV_TYPE="venv"
    ENV_NAME=$(basename "$VIRTUAL_ENV")
    SITE_PACKAGES=$(python -c "import site; print([p for p in site.getsitepackages() if 'site-packages' in p][0])")
fi

echo "Environment: $ENV_TYPE ($ENV_NAME)"
echo "Site-packages: $SITE_PACKAGES"
echo ""

# Check Python version compatibility
SYSTEM_PY_VERSION=$(python3 --version | awk '{print $2}' | cut -d. -f1,2)
VENV_PY_VERSION=$(python --version | awk '{print $2}' | cut -d. -f1,2)

echo "Python versions:"
echo "  System: $SYSTEM_PY_VERSION"
echo "  Virtual env: $VENV_PY_VERSION"

if [ "$SYSTEM_PY_VERSION" != "$VENV_PY_VERSION" ]; then
    echo ""
    echo "⚠️  WARNING: Python version mismatch detected!"
    echo "   System Python ($SYSTEM_PY_VERSION) != Environment Python ($VENV_PY_VERSION)"
    echo ""
    echo "   Symlinking may not work because lgpio is a C extension."
    echo "   If the import test fails, use the pip method instead:"
    echo ""
    echo "   sudo apt-get install liblgpio-dev liblgpio1"
    echo "   LDFLAGS=\"-L/usr/lib/aarch64-linux-gnu -L/usr/lib\" \\"
    echo "   CFLAGS=\"-I/usr/include\" \\"
    echo "   pip install lgpio"
    echo ""
    read -p "Continue with symlink anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi
fi
echo ""

# Find system lgpio files
# Check both version-specific and version-independent locations
# Include both lgpio* and _lgpio* (C extension has underscore prefix)
# Use -maxdepth 1 to avoid finding gpiozero/pins/lgpio.py
SYSTEM_LGPIO=$(find /usr/lib/python3/dist-packages /usr/lib/python3.*/dist-packages -maxdepth 1 \( -name "lgpio*" -o -name "_lgpio*" \) 2>/dev/null | grep -E "(lgpio\.py$|_lgpio.*\.so$|lgpio.*\.egg-info$)" || true)

if [ -z "$SYSTEM_LGPIO" ]; then
    echo "ERROR: System lgpio not found!"
    echo ""
    echo "Searched locations:"
    echo "  /usr/lib/python3/dist-packages"
    echo "  /usr/lib/python3.*/dist-packages"
    echo ""
    echo "Please install lgpio first:"
    echo "  sudo apt-get update"
    echo "  sudo apt-get install -y python3-lgpio"
    echo ""
    echo "Debug: Check if lgpio is installed elsewhere:"
    echo "  python3 -c 'import lgpio; print(lgpio.__file__)'"
    exit 1
fi

echo "Found system lgpio files:"
echo "$SYSTEM_LGPIO" | while read file; do
    echo "  - $(basename $file)"
done
echo ""

# Create symlinks
echo "Creating symlinks..."
LINKED=0
SKIPPED=0

echo "$SYSTEM_LGPIO" | while read file; do
    TARGET="$SITE_PACKAGES/$(basename $file)"
    if [ -e "$TARGET" ]; then
        echo "  SKIP: $(basename $file) (already exists)"
        SKIPPED=$((SKIPPED + 1))
    else
        ln -s "$file" "$TARGET"
        echo "  LINK: $(basename $file)"
        LINKED=$((LINKED + 1))
    fi
done

echo ""
echo "Verifying lgpio import..."
if python -c "import lgpio" 2>&1; then
    VERSION=$(python -c "import lgpio; print(lgpio.__version__)" 2>/dev/null || echo "unknown")
    LOCATION=$(python -c "import lgpio; print(lgpio.__file__)" 2>/dev/null)

    echo "  SUCCESS: lgpio imported successfully"
    echo ""
    echo "✓ lgpio is now available in your $ENV_TYPE environment!"
    echo "  Version: $VERSION"
    echo "  Location: $LOCATION"
else
    echo "  ERROR: lgpio import failed"
    echo ""
    echo "Try manually:"
    echo "  python -c 'import lgpio'"
    exit 1
fi
