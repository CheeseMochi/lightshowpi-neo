"""
sACN (E1.31) pixel sender for LED outputs.

``SacnSender`` splits a frame of RGB bytes into DMX universes and sends one
E1.31 packet per universe over UDP (unicast, or broadcast/multicast when
enabled). It doesn't depend on BiblioPixel.

Originally a BiblioPixel driver by Ken B (K5ENB), based on
bibliopixel2/drivers/network_udp.py (MIT licence, Copyright (c) 2016 Maniacal
Labs, LLC / Adam M. Haile). The packet layout is unchanged; the BiblioPixel
``DriverBase`` parent is gone.

Planned: move to the maintained ``sacn`` package
(https://pypi.org/project/sacn/, MIT) after the 2026 season. It wasn't adopted
yet because its sender runs its own fixed-rate thread (use ``manual_flush``
and ``flush()`` per frame to keep show timing) and its CI didn't cover Python
3.13 at the time. Keep ``SacnSender.send`` as the seam so the swap stays local.
"""

import logging
import socket
import struct
import time

from e131packet import E131Packet

log = logging.getLogger(__name__)

SACN_PORT = 5568
MAX_UNIVERSE_SIZE = 512

# Log a failing send at most this often, so a receiver that's offline for the
# whole show doesn't flood the log at 30+ frames per second.
ERROR_LOG_INTERVAL_S = 30.0


def split_universes(data, universe_start, universe_size):
    """
    Split a frame into ``[(universe, chunk), ...]`` in ascending universe order.

    Each universe carries up to ``universe_size`` bytes. Use a multiple of 3
    (e.g. 510) so no pixel's RGB is split across two universes.
    """
    if not 1 <= universe_size <= MAX_UNIVERSE_SIZE:
        raise ValueError(f"universe_size must be 1-{MAX_UNIVERSE_SIZE}, got {universe_size}")
    data = bytes(data)
    return [(universe_start + i, data[offset:offset + universe_size])
            for i, offset in enumerate(range(0, len(data), universe_size))]


class SacnSender(object):
    """Sends RGB frames to an sACN receiver (e.g. an ESPixelStick)."""

    def __init__(self, host, port=SACN_PORT, universe=1, universe_boundary=MAX_UNIVERSE_SIZE,
                 broadcast=False, source_name="LightShowPi"):
        self.host = host
        self.port = port
        self.universe = universe
        self.universe_boundary = universe_boundary
        self.broadcast = broadcast
        self.source_name = source_name
        self.sequence = 0
        self._sock = None
        self._last = None
        self._last_error_log = 0.0
        self.errors = 0

        if not host:
            raise ValueError("sACN LED output needs sacn_address (or enable_multicast = True)")
        if universe_boundary % 3:
            log.warning(f"universe_boundary = {universe_boundary} is not a multiple of 3: it "
                        "must match the receiver's universe size exactly, or pixels after the "
                        "first universe show the wrong colours. 510 on both keeps pixels whole.")

    def _socket(self):
        if self._sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if self.broadcast:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack('b', 1))
            sock.settimeout(0.2)
            self._sock = sock
        return self._sock

    def send(self, data):
        """
        Send one frame of RGB bytes. Returns True when sent.

        An unchanged frame isn't re-sent. Network errors are logged
        (rate-limited) and return False instead of raising, so a receiver that
        drops off Wi-Fi never stops the show; the next frame retries.
        """
        data = bytes(data)
        if data == self._last:
            return False
        try:
            sock = self._socket()
            for universe, chunk in split_universes(data, self.universe, self.universe_boundary):
                packet = E131Packet(name=self.source_name, universe=universe, data=chunk,
                                    sequence=self.sequence)
                sock.sendto(packet.packet_data, (self.host, self.port))
        except OSError as e:
            self.errors += 1
            self._last = None
            self.close()
            now = time.monotonic()
            if now - self._last_error_log >= ERROR_LOG_INTERVAL_S:
                self._last_error_log = now
                log.warning(f"sACN send to {self.host}:{self.port} failed ({e}); "
                            f"{self.errors} failed frames so far, will keep retrying")
            return False
        self._last = data
        self.sequence = (self.sequence + 1) % 256
        return True

    def close(self):
        """Close the socket; the next send reopens it."""
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None


# BiblioPixel adapter, only for the legacy LED paths (SPI, SERIAL) in
# led_bibliopixel.py. Defined only when BiblioPixel is installed.
try:
    from bibliopixel.drivers.driver_base import DriverBase
except ImportError:
    DriverBase = None

if DriverBase is not None:
    class DriverSACN(DriverBase):
        """BiblioPixel driver that sends through ``SacnSender``."""

        def __init__(self, num=0, width=0, height=0, host="localhost", broadcast=False,
                     port=SACN_PORT, universe=1, universe_boundary=MAX_UNIVERSE_SIZE,
                     broadcast_interface=''):
            super(DriverSACN, self).__init__(num, width, height)
            self._sender = SacnSender(host, port=port, universe=universe,
                                      universe_boundary=universe_boundary, broadcast=broadcast)

        def _compute_packet(self):
            self._render()

        def _send_packet(self):
            self._sender.send(self._buf)
