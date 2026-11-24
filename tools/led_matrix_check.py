"""Check an sACN LED matrix by eye, without running the show.

Lights the matrix from an LED config: all rows full (colours should line up in
columns), each row on its own, bars growing, then off. Uses the native
led_matrix code, so it runs on the Mac or a Pi without BiblioPixel.

    LED_IP=<receiver ip> SYNCHRONIZED_LIGHTS_HOME=$(pwd) python tools/led_matrix_check.py

LED_CONFIG (default ~/raspberrypi/server/led2.cfg) picks the config,
LED_IP overrides its sacn_address, HOLD sets seconds per step (default 3).
"""
import os, sys, time, logging; sys.path[:0] = ['py']
from unittest.mock import Mock
import numpy as np, configuration_manager, led_module
logging.basicConfig(level=logging.INFO, format='%(message)s')
cfg = os.path.expanduser(os.environ.get('LED_CONFIG', '~/raspberrypi/server/led2.cfg'))
c = configuration_manager.Configuration.__new__(configuration_manager.Configuration)
c.config_dir = os.path.dirname(cfg) + '/'; c.home_dir = os.getcwd()
c.hardware = Mock(); c.hardware.get.return_value = 7
c.set_led(config_file=os.path.basename(cfg))
if os.environ.get('LED_IP'): c.led.sacn_address = os.environ['LED_IP']
hold = float(os.environ.get('HOLD', 3))
led = led_module.Led(c.led)
print('1) all 6 rows full: the same rainbow along each row, colours lined up in columns'); led.all_leds_on(); time.sleep(hold)
for row in range(6):
    levels = np.zeros(7); levels[row] = 1.0
    print(f'2) row {row + 1} only'); led.write_all(levels); time.sleep(hold)
print('3) bars growing on all rows')
for step in range(1, 21):
    led.write_all(np.full(7, step / 20)); time.sleep(hold / 6)
print('4) off'); led.all_leds_off()
