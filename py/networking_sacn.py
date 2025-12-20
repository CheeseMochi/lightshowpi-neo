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
        elif self.sacn_address:
            # Unicast to specific address(es)
            self.target_address = self.sacn_address.split(',')[0].strip()  # First address for data
            log.info(f"sACN server mode - unicast to {self.target_address}:{self.sacn_port}")
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
            self.control_stream.settimeout(0.01)  # Non-blocking with 10ms timeout

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

    def broadcast(self, brightness_array, use_overrides=False):
        """Broadcast GPIO brightness data via sACN (data plane).

        Converts brightness values (0.0-1.0 float) to DMX values (0-255 int)
        and sends via E1.31 protocol.

        Args:
            brightness_array: List of float values (0.0-1.0) for each GPIO channel
            use_overrides: Whether overrides are active (informational only)
        """
        if self.network_stream is None:
            log.debug("sACN stream closed, skipping broadcast")
            return

        # Convert brightness (0.0-1.0) to DMX (0-255)
        dmx_data = [int(min(max(b, 0.0), 1.0) * 255) for b in brightness_array]

        # Calculate number of universes needed
        num_channels = len(dmx_data)
        num_universes = (num_channels + self.universe_boundary - 1) // self.universe_boundary

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

            try:
                # Create E1.31 packet
                packet = E131Packet(
                    name='LightShowPi',
                    universe=universe,
                    data=universe_data,
                    sequence=self.sequence_num
                )

                # Send packet
                self.network_stream.sendto(
                    packet.packet_data,
                    (self.target_address, self.sacn_port)
                )

                log.debug(f"Sent sACN packet: universe={universe}, seq={self.sequence_num}, channels={len(universe_data)}")

            except socket.error as e:
                if e.errno != 9:  # Ignore "Bad file descriptor" during shutdown
                    log.error(f"sACN send error: {e}")

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
            if self.sacn_address:
                # Unicast to specific clients
                for addr in self.sacn_address.split(','):
                    addr = addr.strip()
                    if addr:
                        self.control_stream.sendto(data, (addr, self.sacn_control_port))
                        log.info(f"Sent overrides to {addr}:{self.sacn_control_port}")
            else:
                # Broadcast to all clients
                self.control_stream.sendto(data, ('<broadcast>', self.sacn_control_port))
                log.info(f"Broadcast overrides on port {self.sacn_control_port}")

        except Exception as e:
            log.error(f"Failed to send override message: {e}")

    def receive(self):
        """Receive sACN packet and extract brightness data.

        Returns:
            Tuple of (brightness_array, use_overrides) or None if invalid packet
            brightness_array is list of floats (0.0-1.0)
        """
        try:
            # Receive UDP packet
            data, address = self.network_stream.recvfrom(1024)

            # Parse E1.31 packet
            parsed = self.parse_e131_packet(data)

            if parsed is None:
                return None

            universe = parsed['universe']
            sequence = parsed['sequence']
            dmx_data = parsed['dmx_data']

            # Check sequence number for this universe
            last_seq = self.last_sequence.get(universe, -1)

            # Detect out-of-order packets
            # Allow wraparound (255 -> 0)
            if last_seq >= 0:
                # If new sequence is less than last AND we're not near wraparound
                if sequence < last_seq and last_seq < 250:
                    log.debug(f"Out-of-order packet: universe={universe}, seq={sequence} < {last_seq}")
                    # Still process it, but log the issue

            self.last_sequence[universe] = sequence

            # Convert DMX (0-255) to brightness (0.0-1.0)
            brightness_array = [dmx / 255.0 for dmx in dmx_data]

            # Pad or truncate to match expected channel count
            if len(brightness_array) < self.num_channels:
                brightness_array.extend([0.0] * (self.num_channels - len(brightness_array)))
            elif len(brightness_array) > self.num_channels:
                brightness_array = brightness_array[:self.num_channels]

            # Return as tuple (matches networking.py interface)
            return (brightness_array,)

        except socket.timeout:
            return None
        except Exception as e:
            log.error(f"Error receiving sACN packet: {e}")
            return None

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

        except socket.timeout:
            # Expected - non-blocking receive
            return None
        except json.JSONDecodeError as e:
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
