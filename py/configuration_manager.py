#!/usr/bin/env python
#
# Licensed under the BSD license.  See full license in LICENSE file.
# http://www.lightshowpi.org/
#
# Author: Todd Giles (todd@lightshowpi.org)
# Author: Tom Enos (tomslick.ca@gmail.com)
#

"""Configuration management for the lightshow.

Configuration files are all located in the <homedir>/config directory. This file contains tools to
manage these configuration files.
"""

import configparser
import ast
import csv
import datetime
import fcntl
import logging
import os
import os.path
import sys
import time
import warnings
import json
import shlex
import argparse
from collections import defaultdict

import virtual_channels

log = logging.getLogger(__name__)

# The home directory and configuration directory for the application.
HOME_DIR = os.getenv("SYNCHRONIZED_LIGHTS_HOME")

if not HOME_DIR:
    print("Need to setup SYNCHRONIZED_LIGHTS_HOME environment variable, "
          "see readme")
    sys.exit()

CONFIG_DIR = HOME_DIR + '/config'
LOG_DIR = HOME_DIR + '/logs'


def _as_list(list_str, delimiter=','):
    """Return a list of items from a delimited string (after stripping whitespace).

    :param list_str: string to turn into a list
    :type list_str: str

    :param delimiter: split the string on this
    :type delimiter: str

    :return: string converted to a list
    :rtype: list
    """
    return [str.strip(item).rstrip() for item in list_str.split(delimiter)]


class Configuration(object):
    """Configuration management for the lightshow.

    Configuration files are all located in the <homedir>/config directory. This file contains tools
    to manage these configuration files.
    """

    def __init__(self, param_config=None):
        self.gpio_len = None
        self.playlist = None
        self.playlist_path = None

        # path and file locations
        self.home_dir = os.getenv("SYNCHRONIZED_LIGHTS_HOME")
        self.config_dir = self.home_dir + "/config/"
        self.log_dir = self.home_dir + "/logs/"
        self.state_file = self.config_dir + "state.cfg"

        self.param_config = param_config

        # ConfigParsers
        self.config = configparser.RawConfigParser(allow_no_value=True)
        self.state = configparser.RawConfigParser()

        self.state_section = 'do_not_modify'

        self.load_config()

        # Ensure state file has been created
        if not os.path.isfile(self.state_file):
            open(self.state_file, 'w').close()

        self.load_state()

        # Initialize all configuration sections
        self.hardware = None
        self.lightshow = None
        self.audio_processing = None
        self.network = None
        self.terminal = None
        self.led = None
        self.configs = None
        self.outputs = []

        self.set_hardware()
        self.set_configs()
        self.set_lightshow()
        self.set_audio_processing()
        self.set_network()
        self.set_terminal()
        self.set_custom_sequences()
        self.set_outputs()

    def set_outputs(self):
        """Collect ``[output:<name>]`` sections (Nanoleaf, ...) as raw dicts.

        Each needs ``type``; ``enabled = false`` skips it. The output's own
        class parses and validates the rest (see py/outputs/).
        """
        outputs = []
        for section in self.config.sections():
            if not section.lower().startswith("output:"):
                continue
            values = {k: (v or "") for k, v in self.config.items(section)}
            name = section.split(":", 1)[1].strip()
            if values.get("enabled", "true").strip().lower() in ("0", "false", "no", "off"):
                log.info("output %s disabled in config", name)
                continue
            values["name"] = name
            outputs.append(values)
        self.outputs = outputs

    def load_config(self):
        """Load config files into ConfigParser instance"""
        self.config.read_file(open(self.config_dir + 'defaults.cfg'))

        overrides = list()
        if self.param_config:
            # Handle both absolute paths (e.g., from tests) and relative paths
            if os.path.isabs(self.param_config):
                overrides.append(self.param_config)
            else:
                overrides.append(self.config_dir + self.param_config)
        else:
            overrides.append(self.config_dir + "overrides.cfg")
        self.config.read(overrides)

    # handle the program state / next 3 methods
    def load_state(self):
        """Force the state to be reloaded form disk."""
        with open(self.state_file) as state_fp:
            fcntl.lockf(state_fp, fcntl.LOCK_SH)
            self.state.read_file(state_fp, self.state_file)
            # Stat under the shared lock so no writer can slip in between
            # the read and the signature refresh_state() compares against.
            self._state_signature = self._stat_state_file()
            fcntl.lockf(state_fp, fcntl.LOCK_UN)
        self._state_loaded_at = time.monotonic()

    # Upper bound on how stale refresh_state() may leave self.state. Covers two
    # writes landing in the same filesystem timestamp tick with the same size.
    STATE_MAX_AGE_S = 1.0

    def _stat_state_file(self):
        """Return a cheap change signature for the state file, or None if missing."""
        try:
            st = os.stat(self.state_file)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def refresh_state(self):
        """Reload the state file only if it changed or STATE_MAX_AGE_S has passed.

        The show loop calls this once per audio chunk (~20x/s). A stat() is much
        cheaper than open + lock + parse, so this keeps play_now responsive
        without re-parsing an unchanged file every chunk.

        :return: True if the state was reloaded
        :rtype: bool
        """
        loaded_at = getattr(self, '_state_loaded_at', None)
        if (loaded_at is not None
                and time.monotonic() - loaded_at < self.STATE_MAX_AGE_S
                and self._stat_state_file() == getattr(self, '_state_signature', None)):
            return False
        self.load_state()
        return True

    def get_state(self, name, default=""):
        """
        Get application state

        Return the value of a specific application state variable, or the specified
        default if not able to load it from the state file

        :param name: option to load from state file
        :type name: str

        :param default: return if not able to load option from state file
        :type default: str

        :return: the current state
        :rtype: str
        """
        try:
            return self.state.get(self.state_section, name)
        except (configparser.NoOptionError, configparser.NoSectionError):
            return default

    def update_state(self, name, value):
        """Update the application state (name / value pair)

        :param name: option name to update
        :type name: str

        :param value: value to update option name to
        :type value: str
        """
        self.update_states({name: value})

    def update_states(self, values):
        """Update several application state values in a single locked write.

        The state file is shared by several processes (synchronized_lights,
        the button manager and the API). To avoid lost updates the file is
        locked exclusively, re-read from disk, updated, and rewritten while
        the lock is held.

        :param values: mapping of option name -> value (values are stored as str)
        :type values: dict
        """
        values = {name: str(value) for name, value in values.items()}
        if not values:
            return
        logging.info('Updating application state %s', values)

        # O_CREAT so a missing state file is recreated rather than raising
        fd = os.open(self.state_file, os.O_RDWR | os.O_CREAT, 0o644)
        with os.fdopen(fd, 'r+') as state_fp:
            fcntl.lockf(state_fp, fcntl.LOCK_EX)
            try:
                fresh = configparser.RawConfigParser()
                state_fp.seek(0)
                fresh.read_file(state_fp, self.state_file)

                if not fresh.has_section(self.state_section):
                    fresh.add_section(self.state_section)

                for name, value in values.items():
                    fresh.set(self.state_section, name, value)

                state_fp.seek(0)
                state_fp.truncate()
                fresh.write(state_fp)
                state_fp.flush()
            finally:
                fcntl.lockf(state_fp, fcntl.LOCK_UN)

        self.state = fresh

    def set_configs(self):

        configs = dict()

        if self.config.get('configs', 'led_config') == "":
            configs["led"] = list()
        else:
            configs["led"] = self.config.get('configs', 'led_config').split(",")

        configs["led_multiprocess"] = self.config.getboolean('configs','led_multiprocess')

        self.configs = Section(configs)

    def set_hardware(self):
        """
        Retrieves the hardware configuration parsing it from the Config Parser as necessary.
        """
        hrdwr = dict()
        devices = dict()
        try:
            devices = json.loads(self.config.get('hardware', 'devices'))
        except Exception as error:
            logging.error("devices not defined or not in JSON format." + str(error))
        hrdwr["devices"] = devices

        for device_type, settings in hrdwr["devices"].items():
            for count in range(len(settings)):
                for k, v in settings[count].items():
                    settings[count][k] = v

        g_pins = self.config.get('hardware', 'gpio_pins')
        try:
            hrdwr["gpio_pins"] = list(map(int, g_pins.split(",")))
        except (AttributeError, ValueError):
            hrdwr["gpio_pins"] = list()

        self.gpio_len = len(hrdwr["gpio_pins"])

        hrdwr["gpio_len"] = len(hrdwr["gpio_pins"])
        hrdwr["physical_gpio_len"] = hrdwr["gpio_len"]

        temp = self.config.get('hardware', 'pin_modes').split(",")
        if len(temp) != 1:
            hrdwr["pin_modes"] = temp
        else:
            hrdwr["pin_modes"] = [temp[0] for _ in range(self.gpio_len)]

        hrdwr["is_pin_pwm"] = [True if pin == "pwm" else False for pin in hrdwr["pin_modes"]]

        hrdwr["pwm_range"] = int(self.config.get('hardware', 'pwm_range'))
        hrdwr["piglow"] = self.config.getboolean('hardware', 'piglow')

        temp = self.config.get('hardware', 'active_low_mode').split(",")
        if len(temp) != 1:
            hrdwr["active_low_mode"] = [self.config._convert_to_boolean(t) for t in temp]
        else:
            hrdwr["active_low_mode"] = [self.config._convert_to_boolean(temp[0]) for _ in range(self.gpio_len)]

        # Virtual channels are added later by add_virtual_channels(), after the
        # LED configs (which may EXTEND gpio_len) are loaded. Until then the
        # base count is simply gpio_len (base_channel_count is set there; read
        # it as getattr(hardware, 'base_channel_count', hardware.gpio_len)).
        hrdwr["virtual_channel_count"] = 0
        hrdwr["virtual_channel_sources"] = []
        hrdwr["virtual_channels_added"] = False

        self.hardware = Section(hrdwr)

    def add_virtual_channels(self):
        """Append ``[hardware] virtual_channels`` non-physical show channels.

        Call once after every LED config has been loaded (LED EXTEND changes
        gpio_len first). Virtual channels come after all other columns, get
        placeholder pins 2000+i (EXTEND uses 1000+) and are never driven by
        GPIO. Records ``base_channel_count`` (gpio_len before) and
        ``virtual_channel_count``; ``physical_gpio_len`` is unchanged.

        Idempotent: later calls change nothing. With ``virtual_channels = 0``
        (the default) no hardware list is touched.

        :return: number of virtual channels
        :rtype: int
        """
        hw = self.hardware
        if getattr(hw, "virtual_channels_added", False):
            return hw.virtual_channel_count

        base = hw.gpio_len
        count = virtual_channels.parse_count(
            self.config.get('hardware', 'virtual_channels', fallback=''))
        sources = virtual_channels.parse_sources(
            self.config.get('hardware', 'virtual_channel_source', fallback=''), count, base)

        hw.set_value("base_channel_count", base)
        hw.set_value("virtual_channel_count", count)
        hw.set_value("virtual_channel_sources", sources)
        hw.set_value("virtual_channels_added", True)
        if not count:
            return 0

        def extended(values, fill, virtual):
            """First ``base`` entries (padded with ``fill``), then one per virtual channel."""
            values = list(values)[:base]
            values += [fill] * (base - len(values))
            return values + [virtual(i) for i in range(count)]

        hw.set_value("gpio_pins", extended(hw.gpio_pins, -1,
                                           lambda i: virtual_channels.PIN_BASE + i))
        hw.set_value("pin_modes", extended(hw.pin_modes, "pwm", lambda i: "pwm"))
        hw.set_value("is_pin_pwm", extended(hw.is_pin_pwm, True, lambda i: True))
        # EXTEND does not extend active_low_mode, so pad it to the full length
        hw.set_value("active_low_mode", extended(hw.active_low_mode, False, lambda i: False))
        hw.set_value("gpio_len", base + count)
        log.info("Added %d virtual show channels (%d-%d), mirroring %s in FFT mode",
                 count, base, base + count - 1, sources)
        return count

    def channel_layout(self):
        """Ranges of show channels by kind, as half-open ``[start, end)`` lists.

        ``physical``: GPIO channels; ``led_extend``: channels after them up to
        the base count (LED EXTEND, or LEDONLY's LED channels), else None;
        ``virtual``: channels added by add_virtual_channels(), else None.
        """
        hw = self.hardware
        physical = hw.physical_gpio_len
        base = getattr(hw, "base_channel_count", hw.gpio_len)
        return {
            "gpio_len": hw.gpio_len,
            "physical": [0, physical],
            "led_extend": [physical, base] if base > physical else None,
            "virtual": [base, hw.gpio_len] if hw.gpio_len > base else None,
        }

    def set_terminal(self):
        """
        Retrieves the terminal configuration parsing it from the Config Parser as necessary.
        """
        term = dict()
        term["enabled"] = self.config.getboolean('terminal', 'enabled')
        self.terminal = Section(term)

    def set_custom_sequences(self):
        scs = dict()
        scs["timing"] = self.config.getint('custom_sequences', 'timing')
        scs["brightness_range"] = self.config.getint('custom_sequences', 'brightness_range')
        self.custom_sequences = Section(scs)

    def set_led(self, config_file):
        """
        Retrieves the led configuration parsing it from the Config Parser as necessary.
        """

        self.led_config = configparser.RawConfigParser(allow_no_value=True)
        self.led_config.read_file(open(self.config_dir + config_file))

        led = dict()

        lc = self.led_config.get('led', 'led_configuration').upper()
        lconn = self.led_config.get('led', 'led_connection').upper()
        st = self.led_config.get('led', 'strip_type').upper()
        sst = ["APA102", "LPD8806", "WS2801", "WS2811", "WS2812",
               "WS2812B", "NEOPIXEL", "WS2811_400", "APA104",
               "TM1803", "TM1804", "TM1809", "UCS1903", "SM16716",
               "LPD1886", "P9813"]

        if lconn in ["SERIAL", "SPI", "SACN"]:
            led["led_configuration"] = lc
            led["led_connection"] = lconn
        else:
            led["led_configuration"] = None
            led["led_connection"] = None

        if st in sst[0:5] and lconn == "SPI":
            led["strip_type"] = st
        elif lconn in ["SERIAL"] and st in sst:
            led["strip_type"] = st
        else:
            led["strip_type"] = None

        led["enable_multicast"] = self.led_config.getboolean('led', 'enable_multicast')

        # if multicast is enabled setup broadcast flag and broadcast address
        # TODO: possibly need to manage broadcast address a little differently in future to handle more customization
        if led["enable_multicast"]:
            led["sacn_address"] = "239.255.0.1"
            led["sacn_broadcast"] = True
        else:
            led["sacn_address"] = self.led_config.get('led', 'sacn_address')
            led["sacn_broadcast"] = False

        led["sacn_port"] = self.led_config.getint('led', 'sacn_port')
        led["universe_boundary"] = self.led_config.getint('led', 'universe_boundary')
        led["universe_start"] = self.led_config.getint('led', 'universe_start')

        c_order = self.led_config.get('led', 'channel_order').upper()
        if c_order in ["RGB", "RBG", "GRB", "GBR", "BRG", "BGR"]:
            led["channel_order"] = c_order
        else:
            led["channel_order"] = "RGB"

        led_count = self.led_config.getint('led', 'led_channel_count')

        g_leds = self.led_config.get('led', 'custom_per_channel')
        try:
            led["custom_per_channel"] = list(map(int, g_leds.split(",")))
            led["led_channel_count"] = len(led["custom_per_channel"])
            led_count = len(led["custom_per_channel"])
        except (AttributeError, ValueError, TypeError ):
            led["custom_per_channel"] = list()
            led["led_channel_count"] = led_count

        led["led_channel_configuration"] = self.led_config.get('led', 'led_channel_configuration').upper()

        led_count = self.led_config.getint('led', 'led_channel_count')
        led["led_channel_count"] = led_count
        if led["led_configuration"]:

            led["led_count"] = led_count
            gpio_len = self.hardware.get("gpio_len")

            if led["led_channel_configuration"] == "MIRROR":
                led["led_count"] = gpio_len

            elif led["led_channel_configuration"] == "LEDONLY":
                self.hardware.set_value("gpio_len", led_count)
                self.hardware.set_value("physical_gpio_len", 0)

                if led_count > gpio_len:
                    gpio_pins = self.hardware.get("gpio_pins")
                    gpio_pins.extend([_ + 1000 for _ in range(led_count - gpio_len)])
                    self.hardware.set_value("gpio_pins", gpio_pins)

                    pin_modes = self.hardware.get("pin_modes")
                    pin_modes.extend(["pwm" for _ in range(led_count - gpio_len)])
                    self.hardware.set_value("pin_modes", pin_modes)

                    is_pin_pwm = self.hardware.get("is_pin_pwm")
                    is_pin_pwm.extend([True for _ in range(led_count - gpio_len)])
                    self.hardware.set_value("is_pin_pwm", is_pin_pwm)

            elif led["led_channel_configuration"] == "EXTEND":
                self.hardware.set_value("gpio_len", gpio_len + led_count)
                self.hardware.set_value("physical_gpio_len", gpio_len)

                gpio_pins = self.hardware.get("gpio_pins")
                gpio_pins.extend([_ + 1000 for _ in range(led_count)])
                self.hardware.set_value("gpio_pins", gpio_pins)

                pin_modes = self.hardware.get("pin_modes")
                pin_modes.extend(["pwm" for _ in range(led_count)])
                self.hardware.set_value("pin_modes", pin_modes)

                is_pin_pwm = self.hardware.get("is_pin_pwm")
                is_pin_pwm.extend([True for _ in range(led_count)])
                self.hardware.set_value("is_pin_pwm", is_pin_pwm)
        else:
            led["led_count"] = 0

        led["max_brightness"] = self.led_config.getint('led', 'max_brightness')
        led["per_channel"] = self.led_config.getint('led', 'per_channel')

        led["pattern_color_map"] = self.led_config.get('led', 'pattern_color_map').upper()
        led["pattern_color"] = list(map(int, self.led_config.get('led', 'pattern_color').split(",")))
        led["pattern_type"] = self.led_config.get('led', 'pattern_type').upper()

        device_id = self.led_config.getint('led', 'device_id')
        if 0 <= device_id <= 255:
            led["device_id"] = device_id
        else:
            led["device_id"] = None

        led["device_address"] = self.led_config.get('led', 'device_address')
        led["hardware_id"] = self.led_config.get('led', 'hardware_id')
        if led["hardware_id"] == "":
            led["hardware_id"] = "1D50:60AB"
        led["baud_rate"] = self.led_config.getint('led', 'baud_rate')
        led["update_throttle"] = self.led_config.getint('led', 'update_throttle')
        # Draw and send LED frames on their own thread so a slow render or a
        # network error never stalls the audio loop. max_fps 0 = uncapped.
        led["threaded"] = self.led_config.getboolean('led', 'threaded', fallback=True)
        led["max_fps"] = self.led_config.getfloat('led', 'max_fps', fallback=30.0)

        led["matrix_width"] = self.led_config.getint('led', 'matrix_width')
        led["matrix_height"] = self.led_config.getint('led', 'matrix_height')
        led["matrix_pattern_type"] = self.led_config.get('led', 'matrix_pattern_type').upper().split(",")

        file_name = self.led_config.get('led', 'image_path').replace('$SYNCHRONIZED_LIGHTS_HOME',
                                                                 self.home_dir)
        if os.path.isfile(file_name):
            led["image_path"] = file_name
        else:
            led["image_path"] = self.home_dir + "/config/resources/16x16star8chan.gif"

        led["multiprocess"] = False

        led["beats"] = self.led_config.getint('led', 'beats')

        led["banner_text"] = self.led_config.get('led', 'banner_text').replace('"','')

        self.led = Section(led)

    def set_network(self):
        """
        Retrieves the network configuration parsing it from the Config Parser as necessary.
        """
        ntwrk = dict()
        ntwrk["networking"] = self.config.get('network', 'networking')
        ntwrk["ip_clients"] = list(map(str, self.config.get('network', 'ip_clients').split(",")))
        ntwrk["port"] = self.config.getint('network', 'port')
        ntwrk["buffer"] = self.config.getint('network', 'buffer')
        ntwrk["get_overrides"] = self.config.getboolean('network', 'get_overrides')

        # sACN (E1.31) Pi-to-Pi networking (networking = sacn_server / sacn_client)
        ntwrk["sacn_address"] = self.config.get('network', 'sacn_address', fallback='').strip()
        ntwrk["sacn_port"] = self.config.getint('network', 'sacn_port', fallback=5568)
        ntwrk["sacn_control_port"] = self.config.getint('network', 'sacn_control_port', fallback=8889)
        ntwrk["universe_start"] = self.config.getint('network', 'universe_start', fallback=100)
        ntwrk["universe_boundary"] = self.config.getint('network', 'universe_boundary', fallback=512)
        ntwrk["enable_multicast"] = self.config.getboolean('network', 'enable_multicast', fallback=False)
        ntwrk["sacn_priority"] = self.config.getint('network', 'sacn_priority', fallback=100)

        if len(self.config.get('network', 'channels')) == 0:
            channels = [_ for _ in range(self.gpio_len)]
        else:
            channels = list(map(int, self.config.get('network', 'channels').split(",")))

        # Removing this because it doesn't handle multiple network channels mapped to same server channel
        # Basically it ends up overwriting the key in the dict
        # We're going to keep this as is and handle later
        #temp = defaultdict()
        #for channel in range(len(channels)):
        #    temp[channels[channel]] = channel

        ntwrk["channels"] = channels

        self.network = Section(ntwrk)

    def set_lightshow(self):
        """
        Retrieve the lightshow configuration loading and parsing it from a file as necessary.
        """
        lghtshw = dict()
        ls = 'lightshow'
        lghtshw["mode"] = self.config.get(ls, 'mode', fallback='playlist')
        lghtshw["use_fifo"] = self.config.getboolean(ls, 'use_fifo', fallback=False)
        lghtshw["fifo"] = "/tmp/audio"
        lghtshw["audio_in_card"] = self.config.get(ls, 'audio_in_card', fallback='default')
        lghtshw["audio_out_card"] = self.config.get(ls, 'audio_out_card', fallback='default')

        if lghtshw["use_fifo"]:
            lghtshw["audio_out_card"] = ""

        lghtshw["input_channels"] = self.config.getint(ls, 'input_channels', fallback=1)
        lghtshw["input_sample_rate"] = self.config.getint(ls, 'input_sample_rate', fallback=48000)

        lghtshw["songname_command"] = self.config.get(ls, 'songname_command', fallback='')

        command_string = self.config.get(ls, 'stream_command_string',
                                         fallback='sudo mpg123 --stdout http://193.34.51.115:80')
        lghtshw["stream_command_string"] = shlex.split(command_string)

        lghtshw["stream_song_delim"] = self.config.get(ls, 'stream_song_delim',
                                                       fallback='ICY-META: StreamTitle=')
        lghtshw["stream_song_exit_count"] = self.config.getint(ls, 'stream_song_exit_count', fallback=0)

        playlist_path = self.config.get(ls, 'playlist_path',
                                        fallback='$SYNCHRONIZED_LIGHTS_HOME/music/sample/.playlist')
        playlist_path = playlist_path.replace('$SYNCHRONIZED_LIGHTS_HOME', self.home_dir)
        if playlist_path:
            lghtshw["playlist_path"] = playlist_path
        else:
            lghtshw["playlist_path"] = "/home/pi/music/.playlist"

        # Per-song variant sequences: <sequences_dir>/<song>/<variant>.csv
        sequences_dir = (self.config.get(ls, 'sequences_dir', fallback='') or '').strip()
        if not sequences_dir:
            sequences_dir = '$SYNCHRONIZED_LIGHTS_HOME/music/sequences'
        lghtshw["sequences_dir"] = sequences_dir.replace('$SYNCHRONIZED_LIGHTS_HOME',
                                                         self.home_dir or '')
        # Let the API play a song once with a chosen variant (state sequence_once)
        lghtshw["allow_sequence_audition"] = self.config.getboolean(
            ls, 'allow_sequence_audition', fallback=True)

        lghtshw["randomize_playlist"] = self.config.getboolean(ls, 'randomize_playlist', fallback=False)

        on_c = "always_on_channels"
        lghtshw[on_c] = list(map(int, self.config.get(ls, on_c, fallback="-1").split(",")))
        off_c = "always_off_channels"
        lghtshw[off_c] = list(map(int, self.config.get(ls, off_c, fallback="-1").split(",")))
        ic = "invert_channels"
        lghtshw[ic] = list(map(int, self.config.get(ls, ic, fallback="-1").split(",")))

        # setup up preshow
        preshow = None
        preshow_configuration = self.config.get(ls, 'preshow_configuration', fallback='')
        preshow_script = self.config.get(ls, 'preshow_script', fallback='')

        if preshow_configuration and not preshow_script:
            try:
                preshow = json.loads(preshow_configuration)
            except (ValueError, TypeError) as error:
                msg = "Preshow_configuration not defined or not in JSON format."
                logging.error(msg + str(error))
        else:
            preshow = self._resolve_show_script('preshow_script', preshow_script)

        lghtshw['preshow'] = preshow

        # setup postshow
        postshow = None
        postshow_configuration = self.config.get(ls, 'postshow_configuration', fallback='')
        postshow_script = self.config.get(ls, 'postshow_script', fallback='')

        if postshow_configuration and not postshow_script:
            try:
                postshow = json.loads(postshow_configuration)
            except (ValueError, TypeError) as error:
                msg = "Postshow_configuration not defined or not in JSON format."
                logging.error(msg + str(error))
        else:
            postshow = self._resolve_show_script('postshow_script', postshow_script)

        lghtshw['postshow'] = postshow
        lghtshw["decay_factor"] = self.config.getfloat(ls, 'decay_factor', fallback=0.0)
        lghtshw["attenuate_pct"] = self.config.getfloat(ls, 'attenuate_pct', fallback=0.0)
        lghtshw["light_delay"] = self.config.getfloat(ls, 'light_delay', fallback=0.0)
        lghtshw["light_delay_legacy_math"] = self.config.getboolean(
            ls, 'light_delay_legacy_math', fallback=True)

        lghtshw["log_level"] = self.config.get(ls, 'log_level', fallback='INFO').upper()

        # Standard Deviation
        lghtshw["SD_low"] = self.config.getfloat(ls, 'SD_low', fallback=0.5)
        lghtshw["SD_high"] = self.config.getfloat(ls, 'SD_high', fallback=0.75)

        self.lightshow = Section(lghtshw)

    def set_audio_processing(self):
        """
        Retrieve the audio processing configuration loading and parsing it from a file as necessary.
        """
        audio_prcssng = dict()
        audio_prcssng["use_gpu"] = self.config.getboolean('audio_processing', 'use_gpu')
        audio_prcssng["chunk_size"] = self.config.getint('audio_processing', 'chunk_size')
        audio_prcssng["min_frequency"] = \
            self.config.getfloat('audio_processing', 'min_frequency')
        audio_prcssng["max_frequency"] = \
            self.config.getfloat('audio_processing', 'max_frequency')
        temp = self.config.get('audio_processing', 'custom_channel_mapping')
        audio_prcssng["custom_channel_mapping"] = \
            list(map(int, temp.split(','))) if temp else 0
        temp = self.config.get('audio_processing', 'custom_channel_frequencies')
        audio_prcssng["custom_channel_frequencies"] = \
            list(map(int, temp.split(','))) if temp else 0

        self.audio_processing = Section(audio_prcssng)

    def _resolve_show_script(self, key, path):
        """Return a pre/post-show script path, or None.

        Expands $SYNCHRONIZED_LIGHTS_HOME (as image_path does) so configs don't
        need absolute paths, and warns instead of silently skipping a
        configured script that doesn't exist.
        """
        if not path:
            return None
        path = path.replace('$SYNCHRONIZED_LIGHTS_HOME', self.home_dir or '')
        if os.path.isfile(path):
            return path
        log.warning("%s = %s not found; it will not run", key, path)
        return None

    def get_playlist(self, play_list=None):
        play_list = play_list or self.playlist_path

        # Check if playlist file exists
        if not os.path.exists(play_list):
            log.error(f"Playlist file not found: {play_list}")
            print(f"\n{'='*70}")
            print(f"ERROR: Playlist file not found!")
            print(f"{'='*70}")
            print(f"Looking for: {play_list}")
            print(f"\nPlease either:")
            print(f"  1. Create the playlist file with your songs")
            print(f"  2. Update config/overrides.cfg with a valid playlist_path")
            print(f"  3. Use --playlist option: bin/start_music_and_lights --playlist <file>")
            print(f"\nPlaylist format (tab-separated):")
            print(f"  Song Name<TAB>path/to/song.mp3")
            print(f"{'='*70}\n")
            sys.exit(1)

        try:
            songs = read_playlist(play_list, on_invalid=_report_invalid_playlist_entry)
        except Exception as e:
            log.error(f"Error reading playlist file: {e}")
            print(f"\nERROR: Failed to read playlist file: {play_list}")
            print(f"Error: {e}\n")
            sys.exit(1)

        self.playlist = songs

        return songs

    def set_playlist(self, songs):
        self.playlist = songs

    def write_playlist(self, songs, playlist=None):
        playlist = playlist or self.playlist_path

        with open(playlist, 'wt') as playlist_fp:
            fcntl.lockf(playlist_fp, fcntl.LOCK_EX)
            writer = csv.writer(playlist_fp, delimiter='\t')

            for song in songs:
                if len(song[2]) > 0:
                    song[2] = ",".join(song[2])
                else:
                    del song[2]

            writer.writerows(songs)
            fcntl.lockf(playlist_fp, fcntl.LOCK_UN)

        self.playlist = songs

    # SMS-related methods (has_permission, is_throttle_exceeded) removed


def _report_invalid_playlist_entry(song):
    """CLI output for a playlist line that read_playlist() skipped."""
    print("Error found in playlist")
    print("Deleting entry:", song)


def read_playlist(path, on_invalid=None):
    """Parse a tab-separated playlist file without ever exiting the process.

    Each line is ``name<TAB>path[<TAB>votes[<TAB>playing!]]``. Lines with fewer
    than 2 or more than 4 fields are logged and skipped (``on_invalid`` is
    called with the raw fields). The votes column becomes a set; a missing one
    becomes an empty set.

    The show reads playlists through Configuration.get_playlist(), which adds
    the exit and help text; the API calls this directly because it must never
    exit.

    :param path: playlist file
    :param on_invalid: optional callback for each skipped line
    :return: list of ``[name, path, votes(set)[, "playing!"]]`` entries
    :raises OSError: when the file can't be opened
    """
    songs = list()
    with open(path, 'rt') as playlist_fp:
        fcntl.lockf(playlist_fp, fcntl.LOCK_SH)
        try:
            for song in csv.reader(playlist_fp, delimiter='\t'):
                if len(song) < 2 or len(song) > 4:
                    log.error('Invalid playlist.  Each line should be in the form: '
                              '<song name><tab><path to song>')
                    log.warning('Removing invalid entry')
                    if on_invalid is not None:
                        on_invalid(song)
                    continue

                elif len(song) > 2:
                    song[2] = set(song[2].split(','))

                elif len(song) == 2:
                    song.append(set())

                songs.append(song)
        finally:
            fcntl.lockf(playlist_fp, fcntl.LOCK_UN)
    return songs


def load_channel_config(config_dir, load_leds=True):
    """Resolve show channels from a config dir the way Hardware does, read-only.

    Reads defaults.cfg + overrides.cfg, loads every ``[configs] led_config``
    (when ``load_leds``: Hardware only loads them when led_module imports; a
    strip that later fails to start still counts, as in Hardware), then adds
    the virtual channels. Never touches state.cfg, so the API can call it on
    every request without the in-process Configuration growing gpio_len.

    :param config_dir: directory holding defaults.cfg
    :param load_leds: whether the LED configs extend the channels
    :return: (Configuration, LED entries, LED config error strings). Each LED
        entry is {name, channel_configuration, physical_gpio_len, gpio_len}
        as they stood right after that config loaded (what LedOutput gets).
    """
    c = Configuration.__new__(Configuration)
    c.gpio_len = None
    c.param_config = None
    c.config_dir = os.path.join(config_dir, "")
    c.home_dir = os.path.dirname(os.path.normpath(config_dir))
    c.config = configparser.RawConfigParser(allow_no_value=True)
    c.load_config()
    c.led = None
    c.outputs = []
    c.set_hardware()
    c.set_configs()
    c.set_outputs()
    leds, errors = [], []
    if load_leds:
        for lc in c.configs.led:
            try:
                c.set_led(config_file=lc)
            except Exception as error:  # Hardware would not start with this config
                errors.append("%s: %s" % (lc, error))
                continue
            leds.append({"name": lc,
                         "channel_configuration": c.led.led_channel_configuration,
                         "physical_gpio_len": c.hardware.physical_gpio_len,
                         "gpio_len": c.hardware.gpio_len})
    c.add_virtual_channels()
    return c, leds, errors


class Section(object):
    def __init__(self, config):
        self.config = config
        self.set_values(self.config)

    def set_config(self, config):
        self.config = config
        self.set_values(self.config)

    def get_config(self):
        return self.config

    def set_value(self, key, value):
        setattr(self, key, value)

    def set_values(self, dict_of_items):
        """Create class instance variables from key, value pairs

        :param dict_of_items: a dict containing key, value pairs to set
        :type dict_of_items: dict
        """
        for key, value in dict_of_items.items():
            setattr(self, key, value)

    def get(self, item):
        """Get class instance variables from string

        :param item:
        :type item: str

        :return: object of item type
        :rtype: object
        """
        return getattr(self, item)


if __name__ == "__main__":
    # prints the current configuration

    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=None, help='Config File Override')
    args = parser.parse_args()

    cm = Configuration(param_config=args.config)

    if args.config:
        print("Configuration File:", args.config, "\n")
    elif os.path.isfile(CONFIG_DIR + '/overrides.cfg'):
        print("Configuration File: overrides.cfg\n")
    else:
        print("Configuration File: defaults.cfg\n")

    print("Home directory set:", HOME_DIR)
    print("Config directory set:", CONFIG_DIR)
    print("Logs directory set:", LOG_DIR)

    print("\nHardware Configuration")
    for h_key, h_value in cm.hardware.config.items():
        print(h_key, "=", h_value)

    print("\nLightshow Configuration")
    for l_key, l_value in cm.lightshow.config.items():
        print(l_key, "=", l_value)

    print("\nAudio Processing Configuration")
    for a_key, a_value in cm.audio_processing.config.items():
        print(a_key, "=", a_value)

    print("\nNetwork Configuration")
    for nkey, nvalue in cm.network.config.items():
        print(nkey, "=", nvalue)

    print("\nLED Configuration")
    for lc in cm.configs.led:
        cm.set_led(config_file=lc)
        for led_key, led_value in cm.led.config.items():
            print(led_key, "=", led_value)

    print("\nTerminal Configuration")
    for tkey, tvalue in cm.terminal.config.items():
        print(tkey, "=", tvalue)
