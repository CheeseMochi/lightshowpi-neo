#!/usr/bin/env python
#
# Licensed under the BSD license.  See full license in LICENSE file.
# http://www.lightshowpi.org/
#
# Author: Micah Wedemeyer
# Author: Tom Enos (tomslick.ca@gmail.com)


"""Empty wrapper module for wiringpi

This module is a place holder for virtual hardware to run a simulated lightshow
an a pc.  This module is not yet functional.
"""

# Setup
def wiringPiSetup(*args):
    pass


def wiringPiSetupPY(*args):
    """Alias with PY suffix for compatibility."""
    pass


def wiringPiSetupSys(*args):
    pass


def pinMode(*args):
    pass


def pinModePY(*args):
    """Alias with PY suffix for compatibility."""
    pass


# Pin Writes
def softPwmCreate(*args):
    pass


def softPwmCreatePY(*args):
    """Alias with PY suffix for compatibility."""
    pass


def softPwmWrite(*args):
    pass


def softPwmWritePY(*args):
    """Alias with PY suffix for compatibility."""
    pass


def softPwmStop(*args):
    pass


def softPwmStopPY(*args):
    """Alias with PY suffix for compatibility."""
    pass


def digitalWrite(*args):
    pass


def digitalWritePY(*args):
    """Alias with PY suffix for compatibility."""
    pass


def digitalRead(*args):
    return 0


def digitalReadPY(*args):
    """Alias with PY suffix for compatibility."""
    return 0


def analogWrite(*args):
    pass


def analogWritePY(*args):
    """Alias with PY suffix for compatibility."""
    pass


# Devices
def mcp23017Setup(*args):
    pass


def mcp23017SetupPY(*args):
    """Alias with PY suffix for compatibility."""
    pass


def mcp23s17Setup(*args):
    pass


def mcp23s17SetupPY(*args):
    """Alias with PY suffix for compatibility."""
    pass


def mcp23016Setup(*args):
    pass


def mcp23016SetupPY(*args):
    """Alias with PY suffix for compatibility."""
    pass


def mcp23008Setup(*args):
    pass


def mcp23008SetupPY(*args):
    """Alias with PY suffix for compatibility."""
    pass


def mcp23s08Setup(*args):
    pass


def mcp23s08SetupPY(*args):
    """Alias with PY suffix for compatibility."""
    pass


def sr595Setup(*args):
    pass


def sr595SetupPY(*args):
    """Alias with PY suffix for compatibility."""
    pass


def pcf8574Setup(*args):
    pass


def pcf8574SetupPY(*args):
    """Alias with PY suffix for compatibility."""
    pass
