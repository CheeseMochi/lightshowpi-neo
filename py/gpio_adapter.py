#!/usr/bin/env python
#
# Licensed under the BSD license.  See full license in LICENSE file.
# http://www.lightshowpi.org/
#
# Modern GPIO adapter using lgpio with wiringPi-compatible API
#

"""GPIO Adapter Module

This module provides a wiringPi-compatible API using modern lgpio library.
It serves as a drop-in replacement for wiringpipy, allowing the codebase
to use lgpio without major refactoring.

Key features:
- Software PWM via lgpio.tx_pwm
- Digital I/O via lgpio GPIO functions
- I2C expander support via adafruit-circuitpython-mcp230xx
- Compatible with existing hardware_controller.py code
"""

import logging
import lgpio
from typing import Dict, Optional

# Global state
_chip_handle: Optional[int] = None
_pwm_pins: Dict[int, dict] = {}  # Track PWM state per pin
_pwm_frequency = 100  # Default PWM frequency in Hz

# Pin mode constants (matching wiringPi)
INPUT = 0
OUTPUT = 1

# Digital value constants (matching wiringPi)
LOW = 0
HIGH = 1

# I2C/SPI expander tracking
_expanders: Dict[int, object] = {}  # Maps pinBase to expander object


# ============================================================================
# Core Setup Functions
# ============================================================================

def wiringPiSetupPY():
    """Initialize GPIO chip using lgpio

    This opens the gpiochip device and stores the handle globally.
    Maps to: lgpio.gpiochip_open(0)
    """
    global _chip_handle
    try:
        _chip_handle = lgpio.gpiochip_open(0)
        logging.debug(f"GPIO initialized with lgpio, handle: {_chip_handle}")
    except Exception as e:
        logging.error(f"Failed to initialize GPIO: {e}")
        raise


# ============================================================================
# Pin Mode Configuration
# ============================================================================

def pinModePY(pin: int, mode: int):
    """Set pin mode to input or output

    Args:
        pin: GPIO pin number
        mode: 0 for INPUT, 1 for OUTPUT

    Maps to: lgpio.gpio_claim_input() or lgpio.gpio_claim_output()
    """
    if _chip_handle is None:
        logging.error("GPIO not initialized. Call wiringPiSetupPY first.")
        return

    try:
        if mode == INPUT:
            lgpio.gpio_claim_input(_chip_handle, pin)
            logging.debug(f"Pin {pin} set as INPUT")
        elif mode == OUTPUT:
            lgpio.gpio_claim_output(_chip_handle, pin)
            logging.debug(f"Pin {pin} set as OUTPUT")
        else:
            logging.warning(f"Unknown pin mode {mode} for pin {pin}")
    except Exception as e:
        logging.error(f"Failed to set pin {pin} mode: {e}")


# ============================================================================
# Software PWM Functions
# ============================================================================

def softPwmCreatePY(pin: int, initial_value: int, pwm_range: int):
    """Create software PWM on a pin

    Args:
        pin: GPIO pin number
        initial_value: Initial PWM value (0 to pwm_range)
        pwm_range: Maximum PWM value (e.g., 100 for percentage)

    Returns:
        0 on success, None on failure

    Maps to: lgpio.gpio_claim_output() + lgpio.tx_pwm()
    Note: lgpio uses frequency + duty cycle, not range + value
    """
    if _chip_handle is None:
        logging.error("GPIO not initialized. Call wiringPiSetupPY first.")
        return None

    try:
        # Claim pin as output if not already claimed
        lgpio.gpio_claim_output(_chip_handle, pin)

        # Store PWM configuration for this pin
        _pwm_pins[pin] = {
            'range': pwm_range,
            'frequency': _pwm_frequency
        }

        # Set initial PWM value
        duty_cycle = (initial_value / pwm_range) * 100.0 if pwm_range > 0 else 0
        lgpio.tx_pwm(_chip_handle, pin, _pwm_frequency, duty_cycle)

        logging.debug(f"PWM created on pin {pin}: range={pwm_range}, "
                     f"initial={initial_value}, freq={_pwm_frequency}Hz")
        return 0
    except Exception as e:
        logging.error(f"Failed to create PWM on pin {pin}: {e}")
        return None


def softPwmWritePY(pin: int, value: int):
    """Write PWM value to a pin

    Args:
        pin: GPIO pin number
        value: PWM value (0 to pwm_range configured in softPwmCreatePY)

    Maps to: lgpio.tx_pwm() with duty cycle conversion
    """
    if _chip_handle is None:
        logging.error("GPIO not initialized.")
        return

    if pin not in _pwm_pins:
        logging.warning(f"Pin {pin} not configured for PWM. Call softPwmCreatePY first.")
        # Try to create it with default range
        softPwmCreatePY(pin, 0, 100)

    try:
        pwm_range = _pwm_pins[pin]['range']
        frequency = _pwm_pins[pin]['frequency']

        # Convert value to duty cycle percentage
        duty_cycle = (value / pwm_range) * 100.0 if pwm_range > 0 else 0
        duty_cycle = max(0, min(100, duty_cycle))  # Clamp to 0-100

        lgpio.tx_pwm(_chip_handle, pin, frequency, duty_cycle)
    except Exception as e:
        logging.error(f"Failed to write PWM value {value} to pin {pin}: {e}")


def softPwmStopPY(pin: int):
    """Stop PWM on a pin

    Args:
        pin: GPIO pin number

    Maps to: lgpio.tx_pwm() with 0 frequency to stop PWM
    """
    if _chip_handle is None:
        logging.error("GPIO not initialized.")
        return

    try:
        # Stop PWM by setting frequency to 0
        lgpio.tx_pwm(_chip_handle, pin, 0, 0)

        # Remove from tracking dict
        if pin in _pwm_pins:
            del _pwm_pins[pin]

        logging.debug(f"PWM stopped on pin {pin}")
    except Exception as e:
        logging.error(f"Failed to stop PWM on pin {pin}: {e}")


# ============================================================================
# Digital I/O Functions
# ============================================================================

def digitalWritePY(pin: int, value: int):
    """Write digital value to a pin

    Args:
        pin: GPIO pin number
        value: 0 for LOW, 1 for HIGH

    Maps to: lgpio.gpio_write()
    """
    if _chip_handle is None:
        logging.error("GPIO not initialized.")
        return

    try:
        lgpio.gpio_write(_chip_handle, pin, 1 if value else 0)
    except Exception as e:
        logging.error(f"Failed to write digital value {value} to pin {pin}: {e}")


def digitalReadPY(pin: int) -> int:
    """Read digital value from a pin

    Args:
        pin: GPIO pin number

    Returns:
        0 for LOW, 1 for HIGH

    Maps to: lgpio.gpio_read()
    """
    if _chip_handle is None:
        logging.error("GPIO not initialized.")
        return 0

    try:
        return lgpio.gpio_read(_chip_handle, pin)
    except Exception as e:
        logging.error(f"Failed to read from pin {pin}: {e}")
        return 0


def analogWritePY(pin: int, value: int):
    """Write analog value (for PiGlow and similar devices)

    Args:
        pin: GPIO pin number (may be offset for specific devices)
        value: Analog value (0-255 typically)

    Note: This is device-specific. WiringPi had special handling for PiGlow.
    For now, we'll map this to PWM with 0-255 range.
    """
    if _chip_handle is None:
        logging.error("GPIO not initialized.")
        return

    try:
        # For PiGlow compatibility, wiringPi uses pin + 577
        # We'll handle the raw pin and map to PWM
        duty_cycle = (value / 255.0) * 100.0
        duty_cycle = max(0, min(100, duty_cycle))

        # Use default frequency for analog writes
        lgpio.tx_pwm(_chip_handle, pin, _pwm_frequency, duty_cycle)
    except Exception as e:
        logging.error(f"Failed to write analog value {value} to pin {pin}: {e}")


# ============================================================================
# I2C/SPI Expander Functions
# ============================================================================
# Note: wiringPi had built-in support for expanders. lgpio doesn't.
# We'll use adafruit-circuitpython-mcp230xx for MCP chips.
# These functions maintain API compatibility but issue warnings.

def mcp23008SetupPY(pinBase: int, i2cAddress: int):
    """Setup MCP23008 I2C GPIO expander

    Args:
        pinBase: Base pin number for this expander
        i2cAddress: I2C address of the device

    Note: lgpio doesn't have built-in expander support.
    Use adafruit-circuitpython-mcp230xx library instead.
    """
    logging.warning(f"MCP23008 expander setup requested (pinBase={pinBase}, "
                   f"addr=0x{i2cAddress:02X}). lgpio requires external library "
                   f"(adafruit-circuitpython-mcp230xx) for expander support. "
                   f"This functionality needs additional integration.")
    # TODO: Integrate adafruit_mcp230xx.mcp23008 here
    _expanders[pinBase] = {'type': 'mcp23008', 'address': i2cAddress}


def mcp23017SetupPY(pinBase: int, i2cAddress: int):
    """Setup MCP23017 I2C GPIO expander

    Args:
        pinBase: Base pin number for this expander
        i2cAddress: I2C address of the device

    Returns:
        -1 (not implemented - requires external library)
    """
    logging.warning(f"MCP23017 expander setup requested (pinBase={pinBase}, "
                   f"addr=0x{i2cAddress:02X}). lgpio requires external library "
                   f"(adafruit-circuitpython-mcp230xx) for expander support. "
                   f"This functionality needs additional integration.")
    # TODO: Integrate adafruit_mcp230xx.mcp23017 here
    _expanders[pinBase] = {'type': 'mcp23017', 'address': i2cAddress}
    return -1


def mcp23016SetupPY(pinBase: int, i2cAddress: int):
    """Setup MCP23016 I2C GPIO expander"""
    logging.warning(f"MCP23016 expander setup requested (pinBase={pinBase}, "
                   f"addr=0x{i2cAddress:02X}). lgpio doesn't have built-in support. "
                   f"Expander functionality may not work.")
    _expanders[pinBase] = {'type': 'mcp23016', 'address': i2cAddress}


def mcp23s08SetupPY(pinBase: int, spiPort: int, devId: int):
    """Setup MCP23S08 SPI GPIO expander"""
    logging.warning(f"MCP23S08 SPI expander setup requested (pinBase={pinBase}). "
                   f"lgpio doesn't have built-in support. "
                   f"Expander functionality may not work.")
    _expanders[pinBase] = {'type': 'mcp23s08', 'spiPort': spiPort, 'devId': devId}


def mcp23s17SetupPY(pinBase: int, spiPort: int, devId: int):
    """Setup MCP23S17 SPI GPIO expander

    Returns:
        -1 (not implemented - requires external library)
    """
    logging.warning(f"MCP23S17 SPI expander setup requested (pinBase={pinBase}). "
                   f"lgpio doesn't have built-in support. "
                   f"Expander functionality may not work.")
    _expanders[pinBase] = {'type': 'mcp23s17', 'spiPort': spiPort, 'devId': devId}
    return -1


def sr595SetupPY(pinBase: int, numPins: int, dataPin: int, clockPin: int, latchPin: int):
    """Setup 74HC595 shift register"""
    logging.warning(f"74HC595 shift register setup requested (pinBase={pinBase}). "
                   f"lgpio doesn't have built-in support. "
                   f"Shift register functionality may not work.")
    _expanders[pinBase] = {
        'type': 'sr595',
        'numPins': numPins,
        'dataPin': dataPin,
        'clockPin': clockPin,
        'latchPin': latchPin
    }


def pcf8574SetupPY(pinBase: int, i2cAddress: int):
    """Setup PCF8574 I2C GPIO expander"""
    logging.warning(f"PCF8574 expander setup requested (pinBase={pinBase}, "
                   f"addr=0x{i2cAddress:02X}). lgpio doesn't have built-in support. "
                   f"Expander functionality may not work.")
    _expanders[pinBase] = {'type': 'pcf8574', 'address': i2cAddress}


# ============================================================================
# Cleanup
# ============================================================================

def cleanup():
    """Cleanup GPIO resources"""
    global _chip_handle

    if _chip_handle is not None:
        try:
            # Stop all PWM
            for pin in _pwm_pins.keys():
                try:
                    lgpio.tx_pwm(_chip_handle, pin, 0, 0)  # Stop PWM
                except:
                    pass

            # Close chip handle
            lgpio.gpiochip_close(_chip_handle)
            logging.debug("GPIO cleanup completed")
        except Exception as e:
            logging.error(f"Error during GPIO cleanup: {e}")
        finally:
            _chip_handle = None
            _pwm_pins.clear()
            _expanders.clear()


# ============================================================================
# Module Info
# ============================================================================

def get_info():
    """Return information about the GPIO adapter"""
    return {
        'backend': 'lgpio',
        'chip_handle': _chip_handle,
        'pwm_pins': list(_pwm_pins.keys()),
        'expanders': list(_expanders.keys()),
        'initialized': _chip_handle is not None
    }
