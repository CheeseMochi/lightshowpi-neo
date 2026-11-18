#!/usr/bin/env python
#
# Licensed under the BSD license.  See full license in LICENSE file.
# http://www.lightshowpi.org/
#
# Author: LightShowPi Neo Contributors

"""sACN (E1.31) network controller for GPIO channels with hybrid control plane.

This module provides sACN/E1.31 protocol support for distributing GPIO
brightness data across multiple Raspberry Pi controllers, plus a separate
control plane for configuration messages (overrides, commands, etc).

Architecture:
- Data Plane: sACN E1.31 on port 5568 for high-frequency brightness updates
- Control Plane: JSON over UDP on port 8889 for low-frequency config changes

Key features:
- Sequence number tracking (detect out-of-order packets)
- Standard E1.31 protocol (compatible with professional tools)
- Multicast and unicast support
- Multiple universe support (512 channels per universe)
- Separate control channel for overrides (always_on, always_off, inverted)
- JSON control messages (safe alternative to pickle)
"""

import socket
import struct
import logging as log
import sys
import json
import time
from collections import namedtuple

import numpy as np

import networking

# Import existing E1.31 packet implementation
from e131packet import E131Packet

# Named tuples for GPIO channel data
SACNGPIOData = namedtuple('SACNGPIOData', ['universe', 'sequence', 'dmx_data'])


class SACNNetworking(object):
    """sACN E1.31 network controller for GPIO channels with control plane.

    Handles two separate communication channels:
    1. Data plane (sACN): High-frequency brightness updates
    2. Control plane (JSON/UDP): Low-frequency configuration changes
    """

    def __init__(self, cm):
        """Initialize sACN networking with control plane.

        Args:
            cm: Configuration manager instance
        """
        self.cm = cm

        # Network configuration
        self.networking = cm.network.networking
        self.sacn_address = getattr(cm.network, 'sacn_address', '')
        self.sacn_port = getattr(cm.network, 'sacn_port', 5568)
        self.sacn_control_port = getattr(cm.network, 'sacn_control_port', 8889)
        self.universe_start = getattr(cm.network, 'universe_start', 1)
        self.universe_boundary = getattr(cm.network, 'universe_boundary', 512)
        self.enable_multicast = getattr(cm.network, 'enable_multicast', False)
        self.sacn_priority = getattr(cm.network, 'sacn_priority', 100)

        # Unicast targets: every address in sacn_address (comma separated)
        self.target_addresses = [a.strip() for a in (self.sacn_address or '').split(',') if a.strip()]

        # Last levels sent, so a single-pin update (pre/post-show) can be sent
        # as a full frame: sACN has no "one channel" message
        self._levels = np.zeros(cm.hardware.gpio_len, dtype='float32')

        # Last levels received (client), across all universes
        self._rx_levels = np.zeros(0, dtype='float32')

        # State tracking
        self.playing = False
        self.ready = False
        self.sequence_num = 0
        self.last_sequence = {}  # Track per universe

        # Network streams
        self.network_stream = None  # Data plane (sACN)
        self.control_stream = None  # Control plane (JSON)

        # Channel configuration
        self.channels = cm.network.channels
        self.num_channels = cm.hardware.gpio_len
        # Client: apply per-song overrides sent by the server (same as networking.py)
        self.get_overrides = bool(getattr(cm.network, 'get_overrides', False))

        self.setup()

    def setup(self):
        """Setup as either sACN server or client"""
        if self.networking == "sacn_server":
            self.setup_server()
        elif self.networking == "sacn_client":
            self.setup_client()

        self.ready = True

    def setup_server(self):
        """Setup sACN sender (server broadcasts to clients)"""

        # ===== DATA PLANE (sACN E1.31) =====

        # Determine target address
        if self.enable_multicast:
            # E1.31 multicast address for universe N is 239.255.0.N
            self.target_address = f"239.255.{(self.universe_start >> 8) & 0xFF}.{self.universe_start & 0xFF}"
            log.info(f"sACN server mode - multicast to {self.target_address}:{self.sacn_port}")
        elif self.target_addresses:
            # Unicast to every client (recommended on Wi-Fi: unicast frames are
            # acknowledged and retried, broadcast/multicast frames are not)
            self.target_address = self.target_addresses[0]
            log.info(f"sACN server mode - unicast to {', '.join(self.target_addresses)}:{self.sacn_port}")
        else:
            # Broadcast fallback
            self.target_address = '<broadcast>'
            log.info(f"sACN server mode - broadcast on port {self.sacn_port}")

        try:
            self.network_stream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.network_stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            if self.enable_multicast:
                # Set multicast TTL (1 = local subnet)
                ttl = struct.pack('b', 1)
                self.network_stream.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
            else:
                # Enable broadcast
                self.network_stream.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            log.info(f"sACN server initialized - universe {self.universe_start}, port {self.sacn_port}")
            print(f"sACN data plane: {self.target_address}:{self.sacn_port}")

        except socket.error as e:
            log.error(f'Failed to create sACN socket: {e}')
            print(f"Error creating sACN socket: {e}")
            sys.exit(1)

        # ===== CONTROL PLANE (JSON over UDP) =====

        try:
            self.control_stream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.control_stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.control_stream.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            log.info(f"Control plane initialized - port {self.sacn_control_port}")
            print(f"sACN control plane: port {self.sacn_control_port}")

        except socket.error as e:
            log.error(f'Failed to create control socket: {e}')
            print(f"Warning: Control plane unavailable: {e}")
            self.control_stream = None

    def setup_client(self):
        """Setup sACN receiver (client listens for broadcasts)"""

        log.info("sACN client mode starting")
        print("sACN client mode starting...")

        # ===== DATA PLANE (sACN E1.31) =====

        try:
            self.network_stream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.network_stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            if self.enable_multicast:
                # Join multicast group
                self.network_stream.bind(('', self.sacn_port))

                # Calculate multicast address for our universe
                mcast_addr = f"239.255.{(self.universe_start >> 8) & 0xFF}.{self.universe_start & 0xFF}"
                mreq = struct.pack('4sL', socket.inet_aton(mcast_addr), socket.INADDR_ANY)
                self.network_stream.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

                log.info(f"Joined multicast group {mcast_addr}")
                print(f"sACN data plane: multicast {mcast_addr}:{self.sacn_port}")
            else:
                # Bind to port for broadcast/unicast
                self.network_stream.bind(('', self.sacn_port))
                print(f"sACN data plane: port {self.sacn_port}")

            log.info(f"Client channels mapped as\n{str(self.channels)}")

        except socket.error as e:
            log.error(f'Failed to create sACN socket: {e}')
            print(f"Error creating sACN socket: {e}")
            if self.network_stream:
                self.network_stream.close()
            sys.exit(1)

        # ===== CONTROL PLANE (JSON over UDP) =====

        try:
            self.control_stream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.control_stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.control_stream.bind(('', self.sacn_control_port))
            # Non-blocking: checked once per received frame, must not add latency
            self.control_stream.setblocking(False)

            log.info(f"Control plane listening on port {self.sacn_control_port}")
            print(f"sACN control plane: port {self.sacn_control_port}")

        except socket.error as e:
            log.error(f'Failed to create control socket: {e}')
            print(f"Warning: Control plane unavailable: {e}")
            self.control_stream = None

    def close_connection(self):
        """Close both data and control streams"""
        self.ready = False

        if self.network_stream:
            self.network_stream.close()
            self.network_stream = None

        if self.control_stream:
            self.control_stream.close()
            self.control_stream = None

    def broadcast(self, *args):
        """Send light data to clients via sACN (data plane).

        Accepts what Hardware sends to any network controller, so the show
        code doesn't need to know which transport is in use:

        - ``BcastArray`` / ndarray / list: a full frame of levels (0.0-1.0)
        - ``BcastPin``: one channel changed (pre/post-show); the full frame is
          resent with that channel updated
        - ``BcastOverrides``: forwarded to the control plane
        """
        if not args:
            return
        payload = args[0]

        if isinstance(payload, networking.BcastOverrides):
            self.broadcast_overrides(always_off=payload.always_off,
                                     always_on=payload.always_on,
                                     inverted=payload.inverted)
            return
        if isinstance(payload, networking.BcastPin):
            if 0 <= payload.pin < len(self._levels):
                self._levels[payload.pin] = payload.brightness
            levels = self._levels
        else:
            if isinstance(payload, networking.BcastArray):
                payload = payload.array
            levels = np.asarray(payload, dtype='float32').ravel()
            if len(levels) == len(self._levels):
                self._levels[:] = levels
            else:
                self._levels = levels.copy()

        self.send_levels(levels)

    def send_levels(self, brightness_array):
        """Encode levels (0.0-1.0) as DMX (0-255) and send them as E1.31."""
        if self.network_stream is None:
            log.debug("sACN stream closed, skipping broadcast")
            return

        # Convert brightness (0.0-1.0) to DMX (0-255)
        dmx_data = [int(min(max(float(b), 0.0), 1.0) * 255) for b in brightness_array]

        # Calculate number of universes needed
        num_channels = len(dmx_data)
        num_universes = max(1, (num_channels + self.universe_boundary - 1) // self.universe_boundary)

        targets = self.target_addresses if (self.target_addresses and not self.enable_multicast) \
            else [self.target_address]

        # Send data across one or more universes
        for univ_idx in range(num_universes):
            universe = self.universe_start + univ_idx

            # Extract DMX data for this universe
            start_idx = univ_idx * self.universe_boundary
            end_idx = min(start_idx + self.universe_boundary, num_channels)
            universe_data = dmx_data[start_idx:end_idx]

            # Pad to at least 1 channel (E1.31 requirement)
            if not universe_data:
                universe_data = [0]

            packet = E131Packet(
                name='LightShowPi',
                universe=universe,
                data=universe_data,
                sequence=self.sequence_num
            ).packet_data

            for target in targets:
                try:
                    self.network_stream.sendto(packet, (target, self.sacn_port))
                except OSError as e:
                    # A client off Wi-Fi must never stop the show; 9 = closed at shutdown
                    if e.errno != 9:
                        log.error(f"sACN send to {target} failed: {e}")

        # Increment sequence number (0-255, wraps)
        self.sequence_num = (self.sequence_num + 1) % 256

    def broadcast_overrides(self, always_off=[], always_on=[], inverted=[]):
        """Send override configuration to clients via control plane.

        Uses JSON over UDP for safety and simplicity.

        Args:
            always_off: List of channel numbers to force off
            always_on: List of channel numbers to force on
            inverted: List of channel numbers to invert
        """
        if self.control_stream is None:
            log.debug("Control stream not available, skipping override broadcast")
            return

        # Create control message
        message = {
            'type': 'overrides',
            'always_off': always_off,
            'always_on': always_on,
            'inverted': inverted,
            'timestamp': time.time()
        }

        try:
            data = json.dumps(message).encode('utf-8')

            # Send to all configured clients
            if self.target_addresses:
                for addr in self.target_addresses:
                    self.control_stream.sendto(data, (addr, self.sacn_control_port))
                    log.info(f"Sent overrides to {addr}:{self.sacn_control_port}")
            else:
                # Broadcast to all clients
                self.control_stream.sendto(data, ('<broadcast>', self.sacn_control_port))
                log.info(f"Broadcast overrides on port {self.sacn_control_port}")

        except Exception as e:
            log.error(f"Failed to send override message: {e}")

    # E1.31 section 6.7.2: a packet whose sequence number is 1-20 behind the
    # last one accepted is out of order and is discarded.
    SEQUENCE_REJECT_WINDOW = 20

    def receive(self):
        """Receive one E1.31 packet and return its levels.

        Returns:
            ``(levels,)`` with ``levels`` a float32 ndarray (0.0-1.0) of the
            channels the server sent, or None for an invalid, out-of-order or
            timed-out packet. Clients map server channels with
            ``[network] channels``; anything the server didn't send reads as 0.
        """
        try:
            data, address = self.network_stream.recvfrom(1144)
        except socket.timeout:
            return None
        except OSError as e:
            log.error(f"Error receiving sACN packet: {e}")
            return None

        parsed = self.parse_e131_packet(data)
        if parsed is None:
            return None

        universe = parsed['universe']
        if not (self.universe_start <= universe < self.universe_start + 64):
            # Someone else's universe (e.g. an LED controller's) on the same port
            return None

        sequence = parsed['sequence']
        last_seq = self.last_sequence.get(universe)
        if last_seq is not None:
            behind = (last_seq - sequence) % 256
            if 0 < behind <= self.SEQUENCE_REJECT_WINDOW:
                log.debug(f"Dropping out-of-order sACN packet: universe={universe}, "
                          f"seq={sequence} after {last_seq}")
                return None
        self.last_sequence[universe] = sequence

        # Multi-universe frames: place this universe's slice at its offset
        offset = (universe - self.universe_start) * self.universe_boundary
        dmx = np.asarray(parsed['dmx_data'], dtype='float32') / 255.0
        needed = offset + len(dmx)
        if len(self._rx_levels) < needed:
            grown = np.zeros(needed, dtype='float32')
            grown[:len(self._rx_levels)] = self._rx_levels
            self._rx_levels = grown
        # Trailing zeros were trimmed by the parser: clear the rest of this universe
        end = min(offset + self.universe_boundary, len(self._rx_levels))
        self._rx_levels[offset:end] = 0.0
        self._rx_levels[offset:needed] = dmx

        return (self._rx_levels.copy(),)

    def receive_control_message(self):
        """Receive control messages from control plane (non-blocking).

        Returns:
            Dict with message data, or None if no message
        """
        if self.control_stream is None:
            return None

        try:
            data, addr = self.control_stream.recvfrom(1024)
            message = json.loads(data.decode('utf-8'))

            # Validate message structure
            if 'type' not in message:
                log.warning(f"Control message missing 'type' field from {addr}")
                return None

            log.debug(f"Received control message type '{message['type']}' from {addr}")
            return message

        except (socket.timeout, BlockingIOError):
            # Expected - non-blocking receive
            return None
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            log.error(f"Invalid JSON in control message: {e}")
            return None
        except Exception as e:
            log.error(f"Error receiving control message: {e}")
            return None

    def parse_e131_packet(self, data):
        """Parse E1.31 packet and extract DMX data.

        Args:
            data: Raw UDP packet bytes

        Returns:
            Dict with universe, sequence, dmx_data, or None if invalid
        """
        try:
            # E1.31 packet structure (simplified parsing)
            # We only need to extract the essential fields

            # Check minimum packet size
            if len(data) < 126:
                log.debug("Packet too short for E1.31")
                return None

            # Check preamble (should be 0x0010)
            preamble = struct.unpack('!H', data[0:2])[0]
            if preamble != 0x0010:
                log.debug(f"Invalid E1.31 preamble: {preamble:#x}")
                return None

            # Check ACN packet identifier
            acn_id = data[4:16]
            if acn_id != b'ASC-E1.17\x00\x00\x00':
                log.debug("Invalid ACN packet identifier")
                return None

            # Extract universe (offset 113-114, big-endian uint16)
            universe = struct.unpack('!H', data[113:115])[0]

            # Extract sequence number (offset 111)
            sequence = data[111]

            # Extract DMX data
            # DMP layer starts around offset 115
            # DMX start code at offset 125 (should be 0x00)
            # DMX data starts at offset 126
            dmx_start_code = data[125]
            if dmx_start_code != 0x00:
                log.debug(f"Invalid DMX start code: {dmx_start_code:#x}")
                return None

            # DMX data (rest of packet after start code)
            dmx_data = list(data[126:])

            # Remove any trailing zeros (padding)
            # Keep at least 1 value
            while len(dmx_data) > 1 and dmx_data[-1] == 0:
                dmx_data.pop()

            return {
                'universe': universe,
                'sequence': sequence,
                'dmx_data': dmx_data
            }

        except Exception as e:
            log.error(f"Error parsing E1.31 packet: {e}")
            return None

    def set_playing(self):
        """Set playing flag (for compatibility with networking.py)"""
        self.playing = True

    def unset_playing(self):
        """Unset playing flag (for compatibility with networking.py)"""
        self.playing = False


# Convenience function for testing
def test_sacn_parsing():
    """Test E1.31 packet parsing with a sample packet"""
    from e131packet import E131Packet

    # Create a test packet
    test_data = [255, 128, 64, 0, 32, 96, 160, 224]
    packet = E131Packet(
        name='Test',
        universe=1,
        data=test_data,
        sequence=42
    )

    # Mock CM object
    class MockCM:
        class network:
            networking = 'sacn_server'
            sacn_address = ''
            sacn_port = 5568
            sacn_control_port = 8889
            universe_start = 1
            universe_boundary = 512
            enable_multicast = False
            sacn_priority = 100
            channels = []
        class hardware:
            gpio_len = 8

    # Create networking instance (without sockets)
    sacn = SACNNetworking.__new__(SACNNetworking)
    sacn.cm = MockCM()

    # Parse test packet
    parsed = sacn.parse_e131_packet(packet.packet_data)

    print(f"Test packet parsing:")
    print(f"  Universe: {parsed['universe']} (expected 1)")
    print(f"  Sequence: {parsed['sequence']} (expected 42)")
    print(f"  DMX Data: {parsed['dmx_data']}")
    print(f"  Expected: {test_data}")
    print(f"  Match: {parsed['dmx_data'] == test_data}")


if __name__ == "__main__":
    # Run test if executed directly
    print("Testing sACN packet parsing...")
    test_sacn_parsing()
