"""NanoleafOutput: drive Nanoleaf Essentials (e.g. NL73K3 Permanent Outdoor
Lights) from show frames using per-LED UDP stream control (extControl v2).

Always wrap it in ThreadedOutput (Hardware does): HTTP calls and Wi-Fi sends
happen on the output's own thread, never in the audio loop.

Mapping: the strip is split into one segment per show channel listed in
``channels``. Each segment shows its colour scaled by that channel's level.

Recovery: stream mode is (re)enabled on the first frame and every
``reassert_s`` seconds, so lights that were power-cycled or that dropped off
Wi-Fi rejoin the show by themselves. Any HTTP/socket error raises, which
ThreadedOutput logs and retries with back-off.

Protocol details and sources: py/nanoleaf_protocol.py.
"""
import colorsys
import logging
import time

import nanoleaf_protocol as nl
from outputs.base import Output

log = logging.getLogger(__name__)

COLOR_ORDERS = ("rgb", "rbg", "grb", "gbr", "brg", "bgr")


def parse_bool(value, default=False):
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def rainbow(n):
    """n evenly spaced, fully saturated colours (like the LED FREQ1 map)."""
    out = []
    for i in range(n):
        r, g, b = colorsys.hsv_to_rgb(i / max(n, 1), 1.0, 1.0)
        out.append((int(r * 255), int(g * 255), int(b * 255)))
    return out


def parse_color(text):
    """'#ff8000', 'ff8000' or '255,128,0' -> (r, g, b)."""
    text = text.strip().lstrip("#")
    if "," in text or " " in text:
        parts = [int(p) for p in text.replace(",", " ").split()]
    else:
        parts = [int(text[i:i + 2], 16) for i in (0, 2, 4)]
    if len(parts) != 3:
        raise ValueError(f"bad colour {text!r}")
    return tuple(max(0, min(255, p)) for p in parts)


def segments(n_leds, n_segments):
    """Split LED indexes 0..n_leds-1 into n_segments contiguous, near-equal ranges."""
    bounds = [round(i * n_leds / n_segments) for i in range(n_segments + 1)]
    return [range(bounds[i], bounds[i + 1]) for i in range(n_segments)]


class NanoleafOutput(Output):
    """One Nanoleaf device, streamed per LED."""

    max_fps = 10
    latency_s = 0.0

    def __init__(self, host, token, channels, port=nl.DEFAULT_HTTP_PORT,
                 stream_port=nl.DEFAULT_STREAM_PORT, leds=None, colors=None,
                 brightness=None, reverse=False, color_order="rgb", max_fps=10,
                 transition=0, restore=False, reassert_s=30.0, http_timeout=2.0,
                 name="nanoleaf", client_factory=nl.NanoleafClient,
                 sender_factory=nl.StreamSender, clock=time.monotonic):
        if not host:
            raise ValueError("nanoleaf output needs host")
        if not token:
            raise ValueError("nanoleaf output needs token (pair with tools/nanoleaf_probe.py --pair)")
        if not channels:
            raise ValueError("nanoleaf output needs at least one channel")
        color_order = (color_order or "rgb").lower()
        if color_order not in COLOR_ORDERS:
            raise ValueError(f"color_order must be one of {', '.join(COLOR_ORDERS)}")
        self.name = name
        self.host = host
        self.channels = list(channels)
        self.leds = int(leds) if leds else None
        self.colors = list(colors) if colors else rainbow(len(self.channels))
        if len(self.colors) < len(self.channels):
            # Too few colours: repeat them
            self.colors = [self.colors[i % len(self.colors)] for i in range(len(self.channels))]
        self.brightness = brightness
        self.reverse = reverse
        self.color_order = color_order
        self.max_fps = max_fps or None
        self.transition = int(transition)
        self.restore = restore
        self.reassert_s = reassert_s
        self.stream_port = stream_port
        self._client = client_factory(host, port, token, http_timeout)
        self._sender_factory = sender_factory
        self._sender = None
        self._clock = clock
        self._layout = nl.LAYOUTS["v2"]
        self._segments = None
        self._streaming_since = None
        self._snapshot = None

    @classmethod
    def from_config(cls, name, section, gpio_len):
        """Build from an ``[output:<name>]`` section (dict of raw strings).

        Inline ``# comments`` after a value are ignored (the config loader
        keeps them as part of the value), except in ``colors``, whose values
        use ``#rrggbb``.
        """
        section = {k: str(v).strip() if k == "colors"
                   else str(v).split(" #", 1)[0].split("\t#", 1)[0].strip()
                   for k, v in section.items()}
        channels_text = section.get("channels", "").strip()
        channels = [int(c) for c in channels_text.split(",")] if channels_text \
            else list(range(gpio_len))
        colors_text = section.get("colors", "").strip()
        colors = [parse_color(c) for c in colors_text.split(";")] if colors_text else None
        brightness = section.get("brightness", "").strip()
        return cls(
            host=section.get("host", "").strip(),
            token=section.get("token", "").strip() or cls._saved_token(section),
            channels=channels,
            port=int(section.get("port") or nl.DEFAULT_HTTP_PORT),
            leds=section.get("leds", "").strip() or None,
            colors=colors,
            brightness=int(brightness) if brightness else None,
            reverse=parse_bool(section.get("reverse"), False),
            color_order=section.get("color_order", "rgb"),
            max_fps=float(section.get("max_fps") or 10),
            transition=int(section.get("transition") or 0),
            restore=parse_bool(section.get("restore"), False),
            name="nanoleaf:%s" % name,
        )

    @staticmethod
    def _saved_token(section):
        host = section.get("host", "").strip()
        _, entry = nl.lookup_device(host, section.get("token_file", "").strip() or None)
        return (entry or {}).get("auth_token")

    # -- Output interface (called on the ThreadedOutput worker) ---------------

    def start(self):
        if self._sender is None:
            self._sender = self._sender_factory(self.host, self.stream_port)

    def write_frame(self, levels):
        self._ensure_streaming()
        colors = []
        for seg_index, channel in enumerate(self.channels):
            level = float(levels[channel]) if 0 <= channel < len(levels) else 0.0
            level = 0.0 if level < 0 else 1.0 if level > 1 else level
            r, g, b = self.colors[seg_index]
            colors.append((int(r * level), int(g * level), int(b * level)))
        self._send_segment_colors(colors)

    def all_on(self):
        self._ensure_streaming()
        self._send_segment_colors(self.colors[:len(self.channels)])

    def all_off(self):
        self._ensure_streaming()
        self._send_segment_colors([(0, 0, 0)] * len(self.channels))

    def close(self):
        try:
            if self._streaming_since is not None:
                self._send_segment_colors([(0, 0, 0)] * len(self.channels))
        except Exception as err:
            log.warning("%s: blackout on close failed: %s", self.name, err)
        if self.restore and self._snapshot is not None:
            try:
                nl.restore_state(self._client, self._snapshot)
            except Exception as err:
                log.warning("%s: restoring previous state failed: %s", self.name, err)
        self._streaming_since = None
        if self._sender is not None:
            self._sender.close()
            self._sender = None

    # -- internals -------------------------------------------------------------

    def _ensure_streaming(self):
        """(Re)enable stream control on first use and every reassert_s."""
        if self._sender is None:
            self.start()
        now = self._clock()
        if self._streaming_since is not None and now - self._streaming_since < self.reassert_s:
            return
        first = self._streaming_since is None
        if first:
            if self.restore and self._snapshot is None:
                self._snapshot = nl.capture_state(self._client)
            if self.leds is None:
                self.leds = self._client.length()
                if not self.leds:
                    raise nl.NanoleafError("could not read LED count (GET /length)")
            self._segments = segments(self.leds, len(self.channels))
            self._check(self._client.set_state(on=True), "turn on")
            if self.brightness is not None:
                self._check(self._client.set_state(brightness=self.brightness), "set brightness")
        self._check(self._client.enable_ext_control("v2"), "enable stream control")
        if first:
            log.info("%s: streaming to %s, %d LEDs in %d segments", self.name, self.host,
                     self.leds, len(self.channels))
        self._streaming_since = now

    @staticmethod
    def _check(response, what):
        if not (200 <= response.status < 300):
            raise nl.NanoleafError(f"{what} failed: HTTP {response.status} {response.raw[:80]!r}")

    def _order(self, rgb):
        r, g, b = rgb
        by = {"r": r, "g": g, "b": b}
        return tuple(by[c] for c in self.color_order)

    def _send_segment_colors(self, colors):
        pixels = []
        last = self.leds - 1
        for seg, rgb in zip(self._segments, colors):
            r, g, b = self._order(rgb)
            for i in seg:
                led = last - i if self.reverse else i
                pixels.append((led, r, g, b))
        packets = nl.encode_packets(self._layout, pixels, self.transition,
                                    max_payload=nl.SAFE_UDP_PAYLOAD)
        self._sender.send(packets)
