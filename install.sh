#!/bin/bash
#
# Installation framework for lightshowPi
#
# Support for each individual distribution is 
INSTALL_DIR="$( cd $(dirname $0) ; pwd -P )"
BUILD_DIR=${INSTALL_DIR}/build_dir

# Globals populated below
BASE_DISTRO=
DISTRO=

# Globals populated by distro-specific scripts
INSTALL_COMMAND=
SYSTEM_DEPS=

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

#
# Wrapper for informational logging
# Args:
#     All arguments are written to the terminal and log file
log() {
    echo -ne "\e[1;34mlightshowpi \e[m" >&2
    echo -e "[`date`] $@"
}

#
# Checks the return value of the last command to run
# Args:
#     1 - Message to display on failure
verify() {
    if [ $? -ne 0 ]; then
        echo "Encountered a fatal error: $@"
        exit 1
    fi
}

#
# Configure installation process based on Linux distribution
install_init() {
    DISTRO=`awk -F= '$1~/^ID$/ {print $2}' /etc/os-release`
    BASE_DISTRO=`awk -F= '$1=/ID_LIKE/ {print $2}' /etc/os-release`

    all_supported="ls "

    case $DISTRO in
        archarm|raspbian)
            log Configuring installation for detected distro="'$DISTRO'"
            source $INSTALL_DIR/install-scripts/$DISTRO
            verify "Error importing configuration from install-scripts/$DISTRO"
            ;;
        *)
            log Detected unknown distribution. Please verify that "'$DISTRO'" is supported and update this script.
            log To add support for "'$DISTRO'" create a script with that name in "install-scripts"
            exit 1
            ;;
    esac

}


#
# Wrapper function to handle installation of system packages
# Args:
#     1 - Package name
pkginstall() {
    log Installing $1...
    $INSTALL_COMMAND $1
    verify "Installation of package '$1' failed"
}

# Note: pipinstall function removed - Python dependencies now managed via requirements.txt

# Prepare the build environment
install_init
rm -rf $BUILD_DIR
mkdir -p $BUILD_DIR && cd $BUILD_DIR

# Check for supported Raspberry Pi version (3, 4, or 5)
log Checking for supported Raspberry Pi hardware...
if [ -f /proc/cpuinfo ]; then
    HARDWARE=$(grep -E "^Hardware" /proc/cpuinfo | awk '{print $3}')
    case $HARDWARE in
        BCM2711)
            log Detected Raspberry Pi 4 - supported!
            ;;
        BCM2712)
            log Detected Raspberry Pi 5 - supported!
            ;;
        BCM2835|BCM2837)
            # Could be Pi 3, Pi 2, Pi 1, or Zero - check model
            MODEL=$(grep -E "^Model" /proc/cpuinfo | grep -i "Pi 3")
            if [ -n "$MODEL" ]; then
                log Detected Raspberry Pi 3 - supported!
            else
                echo "ERROR: This version of LightShowPi requires Raspberry Pi 3, 4, or 5"
                echo "Detected hardware: $HARDWARE (Pi 1, 2, or Zero - not supported)"
                echo "Please upgrade to a Raspberry Pi 3 or newer"
                exit 1
            fi
            ;;
        *)
            echo "ERROR: Unsupported or unknown hardware: $HARDWARE"
            echo "This version of LightShowPi requires Raspberry Pi 3, 4, or 5"
            exit 1
            ;;
    esac
else
    echo "WARNING: Could not detect hardware - /proc/cpuinfo not found"
    echo "Proceeding anyway, but this may not work on unsupported hardware"
fi

# Install system dependencies
log Preparing to install ${#SYSTEM_DEPS[@]} packages on your system...
for _dep in ${SYSTEM_DEPS[@]}; do
    pkginstall $_dep;
done

# Some symlinks that will make life a little easier
# Note that this may (intentionally) clobber Python 2 symlinks in older OS's
if [ -f /usr/bin/python2 ]; then
    mv /usr/bin/python /usr/bin/python2.bak 2>/dev/null || true
fi
if [ -f /usr/bin/pip2 ]; then
    mv /usr/bin/pip /usr/bin/pip2.bak 2>/dev/null || true
fi
ln -fs `which python3` /usr/bin/python
ln -fs `which pip3` /usr/bin/pip

# Install Python dependencies from requirements.txt
log Installing Python dependencies from requirements.txt...
log This may take several minutes, especially for numpy and other compiled packages...
pip3 install --upgrade pip setuptools wheel
pip3 install -r ${INSTALL_DIR}/requirements.txt
verify "Installation of Python dependencies failed"

# Optionally add a line to /etc/sudoers
if [ -f /etc/sudoers ]; then
    KEEP_EN="Defaults             env_keep="SYNCHRONIZED_LIGHTS_HOME""
    grep -q "$KEEP_EN" /etc/sudoers
    if [ $? -ne 0 ]; then
        echo "$KEEP_EN" >> /etc/sudoers
    fi
fi

# Set up environment variables
cat <<EOF >/etc/profile.d/lightshowpi.sh
# Lightshow Pi Home
export SYNCHRONIZED_LIGHTS_HOME=${INSTALL_DIR}
# Add Lightshow Pi bin directory to path
export PATH=\$PATH:${INSTALL_DIR}/bin
EOF

# Clean up after ourselves
cd ${INSTALL_DIR} && rm -rf ${BUILD_DIR}

# Print some instructions to the user
cat <<EOF


All done! Reboot your Raspberry Pi before running lightshowPi.
Run the following command to test your installation and hardware setup:

    sudo python $INSTALL_DIR/py/hardware_controller.py --state=flash

EOF
exit 0

