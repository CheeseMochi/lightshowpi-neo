#!/usr/bin/env python
#
# Licensed under the BSD license.  See full license in LICENSE file.
# http://www.lightshowpi.org/
#
# Author: Ryan Jennings
# Author: Chris Usey
# Author: Todd Giles (todd@lightshowpi.org)
# Author: Tom Enos (tomslick.ca@gmail.com)

"""Control the Raspberry pi hardware.

The hardware controller handles all interaction with the raspberry pi
hardware to turn the lights on and off.

Third party dependencies:

lgpio: modern GPIO library for Linux (via gpio_adapter.py)
    https://pypi.org/project/lgpio/
"""

import argparse
import json
import logging
import math
import time
import sys
import Platform
import random
import atexit
import signal
import threading
import os
import numpy as np
from multiprocessing.managers import BaseManager

from collections import defaultdict

import configuration_manager
import networking
import networking_sacn
from outputs.led import LedOutput
from outputs.threaded import ThreadedOutput

# LED module is optional (only needed for RGB LED strips/matrices)
try:
    import led_module
    LED_MODULE_AVAILABLE = True
except ImportError as e:
    logging.warning(f"LED module not available: {e}. RGB LED features will be disabled.")
    LED_MODULE_AVAILABLE = False
    led_module = None

args = None
cm = None
hc = None
patterns = None


def exit_function():
    """atexit function"""
    if args and hc is not None:
        if getattr(args, 'serve', False):
            # serve() turns the lights off and cleans up itself
            return
        if args.state == "random_pattern" and patterns is not None:
            patterns.exit_event.set()
            time.sleep(3)
            hc.turn_off_lights()
        elif not args.state == "on" and not args.state == "off":
            hc.turn_off_lights()


atexit.register(exit_function)

# Remove traceback on Ctrl-C
#signal.signal(signal.SIGINT, lambda x, y: sys.exit(0))
signal.signal(signal.SIGINT, signal.SIG_IGN)

# Test if running on a RaspberryPi
is_a_raspberryPI = Platform.platform_detect() == 1

if is_a_raspberryPI:
    # Use modern lgpio-based adapter on Raspberry Pi
    import gpio_adapter as wiringpi
else:
    # Use mock for testing on non-Pi systems
    import wiring_pi as wiringpi

    logging.debug("Not running on a raspberryPI")

class LEDManager(BaseManager):
    pass

class Hardware(object):

    def __init__(self, param_config=None):
        self.cm = configuration_manager.Configuration(param_config=param_config)
        global cm
        cm = self.cm

        # list to store the Channels instances in
        self.channels = list()

        # network setup if used
        self.network = networking.Networking(cm)
        self.server = self.network.networking == "server" or self.network.networking == "serverjson"
        self.broadcast = self.network.broadcast

        # Every frame destination (LEDs, Nanoleaf, ...). GPIO channels are still
        # driven directly through self.channels. self.led keeps the raw Led
        # instances for user scripts that use hc.led.
        self.outputs = list()
        self.led = None
        if self.cm.configs.led:
            if not LED_MODULE_AVAILABLE:
                logging.warning("LED configuration found but led_module is not available. "
                              "Install BiblioPixel to enable RGB LED support.")
            else:
                self.led = list()
                if self.cm.configs.led_multiprocess:
                    LEDManager.register('LED', led_module.Led)
                    for lc in self.cm.configs.led:
                        self.cm.set_led(config_file=lc)
                        self.ledmanager = LEDManager()
                        self.ledmanager.start()
                        self.cm.led.multiprocess = True
                        self.led.append(self.ledmanager.LED(self.cm.led))
                        self._add_led_output(self.led[-1], lc)
                else:
                    for lc in self.cm.configs.led:
                        self.cm.set_led(config_file=lc)
                        self.cm.led.multiprocess = False
                        self.led.append(led_module.Led(self.cm.led))
                        self._add_led_output(self.led[-1], lc)

        self.create_lights()
        self.set_overrides()

    def _add_led_output(self, led, config_file):
        """Wrap a Led as an Output, using the LED config that was just loaded."""
        output = LedOutput(
            led,
            channel_configuration=self.cm.led.led_channel_configuration,
            physical_gpio_len=self.cm.hardware.physical_gpio_len,
            gpio_len=self.cm.hardware.gpio_len,
            slice_levels=self.network.networking != "client",
            name="led:%s" % config_file)
        if getattr(self.cm.led, 'threaded', False):
            output = ThreadedOutput(output, max_fps=getattr(self.cm.led, 'max_fps', 0) or None)
        self.outputs.append(output)

    def write_outputs(self, levels):
        """Send one frame of channel levels to every output.

        :param levels: per-channel levels 0..1; lists are converted, since
            led_module scales with ``levels * 255``
        """
        outputs = getattr(self, 'outputs', ())
        if not outputs:
            return
        levels = np.asarray(levels, dtype='float32')
        for output in outputs:
            output.write_frame(levels)

    # Methods
    @staticmethod
    def enable_device():
        """enable the specified device """
        try:
            devices = cm.hardware.devices

            for key in devices.keys():
                device = key
                device_slaves = devices[key]

                if device.lower() == "mcp23008":
                    for slave in device_slaves:
                        params = slave
                        wiringpi.mcp23008SetupPY(int(params['pinBase']),
                                               int(params['i2cAddress'],
                                                   16))

                elif device.lower() == "mcp23s08":
                    for slave in device_slaves:
                        params = slave
                        wiringpi.mcp23s08SetupPY(int(params['pinBase']),
                                               int(params['spiPort'],
                                                   16),
                                               int(params['devId']))

                elif device.lower() == "mcp23016":
                    for slave in device_slaves:
                        params = slave
                        wiringpi.mcp23016SetupPY(int(params['pinBase']),
                                               int(params['i2cAddress'],
                                                   16))

                elif device.lower() == "mcp23017":
                    for slave in device_slaves:
                        params = slave
                        wiringpi.mcp23017SetupPY(int(params['pinBase']),
                                               int(params['i2cAddress'],
                                                   16))

                elif device.lower() == "mcp23s17":
                    for slave in device_slaves:
                        params = slave
                        wiringpi.mcp23s17SetupPY(int(params['pinBase']),
                                               int(params['spiPort'],
                                                   16),
                                               int(params['devId']))

                elif device.lower() == "sr595":
                    for slave in device_slaves:
                        params = slave
                        wiringpi.sr595SetupPY(int(params['pinBase']),
                                            int(params['numPins']),
                                            int(params['dataPin']),
                                            int(params['clockPin']),
                                            int(params['latchPin']))

                elif device.lower() == "pcf8574":
                    for slave in device_slaves:
                        params = slave
                        wiringpi.pcf8574SetupPY(int(params['pinBase']),
                                              int(params['i2cAddress'],
                                                  16))

                else:
                    logging.error("Device defined is not supported, please check "
                                  "your devices settings: " + str(device))
        except Exception as error:
            logging.debug("Error setting up devices, please check your devices "
                          "settings.")
            logging.debug(error)

    def create_lights(self):
        """
        Create a Channel instance for each gpio pin to be used,
        setting up
        """
        for channel in range(cm.hardware.gpio_len):
            self.channels.append(Channel(cm.hardware.gpio_pins[channel],
                                         cm.hardware.is_pin_pwm[channel],
                                         cm.hardware.active_low_mode[channel],
                                         cm.hardware.pwm_range,
                                         cm.hardware.piglow))

    def set_overrides(self):
        """
        Set override flags if they are to be used
        """
        for channel in range(cm.hardware.gpio_len):
            self.channels[channel].set_always_off(
                channel + 1 in cm.lightshow.always_off_channels)
            self.channels[channel].set_always_on(
                channel + 1 in cm.lightshow.always_on_channels)
            self.channels[channel].set_inverted(
                channel + 1 in cm.lightshow.invert_channels)

    def set_pins_as_outputs(self):
        """Set all the configured pins as outputs."""
        for pin in range(cm.hardware.gpio_len):
            self.set_pin_as_output(pin)

    def set_pins_as_inputs(self):
        """Set all the configured pins as inputs."""
        for pin in range(cm.hardware.gpio_len):
            self.set_pin_as_input(pin)

    def set_pin_as_output(self, pin):
        """
        Set the specified pin as an output.

        :param pin: int, index of pin in gpio_pins
        """
        self.channels[pin].set_as_output()

    def set_pin_as_input(self, pin):
        """
        Set the specified pin as an input.

        :param pin: int, index of pin in gpio_pins
        """
        self.channels[pin].set_as_input()

    def turn_on_lights(self, use_always_onoff=False):
        """
        Turn on all the lights

        But leave off all lights designated to be always off if specified.

        :param use_always_onoff: int or boolean, should always on/off be used
        """
        for pin in range(cm.hardware.physical_gpio_len):
            self.set_light(pin, use_always_onoff, 1.0)

        for output in getattr(self, 'outputs', ()):
            output.all_on()

    def turn_off_lights(self, use_always_onoff=False):
        """
        Turn off all the lights

        But leave on all lights designated to be always on if specified.

        :param use_always_onoff: int or boolean, should always on/off be used
        """
        for pin in range(cm.hardware.physical_gpio_len):
            self.set_light(pin, use_always_onoff, 0)

        for output in getattr(self, 'outputs', ()):
            output.all_off()

    # turn_off_light and turn_on_light are left in for compatibility
    # with external scripts and will be removed in future versions
    def turn_off_light(self, pin, use_overrides=False):
        """
        Turn off the specified light

        Taking into account various overrides if specified.

        :param pin: index of pin in cm.hardware.gpio_pins
        :type pin: int

        :param use_overrides: should overrides be used
        :type use_overrides: bool
        """
        self.set_light(pin, use_overrides, 0)

    def turn_on_light(self, pin, use_overrides=False, brightness=1.0):
        """
        Turn off the specified light

        Taking into account various overrides if specified.

        :param pin: index of pin in cm.hardware.gpio_pins
        :type pin: int

        :param use_overrides: should overrides be used
        :type use_overrides: bool
        """
        self.set_light(pin, use_overrides, brightness)

    def set_light(self, pin, use_overrides=False, brightness=1.0):
        """Set the brightness of the specified light

        Taking into account various overrides if specified.
        The default is full on (1.0)
        To turn a light off pass 0 for brightness
        If brightness is a float between 0 and 1.0 that level
        will be set.

        This function replaces turn_on_light and turn_off_light

        :param pin: index of pin in cm.hardware.gpio_pins
        :type pin: int

        :param use_overrides: should overrides be used
        :type use_overrides: bool

        :param brightness: float, a float representing the brightness of the lights
        :type brightness: float
        """
        #if not self.network.playing and self.server:
        if self.server:
            data = networking.BcastPin(pin=cm.hardware.gpio_pins.index(cm.hardware.gpio_pins[pin]),brightness=brightness,use_overrides=use_overrides)
            self.broadcast(data)

        self.channels[pin].set_action(use_overrides, brightness)

    def set_array(self, brightnesses, use_overrides=False):
        """Set the brightness of all lights based on an array

        Taking into account various overrides if specified.
        The default is full on (1.0)
        To turn a light off pass 0 for brightness
        If brightness is a float between 0 and 1.0 that level
        will be set.

        :param use_overrides: should overrides be used
        :type use_overrides: bool

        :param brightness: float, a float representing the brightness of the lights
        :type brightness: float
        """

        #if not self.network.playing and self.server:
        if self.server:
            data = networking.BcastArray(array=brightnesses,use_overrides=use_overrides)
            self.broadcast(data)

        # LEDs and other outputs follow the same frame, so ambient mode and
        # scripts that use set_array light them too
        self.write_outputs(brightnesses)

        if len(brightnesses) != cm.hardware.physical_gpio_len:
            logging.warning("brightnesses length %s not equal to physical_gpio_length %s", len(brightnesses), cm.hardware.physical_gpio_len)
        for pin in range(min(len(brightnesses),cm.hardware.physical_gpio_len)):
            self.channels[pin].set_action(use_overrides, brightnesses[pin])

    def set_custom_overrides(self, always_off=[], always_on=[], inverted=[]):
        """Adds ability to set custom overrides directly

        :param always_off: list of always_off pins. An empty list means do nothing
        :type always_off: list

        :param always_on: list of always_off pins. -1 means no always_on pins. An empty list means do nothing
        :type always_on: list

        :param inverted: list of inverted pins. -1 means invert nothing. An empty list means do nothing
        :type inverted: list

        """

        self.set_overrides()
        if self.server:
            # Check if using sACN with control plane
            if hasattr(self.network, 'broadcast_overrides'):
                # sACN mode - use control plane (JSON over UDP)
                self.network.broadcast_overrides(always_off=always_off, always_on=always_on, inverted=inverted)
                logging.info(f"Sent overrides via sACN control plane")
            else:
                # Legacy mode - use pickle broadcast
                data = networking.BcastOverrides(always_off=always_off,always_on=always_on,inverted=inverted)
                self.broadcast(data)
        else:
            if self.network.get_overrides:
                for channel in range(cm.hardware.gpio_len):
                    if always_off:
                        self.channels[channel].set_always_off(
                            channel + 1 in always_off)
                    if always_on:
                        self.channels[channel].set_always_on(
                            channel + 1 in always_on)
                    if inverted:
                        self.channels[channel].set_inverted(
                            channel + 1 in inverted)

    def receive_and_process(self):
        channels = self.network.channels

        # Check for control messages first (sACN control plane)
        if hasattr(self.network, 'receive_control_message'):
            ctrl_msg = self.network.receive_control_message()
            if ctrl_msg:
                # Handle different control message types
                if ctrl_msg.get('type') == 'overrides':
                    # Apply override configuration
                    self.set_custom_overrides(
                        always_off=ctrl_msg.get('always_off', []),
                        always_on=ctrl_msg.get('always_on', []),
                        inverted=ctrl_msg.get('inverted', [])
                    )
                    logging.info(f"Received overrides via sACN control plane")

        # Receive data (sACN or legacy)
        data = self.network.receive()
        temp_override = True

        if isinstance(data[0], (int, np.integer)):
            # legacy (pin, brightness) tuple
            pin = data[0]
            for x in range(len(channels)):
                if pin == abs(channels[x]):
                    # Generally these are not music related, so let's ignore the overrides
                    self.set_light(x, False, float(data[1] if channels[x] >= 0 else 0))
            return

        elif isinstance(data[0], networking.BcastPin):
            pin = data[0].pin
            for x in range(len(channels)):
                if pin == abs(channels[x]):
                    self.set_light(x, data[0].use_overrides,
                                   float(data[0].brightness if channels[x] >= 0 else 0))
            return

        elif isinstance(data[0], networking.BcastOverrides):
            self.set_custom_overrides(always_off=data[0].always_off, always_on=data[0].always_on, inverted=data[0].inverted)
            return

        elif isinstance(data[0], np.ndarray):
            brightness_levels = data[0]

        elif isinstance(data[0], networking.BcastArray):
            brightness_levels = np.asarray(data[0].array)
            temp_override = data[0].use_overrides

        elif isinstance(data[0], list):
            # ambient mode's keepalive broadcasts a plain list
            brightness_levels = np.asarray(data[0], dtype='float32')

        else:
            logging.warning("unrecognized type of received net data[0] %s", type(data[0]))
            return

        # LEDs get the full brightness array, whichever way the server wrapped it
        # (client LedOutputs are built with slice_levels=False)
        self.write_outputs(brightness_levels)

        for pin in range(len(channels)):
            channel = channels[pin]
            if 0 <= channel < len(brightness_levels):
                level = brightness_levels[channel]
            else:
                level = 0
            self.set_light(pin, temp_override, level)


    def clean_up(self):
        """
        Clean up and end the lightshow

        Turn off all lights and set the pins as inputs
        """
        self.network.unset_playing()
        self.turn_off_lights()
        self.close_outputs()
        self.network.close_connection()
        self.set_pins_as_inputs()

    def initialize(self,reset=True):
        """Set pins as outputs and start all lights in the off state."""
        wiringpi.wiringPiSetupPY()
        self.enable_device()
        self.set_pins_as_outputs()
        self.start_outputs()
        if reset:
            self.turn_off_lights()

    def start_outputs(self):
        """Start every output (threads, connections). Safe to call repeatedly."""
        for output in getattr(self, 'outputs', ()):
            try:
                output.start()
            except Exception:
                logging.exception("could not start output %s; continuing without it", output)

    def close_outputs(self):
        """Close every output. One failing output never blocks GPIO cleanup."""
        for output in getattr(self, 'outputs', ()):
            try:
                output.close()
            except Exception:
                logging.exception("error closing output %s", output)


class Channel(object):
    """
    Channel class
    """

    def __init__(self, pin_number, pin_mode, active_low_mode, pwm_max, piglow=False):
        self.pin_number = pin_number
        self.pwm = pin_mode

        self.pwm_max = pwm_max
        self.active_low_mode = active_low_mode

        self.pwm_on = pwm_max * int(not active_low_mode)
        self.pwm_off = pwm_max * int(active_low_mode)

        self.inout = 'Not set'
        self.always_on = False
        self.always_off = False
        self.inverted = False

        if self.pwm:
            self.action = lambda b: wiringpi.softPwmWritePY(self.pin_number,
                                                          int(b * self.pwm_max))
        elif piglow:
            self.action = lambda b: wiringpi.analogWritePY(self.pin_number + 577, int(b * 255))
        else:
            self.action = lambda b: wiringpi.digitalWritePY(self.pin_number, int(b > 0.5))

    def set_as_input(self):
        """
        set up this pin as input
        """
        self.inout = 'pin is input'
        wiringpi.pinModePY(self.pin_number, 0)

    def set_as_output(self):
        """
        set up this pin as output
        """
        self.inout = 'pin is output'
        if self.pwm:
            wiringpi.softPwmCreatePY(self.pin_number, 0, self.pwm_max)
        else:
            wiringpi.pinModePY(self.pin_number, 1)

    def set_always_on(self, value):
        """
        Should this channel be always on

        :param value: boolean
        """
        self.always_on = value

    def set_always_off(self, value):
        """
        Should this channel be always off

        :param value: boolean
        """
        self.always_off = value

    def set_inverted(self, value):
        """
        Should this channel be inverted

        :param value: boolean
        """
        self.inverted = value

    def set_action(self, use_overrides=False, brightness=1.0):
        """
        Turn this light on or off, or some value in-between

        Taking into account various overrides if specified.
        :param use_overrides: int or boolean, should overrides be used
        :param brightness: float, between 0.0 and 1.0, brightness of light
        0.0 is full off
        1.0 is full on

        """
        if self.active_low_mode:
            brightness = 1.0 - brightness

        if use_overrides:
            if self.always_off:
                brightness = 0
            elif self.always_on:
                brightness = 1

            if self.inverted:
                brightness = 1 - brightness

        self.action(brightness)


# Test patterns
#
# Patterns used to depend on module globals (hc, lights, sleep, flashes, ...)
# set in the __main__ block. They now live on the Patterns class so they can be
# used both from the CLI (looping until Ctrl-C) and from --serve (run once).
class Patterns(object):
    """Light test patterns bound to a Hardware instance.

    :param hardware: Hardware instance
    :param lights: list of channel indexes, or None / [-1] for all channels
    :param sleep: seconds between flashes/fades
    :param flashes: number of times to flash or fade each light
    :param lights_per_group: lights bound together in random_pattern
    :param pwm_speed: seconds to go full on or off in random_pattern
    :param echo: function used for progress output (default print)
    """

    NAMES = ("flash", "fade", "cylon", "step", "dance", "random_pattern")

    def __init__(self, hardware, lights=None, sleep=0.5, flashes=2,
                 lights_per_group=1, pwm_speed=0.5, echo=print):
        self.hc = hardware
        self.cm = hardware.cm
        self.sleep = float(sleep)
        self.flashes = int(flashes)
        self.lights_per_group = max(1, int(lights_per_group))
        self.pwm_speed = float(pwm_speed)
        self.echo = echo
        self.exit_event = threading.Event()

        hw = self.cm.hardware
        self.ccm = False
        self.ccm_map = dict()

        lights = list(lights) if lights is not None else [-1]
        if -1 in lights:
            lights = list(range(0, hw.gpio_len))
            mapping = self.cm.audio_processing.custom_channel_mapping

            if mapping != 0 and mapping and len(mapping) == hw.gpio_len:
                self.ccm = True
                cc = [i - 1 for i in mapping]

                for lit in lights:
                    self.ccm_map[lit] = [idx for idx, pin_num in enumerate(cc) if pin_num == lit]

                if len(lights) == hw.gpio_len:
                    lights = list(range(max(mapping)))

        self.lights = lights

    # Low-level helpers ------------------------------------------------------

    def light_on(self, pins, override=False, brightness=1.0):
        """work around to make custom channel mapping work with hardware tests"""
        if self.ccm:
            pins = self.ccm_map[pins]
        else:
            pins = [pins]

        for pin in pins:
            level = brightness
            if not self.cm.hardware.is_pin_pwm[pin]:
                level = 1

            self.hc.set_light(pin, use_overrides=override, brightness=level)

    def light_off(self, pins, override=False, brightness=0.0):
        """work around to make custom channel mapping work with hardware tests"""
        if self.ccm:
            pins = self.ccm_map[pins]
        else:
            pins = [pins]

        for pin in pins:
            self.hc.set_light(pin, use_overrides=override, brightness=brightness)

    def _announce(self, light):
        if self.ccm:
            for p in self.ccm_map[light]:
                self.echo("channel %s : gpio pin number %d" % (str(p + 1), self.cm.hardware.gpio_pins[p]))
        else:
            self.echo("channel %s : gpio pin number %d" % (
                str(light + 1), self.cm.hardware.gpio_pins[light]))

    def _ccm_notice(self):
        if self.ccm:
            self.echo("custom channel mapping is being used")
            self.echo("multiple channels may display that the same time")

    # Patterns ---------------------------------------------------------------

    def run(self, name, repeat=False):
        """Run a pattern by name. With repeat=False the pattern runs one cycle."""
        if name not in self.NAMES:
            raise ValueError("unknown pattern: %s" % name)
        return getattr(self, name)(repeat=repeat)

    def fade(self, repeat=True):
        """Fade lights in and out in sequence"""
        # Test fading in and out for each light configured in pwm mode
        hw = self.cm.hardware
        self._ccm_notice()

        while True:
            for light in self.lights:
                self._announce(light)

                if hw.is_pin_pwm[light]:
                    for _ in range(self.flashes):
                        for brightness in range(0, hw.pwm_range + 1):
                            # fade in
                            self.light_on(light, False, float(brightness) / hw.pwm_range)
                            time.sleep(self.sleep / hw.pwm_range)
                        for brightness in range(hw.pwm_range - 1, -1, -1):
                            # fade out
                            self.light_on(light, False, float(brightness) / hw.pwm_range)
                            time.sleep(self.sleep / hw.pwm_range)
                else:
                    self.echo("channel %s not set to pwm mode" % light)

            if not repeat:
                return

    def flash(self, repeat=True):
        """Flash lights in sequence"""
        self._ccm_notice()

        while True:
            for light in self.lights:
                self._announce(light)

                for _ in range(self.flashes):
                    self.light_on(light, False, 1.0)
                    time.sleep(self.sleep)
                    self.light_off(light, False, 0.0)
                    time.sleep(self.sleep)
            if not repeat:
                return

    def cylon(self, repeat=True):
        """Cylon

        Lights one channel at a time in order
        Then backs down to the first rapidly
        """
        lights = self.lights
        if len(lights) < 2:
            self.echo("cylon needs at least 2 channels")
            return

        # pause for 1 second
        time.sleep(1)

        # working loop
        while True:
            # here we just loop over the gpio pins and do something with them
            # except the last one
            for light in lights[:-1]:
                # turn off all the lights
                for l in lights:
                    self.light_off(l, False, 0.0)

                # then turn on one
                self.light_on(light, False, 1.0)

                # wait a little bit
                time.sleep(.02)

            # to make the transition back smoother we handle the last pin here
            self.light_off(lights[-2], False, 0.0)
            self.light_on(lights[-1], False, 1.0)

            # this loop walks it back the other way
            for light in lights[::-1][:-1]:
                # turn off all the lights
                for l in lights:
                    self.light_off(l, False, 0.0)

                # then turn on one
                self.light_on(light, False, 1.0)

                # wait a little bit
                time.sleep(.02)

            # again to make it smoother handle the first pin like the last pin
            self.light_off(lights[::-1][-2], False, 0.0)
            self.light_on(lights[::-1][-1], False, 1.0)

            if not repeat:
                return

    def random_pattern(self, repeat=True):
        """Display a random pattern of lights

        This will turn your lights on in a random pattern.  It will read the pin modes
        and use that for the channels you have set.  pwm fade for pwm mode and flash
        for onoff mode.

        From the command line you can set lights to work in groups by using the
        --lights_in_group option (default 1)
        --lights_in_group is the number of lights that you wish to be bound as a group.
        if set to 3, 3 channels will be bound to the first group, and this will continue
        until all channels have been use in order of pin assignment with the last
        group containing the all leftover channels in the case of odd assignment.

        and you can adjust the speed of fade with the --pwm_speed option (default .5).

        If you wish to alter the pattern delay the --sleep' (default 0.5 seconds) is
        the minimum between transitions and the maximum is defined as max_pause
        (default 4 time min_pause) in this function.  This value is a random time
        between the min and max values and not a guarantee of exact time between
        transitions and will change for every transitions.

        --pwm_speed is the time it takes for full on or full off measured in seconds.
        .5 is half a second and 1 is a full second

        A complete command string would look like
        sudo python hardware_controller --state=random_pattern --lights_in_group=2 --sleep=.75 --pwm_speed=1.5

        Initial implementation Thanks to Russell Pyburn.
        """
        hw = self.cm.hardware
        # your gpio pins
        pins = hw.gpio_pins

        # min and max time (ms) to pause before restarting light group
        min_pause = max(1, int(self.sleep * 1000))
        max_pause = min_pause * 4

        step_range = [_ for _ in range(hw.pwm_range)]
        step_range.extend([_ for _ in range(hw.pwm_range, -1, -1)])

        exit_event = self.exit_event

        def the_lights(lits):
            while not exit_event.is_set():
                time.sleep(random.randrange(min_pause, max_pause) * .001)

                # activate the lights
                for stp in step_range:
                    if exit_event.is_set():
                        break
                    for pin in lits:
                        self.light_on(pins.index(pin), True, float(stp) / hw.pwm_range)

                    time.sleep(self.pwm_speed / float(hw.pwm_range))

                if not repeat:
                    return

        # start the threads
        light_group = list()
        for group in range(0, len(pins), self.lights_per_group):
            light_group.append(threading.Thread(
                target=the_lights, args=(pins[group:group + self.lights_per_group],),
                daemon=True))
            light_group[-1].start()

        if repeat:
            self.echo("press <ctrl-c> to exit")
            while not exit_event.is_set():
                time.sleep(.1)
        else:
            for thread in light_group:
                thread.join()

    def dance(self, repeat=True):
        """
        dancing pair

        Start at each end and dance to the other using pwm or onoff
        """
        hw = self.cm.hardware
        lights = self.lights
        # the gpio pins in reversed order
        lights2 = lights[::-1]

        # this is the max value for the pwm channels
        pwm_max = hw.pwm_range

        def pair(light):
            if hw.is_pin_pwm[lights[light]]:
                for brightness in range(0, pwm_max):
                    # fade in
                    self.light_on(lights[light], False, float(brightness) / pwm_max)
                    self.light_on(lights2[light], False, float(brightness) / pwm_max)
                    time.sleep(.1 / pwm_max)

                for brightness in range(pwm_max - 1, -1, -1):
                    # fade out
                    self.light_on(lights[light], False, float(brightness) / pwm_max)
                    self.light_on(lights2[light], False, float(brightness) / pwm_max)
                    time.sleep(.1 / pwm_max)
            else:
                self.light_on(lights[light], False, 1.0)
                self.light_on(lights2[light], False, 1.0)
                time.sleep(.5)
                self.light_off(lights[light], False, 0.0)
                self.light_off(lights2[light], False, 0.0)

        while True:
            for light in range(int(len(lights) / 2)):
                pair(light)

            for light in range(int(len(lights) / 2) - 1, -1, -1):
                pair(light)

            if not repeat:
                return

    def step(self, repeat=True):
        """Test fading in and out for each light configured in pwm mode"""
        hw = self.cm.hardware
        while True:
            for light in self.lights:
                self.echo("channel %s " % light)
                for brightness in range(0, hw.pwm_range):
                    # fade in
                    self.light_on(light, False, float(brightness) / hw.pwm_range)
                    time.sleep(self.sleep / hw.pwm_range)

            for light in reversed(self.lights):
                self.echo("channel %s " % light)
                for brightness in range(hw.pwm_range - 1, -1, -1):
                    # fade out
                    if hw.is_pin_pwm[light]:
                        self.light_on(light, False, float(brightness) / hw.pwm_range)
                    else:
                        self.light_off(light, False, 0)
                    time.sleep(self.sleep / hw.pwm_range)

            if not repeat:
                return

    def test(self):
        cm = self.cm
        pi_info = Platform.get_hardware_info()

        print("We are going to do some basic tests to make sure")
        print("your hardware is working as expected.")
        print("Raspberry Pi %s" % pi_info['model'])
        print("You have %s channels defined" % str(cm.hardware.gpio_len))
        print("They are using gpio pins %s" % ", ".join(map(str, cm.hardware.gpio_pins)))
        print("You have configured your relays as active %s" % (
            "low" if cm.hardware.active_low_mode else "high"))
        print("pin_modes are %s " % ", ".join(cm.hardware.pin_modes))
        print("custom_channel_mapping %s being used" % ("is" if self.ccm else "is not"))

        if self.ccm:
            print("[%s]" % ", ".join(map(str, cm.audio_processing.custom_channel_mapping)))

        print("\nFirst we are going to flash each light in order to see if they are all working")

        input("Press Enter to continue....")

        self.flash(repeat=False)

        print("If everything went correctly you should have seen each of your channels")
        print("flash one at a time in order of assignment.")

        while True:
            answer = input("Did you see all channels flash in order? (yes/no) ").lower()
            yes = ['yes', 'y', 'yep', 'ja', 'si', 'oui']
            no = ['no', 'n', 'nope', 'nein', 'non']

            if answer in yes or answer in no:
                if answer in yes:
                    print("Great, your basic config is ready for you to start with\n\n")

                    sys.exit(1)

                if answer in no:
                    print("Lets make sure you're using the correct gpio pins")
                    print("Here is what the %s header looks like\n" % pi_info['model'])
                    print(Platform.get_gpio_header())
                    print()
                    print("Make sure you are using the correct pins as listed above")

                    if self.ccm:
                        print("Disable custom channel mapping to help with debugging your config")

                    print("After you have made these corrections please rerun this test\n\n")

                    sys.exit(1)

            print("Please answer yes or no")


# Serve mode ------------------------------------------------------------------
#
# Long-running process used by the API's test mode. Reads one JSON command per
# line from stdin and replies with one JSON line on stdout:
#
#   {"cmd": "set", "channel": 0, "on": true, "brightness": 0.5}
#   {"cmd": "all", "on": false}
#   {"cmd": "pattern", "name": "flash"}
#   {"cmd": "status"}
#   {"cmd": "quit"}
#
# Reply: {"ok": bool, "channels": [levels...], "error": str|null}
# A {"ok": true, "ready": true, ...} line is written once the hardware is ready.
# On EOF, SIGTERM or quit all lights are turned off and hc.clean_up() is run.

def _serve_reply(out, ok=True, channels=None, error=None, **extra):
    reply = {"ok": ok, "channels": list(channels or []), "error": error}
    reply.update(extra)
    out.write(json.dumps(reply) + "\n")
    out.flush()


def serve(hardware, stdin=None, stdout=None, patterns=None):
    """Run the JSON-lines command loop for test mode.

    :param hardware: Hardware instance
    :param stdin: text stream to read commands from (default sys.stdin)
    :param stdout: text stream for replies (default sys.stdout)
    :param patterns: optional Patterns instance (created if omitted)
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    hw = hardware.cm.hardware
    channel_count = hw.gpio_len
    levels = [0.0] * channel_count

    if patterns is None:
        patterns = Patterns(hardware, echo=lambda *a: logging.info(" ".join(map(str, a))))

    def all_lights(on):
        if on:
            hardware.turn_on_lights()
        else:
            hardware.turn_off_lights()
        for i in range(channel_count):
            levels[i] = 1.0 if on else 0.0

    try:
        hardware.initialize()
        hardware.network.unset_playing()
        all_lights(False)
        _serve_reply(stdout, True, levels, ready=True, channel_count=channel_count)

        while True:
            line = stdin.readline()
            if not line:
                break  # EOF
            line = line.strip()
            if not line:
                continue

            try:
                cmd = json.loads(line)
                if not isinstance(cmd, dict):
                    raise ValueError("command must be a JSON object")
            except ValueError as error:
                _serve_reply(stdout, False, levels, "invalid JSON command: %s" % error)
                continue

            name = cmd.get("cmd")
            try:
                if name == "set":
                    channel = int(cmd["channel"])
                    if not 0 <= channel < channel_count:
                        raise ValueError("channel %d out of range 0-%d" % (channel, channel_count - 1))
                    on = bool(cmd.get("on", True))
                    brightness = cmd.get("brightness")
                    brightness = 1.0 if brightness is None else float(brightness)
                    brightness = max(0.0, min(1.0, brightness))
                    level = brightness if on else 0.0
                    if level > 0 and not hw.is_pin_pwm[channel]:
                        level = 1.0
                    hardware.set_light(channel, False, level)
                    levels[channel] = level
                    _serve_reply(stdout, True, levels)

                elif name == "all":
                    all_lights(bool(cmd.get("on", False)))
                    _serve_reply(stdout, True, levels)

                elif name == "pattern":
                    pattern = cmd.get("name")
                    if pattern not in Patterns.NAMES:
                        raise ValueError("unknown pattern: %s" % pattern)
                    all_lights(False)
                    patterns.run(pattern, repeat=False)
                    all_lights(False)
                    _serve_reply(stdout, True, levels, pattern=pattern)

                elif name == "status":
                    _serve_reply(stdout, True, levels, channel_count=channel_count)

                elif name == "quit":
                    _serve_reply(stdout, True, levels)
                    break

                else:
                    raise ValueError("unknown command: %s" % name)

            except (KeyError, TypeError, ValueError) as error:
                _serve_reply(stdout, False, levels, str(error))
    finally:
        try:
            hardware.turn_off_lights()
        finally:
            hardware.clean_up()


def main():
    """main"""
    hc.initialize()
    hc.network.unset_playing()

    if args.test:
        patterns.test()
        return

    if state == "cleanup":
        hc.clean_up()

    elif state == "off":
        if len(patterns.lights) != cm.hardware.gpio_len:
            for light in patterns.lights:
                patterns.light_off(light)
        else:
            hc.turn_off_lights(use_always_onoff=True)

    elif state == "on":
        if len(patterns.lights) != cm.hardware.gpio_len:
            for light in patterns.lights:
                patterns.light_on(light)
        else:
            hc.turn_on_lights(use_always_onoff=True)

    elif state in Patterns.NAMES:
        print("Press <CTRL>-C to stop")
        patterns.run(state, repeat=True)

    else:
        parser.print_help()


if __name__ == "__main__":

    signal.signal(signal.SIGINT, lambda x, y: sys.exit(0))

    parser = argparse.ArgumentParser()
    parser.add_argument('--state', choices=["off",
                                            "on",
                                            "flash",
                                            "fade",
                                            "random_pattern",
                                            "cylon",
                                            "dance",
                                            "step",
                                            "cleanup"],
                        help='turn off, on, flash, fade, random_pattern, cylon, '
                             'dance, step or cleanup')
    parser.add_argument('--test', action="store_true",
                        help='Run a basic hardware test')
    parser.add_argument('--serve', action="store_true",
                        help='Test mode: read JSON commands from stdin, one per line '
                             '(used by the API)')

    parser.add_argument('--light', default='-1',
                        help='the lights to act on (comma delimited list), -1 for all lights')
    parser.add_argument('--sleep', default=0.5,
                        help='how long to sleep between flashing or fading a light')
    parser.add_argument('--flashes', default=2,
                        help='the number of times to flash or fade each light')
    parser.add_argument('--lights_in_group', default=1,
                        help='number of light in a group')
    parser.add_argument('--pwm_speed', default=0.5,
                        help='time in seconds to full on or off')
    parser.add_argument('--config', default="",
                    help='Config File Override')

    args = parser.parse_args()

    if args.serve:
        # Keep stdout for the JSON protocol; send any stray prints to stderr
        protocol_out = sys.stdout
        sys.stdout = sys.stderr
        signal.signal(signal.SIGTERM, lambda x, y: sys.exit(0))

    hc = Hardware(param_config=args.config)

    state = args.state
    patterns = Patterns(hc,
                        lights=[int(lit) for lit in args.light.split(',')],
                        sleep=args.sleep,
                        flashes=args.flashes,
                        lights_per_group=args.lights_in_group,
                        pwm_speed=args.pwm_speed)

    if args.serve:
        serve(hc, sys.stdin, protocol_out)
    else:
        main()
