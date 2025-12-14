#!/usr/bin/env python
"""
Hardware GPIO Test Script for LightShowPi Neo

This script tests GPIO functionality using gpio_adapter.
Can run in two modes:
1. Basic mode: Tests GPIO output only (visual verification)
2. Loopback mode: Tests GPIO output + input (automated verification)

PIN NUMBERING: This test uses BCM GPIO numbering for clarity.
  - BCM GPIO numbers match the Broadcom chip specification
  - Example: BCM GPIO 17 = Physical pin 11 = WiringPi pin 0

For loopback mode, connect GPIO pins in pairs:
  - Connect BCM GPIO 17 to BCM GPIO 27 (physical pins 11→13)
  - Connect BCM GPIO 22 to BCM GPIO 23 (physical pins 15→16)

Usage:
  # Basic mode (visual verification)
  sudo python tests/test_gpio_hardware.py

  # Loopback mode (automated testing)
  sudo python tests/test_gpio_hardware.py --loopback

  # Test specific BCM GPIO pin
  sudo python tests/test_gpio_hardware.py --pin 17

  # Cleanup GPIO
  sudo python tests/test_gpio_hardware.py --cleanup

Note: Requires sudo for GPIO access on Raspberry Pi
"""

import argparse
import sys
import os
import time
import logging

# Add py directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'py'))

import Platform

# Check if running on Raspberry Pi
if Platform.platform_detect() != Platform.RASPBERRY_PI:
    print("ERROR: This test must be run on a Raspberry Pi")
    print(f"Detected platform: {Platform.platform_detect()}")
    sys.exit(1)

import gpio_adapter as wiringpi

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)


class GPIOTester:
    """Test GPIO functionality using gpio_adapter (lgpio)."""

    def __init__(self, loopback=False):
        self.loopback = loopback
        self.tests_passed = 0
        self.tests_failed = 0

    def setup(self):
        """Initialize GPIO in BCM mode."""
        log.info("Initializing GPIO in BCM mode...")
        try:
            # Use BCM numbering for clarity in tests (pin numbers = BCM GPIO numbers)
            wiringpi.wiringPiSetupGpio()
            log.info("✓ GPIO initialized successfully (BCM mode)")
            return True
        except Exception as e:
            log.error(f"✗ GPIO initialization failed: {e}")
            return False

    def cleanup(self):
        """Cleanup GPIO resources."""
        log.info("Cleaning up GPIO...")
        try:
            wiringpi.cleanup()
            log.info("✓ GPIO cleanup complete")
        except Exception as e:
            log.warning(f"GPIO cleanup error (may be normal): {e}")

    def test_digital_write(self, pin=17):
        """Test basic digital write (on/off).

        Args:
            pin: BCM GPIO pin number to test (default: 17 = physical pin 11)
        """
        log.info(f"\n{'='*60}")
        log.info(f"TEST: Digital Write on BCM GPIO {pin}")
        log.info(f"{'='*60}")

        try:
            # Set pin as output
            wiringpi.pinModePY(pin, wiringpi.OUTPUT)
            log.info(f"✓ Pin {pin} configured as OUTPUT")

            # Test HIGH
            log.info(f"Setting GPIO {pin} HIGH...")
            wiringpi.digitalWritePY(pin, wiringpi.HIGH)
            time.sleep(0.5)
            log.info(f"✓ GPIO {pin} set to HIGH")

            # Test LOW
            log.info(f"Setting GPIO {pin} LOW...")
            wiringpi.digitalWritePY(pin, wiringpi.LOW)
            time.sleep(0.5)
            log.info(f"✓ GPIO {pin} set to LOW")

            # Blink test
            log.info(f"Blinking GPIO {pin} 5 times...")
            for i in range(5):
                wiringpi.digitalWritePY(pin, wiringpi.HIGH)
                time.sleep(0.2)
                wiringpi.digitalWritePY(pin, wiringpi.LOW)
                time.sleep(0.2)
                print(".", end="", flush=True)
            print()

            log.info(f"✓ Digital write test PASSED on GPIO {pin}")
            self.tests_passed += 1
            return True

        except Exception as e:
            log.error(f"✗ Digital write test FAILED on GPIO {pin}: {e}")
            self.tests_failed += 1
            return False

    def test_pwm(self, pin=18):
        """Test software PWM.

        Args:
            pin: BCM GPIO pin number to test (default: 18 = physical pin 12)
        """
        log.info(f"\n{'='*60}")
        log.info(f"TEST: Software PWM on BCM GPIO {pin}")
        log.info(f"{'='*60}")

        try:
            # Create PWM on pin
            wiringpi.softPwmCreatePY(pin, 0, 100)
            log.info(f"✓ PWM created on GPIO {pin} (range: 0-100)")

            # Fade up
            log.info(f"Fading up GPIO {pin}...")
            for value in range(0, 101, 10):
                wiringpi.softPwmWritePY(pin, value)
                time.sleep(0.1)
                print(".", end="", flush=True)
            print()
            log.info(f"✓ PWM fade up complete")

            # Fade down
            log.info(f"Fading down GPIO {pin}...")
            for value in range(100, -1, -10):
                wiringpi.softPwmWritePY(pin, value)
                time.sleep(0.1)
                print(".", end="", flush=True)
            print()
            log.info(f"✓ PWM fade down complete")

            # Stop PWM
            wiringpi.softPwmStopPY(pin)
            log.info(f"✓ PWM stopped on GPIO {pin}")

            log.info(f"✓ PWM test PASSED on GPIO {pin}")
            self.tests_passed += 1
            return True

        except Exception as e:
            log.error(f"✗ PWM test FAILED on GPIO {pin}: {e}")
            self.tests_failed += 1
            return False

    def test_loopback_digital(self, output_pin=17, input_pin=27):
        """Test digital loopback (output pin connected to input pin).

        Args:
            output_pin: BCM GPIO pin for output (default: 17 = physical pin 11)
            input_pin: BCM GPIO pin for input (default: 27 = physical pin 13)
        """
        log.info(f"\n{'='*60}")
        log.info(f"TEST: Digital Loopback (BCM GPIO {output_pin} → BCM GPIO {input_pin})")
        log.info(f"{'='*60}")
        log.info(f"REQUIRED: Connect BCM GPIO {output_pin} (pin 11) to BCM GPIO {input_pin} (pin 13) with jumper wire")

        try:
            # Configure pins
            wiringpi.pinModePY(output_pin, wiringpi.OUTPUT)
            wiringpi.pinModePY(input_pin, wiringpi.INPUT)
            log.info(f"✓ GPIO {output_pin} configured as OUTPUT")
            log.info(f"✓ GPIO {input_pin} configured as INPUT")

            # Test HIGH
            log.info(f"Testing HIGH signal...")
            wiringpi.digitalWritePY(output_pin, wiringpi.HIGH)
            time.sleep(0.1)  # Allow signal to settle
            value = wiringpi.digitalReadPY(input_pin)

            if value == wiringpi.HIGH:
                log.info(f"✓ HIGH signal verified (output={wiringpi.HIGH}, input={value})")
            else:
                log.error(f"✗ HIGH signal FAILED (output={wiringpi.HIGH}, input={value})")
                log.error(f"  Check connection between GPIO {output_pin} and GPIO {input_pin}")
                self.tests_failed += 1
                return False

            # Test LOW
            log.info(f"Testing LOW signal...")
            wiringpi.digitalWritePY(output_pin, wiringpi.LOW)
            time.sleep(0.1)
            value = wiringpi.digitalReadPY(input_pin)

            if value == wiringpi.LOW:
                log.info(f"✓ LOW signal verified (output={wiringpi.LOW}, input={value})")
            else:
                log.error(f"✗ LOW signal FAILED (output={wiringpi.LOW}, input={value})")
                log.error(f"  Check connection between GPIO {output_pin} and GPIO {input_pin}")
                self.tests_failed += 1
                return False

            log.info(f"✓ Digital loopback test PASSED")
            self.tests_passed += 1
            return True

        except Exception as e:
            log.error(f"✗ Digital loopback test FAILED: {e}")
            self.tests_failed += 1
            return False

    def run_all_tests(self):
        """Run all GPIO tests."""
        log.info("\n" + "="*60)
        log.info("LightShowPi Neo - GPIO Hardware Test")
        log.info("="*60)

        # Get Pi info
        pi_info = Platform.get_hardware_info()
        log.info(f"Hardware: {pi_info['model']}")
        log.info(f"Version: Pi {pi_info['version']}")
        log.info(f"Supported: {pi_info['supported']}")

        if not self.setup():
            log.error("GPIO initialization failed. Aborting tests.")
            return False

        try:
            # Basic tests (work on any setup)
            log.info("\n" + "="*60)
            log.info("BASIC GPIO TESTS")
            log.info("="*60)

            self.test_digital_write(pin=17)
            self.test_digital_write(pin=22)
            self.test_pwm(pin=18)

            # Loopback tests (require physical connections)
            if self.loopback:
                log.info("\n" + "="*60)
                log.info("LOOPBACK TESTS (requires jumper wires)")
                log.info("="*60)

                input("Connect GPIO 17 to GPIO 27, then press Enter...")
                self.test_loopback_digital(output_pin=17, input_pin=27)

            # Summary
            log.info("\n" + "="*60)
            log.info("TEST SUMMARY")
            log.info("="*60)
            log.info(f"Tests Passed: {self.tests_passed}")
            log.info(f"Tests Failed: {self.tests_failed}")

            if self.tests_failed == 0:
                log.info("✓ ALL TESTS PASSED!")
                return True
            else:
                log.error(f"✗ {self.tests_failed} TEST(S) FAILED")
                return False

        finally:
            self.cleanup()


def main():
    parser = argparse.ArgumentParser(description='GPIO Hardware Test for LightShowPi Neo')
    parser.add_argument('--loopback', action='store_true',
                        help='Run loopback tests (requires jumper wires)')
    parser.add_argument('--pin', type=int,
                        help='Test specific BCM GPIO pin number (e.g., 17 for physical pin 11)')
    parser.add_argument('--cleanup', action='store_true',
                        help='Cleanup GPIO and exit')

    args = parser.parse_args()

    if args.cleanup:
        log.info("Cleaning up GPIO...")
        wiringpi.cleanup()
        log.info("Done.")
        return 0

    tester = GPIOTester(loopback=args.loopback)

    if args.pin:
        # Test specific pin
        if not tester.setup():
            return 1
        try:
            log.info(f"\nTesting GPIO {args.pin}...")
            tester.test_digital_write(pin=args.pin)
        finally:
            tester.cleanup()
    else:
        # Run all tests
        success = tester.run_all_tests()
        return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
