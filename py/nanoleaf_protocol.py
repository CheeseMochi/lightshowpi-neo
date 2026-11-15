"""Nanoleaf local Open API helpers: HTTP client, token store, mDNS, stream frames.

Stdlib only, no imports from other LightShowPi modules and no import-time side
effects, so it can be shared by tools/nanoleaf_probe.py and a future
py/outputs/nanoleaf.py output driver.

Sources (implemented from the vendor spec, not copied from any GPL code):

* Nanoleaf "Matter WiFi Essentials Open API" (covers NL73K3 Permanent Outdoor
  Lights, firmware > 3.0.10):
  https://nanoleaf.atlassian.net/wiki/spaces/nlapid/pages/2296381472/
  - mDNS service ``_nanoleafapi._tcp``; pairing ``POST /api/v1/new`` within 30 s
    of tapping "Connect to API" in the app (doc says "204 No Content" but also
    shows a ``{"auth_token": ...}`` body, so any 2xx is accepted here).
  - ``GET /api/v1/<token>/length`` -> ``{"numLEDs": 300}``.
  - Stream control: ``PUT /api/v1/<token>/effects`` with
    ``{"write": {"command": "display", "animType": "extControl",
    "extControlVersion": "v2"}}``, then UDP frames to port 60222.
  - Frame: nPanels(2B) then per LED: id(2B) R G B W(1B each) transition(2B),
    all multi-byte fields big endian, W always 0, transition in 100 ms units,
    LED id == index 0..N-1, any subset of LEDs may be sent per frame.
  - NOTE: the doc's worked example is internally inconsistent: header is
    ``00 04`` for three LED records, and LED 128 ("red") is encoded as
    ``00 FF FF 00``. Records for LED 23 and 282 do match the table. We follow
    the table (count == number of records).
* LedFx device driver at the commit that fixed Essentials support
  (LedFx issue #1850 / PR #1853, tested by the reporter on NL71K2 fw 4.0.12):
  https://github.com/LedFx/LedFx/blob/06837150e32bc173236c81d2f929553500783b05/ledfx/devices/nanoleaf.py
  It sends v2 as ``>H`` count + ``>HBBBBH`` (id, r, g, b, w=0, transition=0)
  for every LED in a single datagram (no MTU splitting), HTTP port 16021,
  refresh 30 fps. ``v2`` below is byte-identical to that.
* Light Panels (NL22) extControl v1, as documented in the original Nanoleaf
  OpenAPI (forum.nanoleaf.me/docs) and implemented by OpenRGB's
  NanoleafController.cpp: nPanels(1B) then per panel: panelId(1B) nFrames(1B,
  always 1) R G B W T (1B each, T in 100 ms units). Only ids 0..255 fit.
"""

import json
import os
import random
import socket
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_HTTP_PORT = 16021
DEFAULT_STREAM_PORT = 60222
MDNS_SERVICE = "_nanoleafapi._tcp.local"
MDNS_ADDR = ("224.0.0.251", 5353)
DEFAULT_TOKEN_PATH = os.path.join("~", ".config", "lightshowpi", "nanoleaf.json")

# Largest UDP payload that fits one 1500-byte Ethernet MTU without IP fragmentation.
SAFE_UDP_PAYLOAD = 1472

Pixel = Tuple[int, int, int, int]  # (led_id, r, g, b)


# --------------------------------------------------------------------------
# Stream frame encoding (pure functions)
# --------------------------------------------------------------------------

def _clamp8(v: int) -> int:
    return 0 if v < 0 else 255 if v > 255 else int(v)


def encode_frame_v2(pixels: Iterable[Pixel], transition: int = 0) -> bytes:
    """Essentials / Light Panels extControl v2 frame (big endian).

    nPanels(H) + per pixel: id(H) R(B) G(B) B(B) W(B)=0 transition(H).
    """
    pixels = list(pixels)
    if len(pixels) > 0xFFFF:
        raise ValueError("too many pixels for one v2 frame")
    out = bytearray(struct.pack(">H", len(pixels)))
    for led_id, r, g, b in pixels:
        if not 0 <= led_id <= 0xFFFF:
            raise ValueError(f"LED id {led_id} out of range for v2")
        out += struct.pack(">HBBBBH", led_id, _clamp8(r), _clamp8(g), _clamp8(b), 0,
                           transition & 0xFFFF)
    return bytes(out)


def encode_frame_v1(pixels: Iterable[Pixel], transition: int = 1) -> bytes:
    """Light Panels extControl v1 frame (all 1-byte fields).

    nPanels(B) + per panel: id(B) nFrames(B)=1 R G B W=0 T.
    """
    pixels = list(pixels)
    if len(pixels) > 0xFF:
        raise ValueError("too many pixels for one v1 frame (max 255)")
    out = bytearray([len(pixels)])
    for led_id, r, g, b in pixels:
        if not 0 <= led_id <= 0xFF:
            raise ValueError(f"LED id {led_id} out of range for v1")
        out += bytes([led_id, 1, _clamp8(r), _clamp8(g), _clamp8(b), 0, transition & 0xFF])
    return bytes(out)


@dataclass(frozen=True)
class Layout:
    name: str
    ext_control_version: str  # value for "extControlVersion"
    header_size: int
    record_size: int
    max_id: int
    default_transition: int
    description: str

    def encode(self, pixels: Iterable[Pixel], transition: Optional[int] = None) -> bytes:
        t = self.default_transition if transition is None else transition
        if self.name == "v1":
            return encode_frame_v1(pixels, t)
        return encode_frame_v2(pixels, t)

    def max_records(self, max_payload: int) -> int:
        return max(1, (max_payload - self.header_size) // self.record_size)


LAYOUTS: Dict[str, Layout] = {
    "v2": Layout("v2", "v2", 2, 8, 0xFFFF, 0,
                 "Essentials spec / LedFx: >H count, >HBBBBH id,R,G,B,W,trans"),
    "v1": Layout("v1", "v1", 1, 7, 0xFF, 1,
                 "Light Panels v1: B count, id,nFrames=1,R,G,B,W,T (1 byte each)"),
}


def colors_to_pixels(colors: Sequence[Tuple[int, int, int]], first_id: int = 0) -> List[Pixel]:
    """Turn a dense list of RGB tuples into (id, r, g, b) pixels starting at first_id."""
    return [(first_id + i, c[0], c[1], c[2]) for i, c in enumerate(colors)]


def encode_packets(layout: Layout, pixels: Sequence[Pixel], transition: Optional[int] = None,
                   max_payload: Optional[int] = None) -> List[bytes]:
    """Encode pixels into one datagram, or several if max_payload is given.

    Pixels with ids above the layout's max id are dropped (v1 can only address 0..255).
    Splitting is legal per the Essentials spec (any subset of LEDs per frame).
    """
    pixels = [p for p in pixels if p[0] <= layout.max_id]
    if layout.name == "v1":
        pixels = pixels[:255]
    if not max_payload:
        return [layout.encode(pixels, transition)]
    n = layout.max_records(max_payload)
    return [layout.encode(pixels[i:i + n], transition) for i in range(0, max(len(pixels), 1), n)]


def decode_frame_v2(data: bytes) -> List[Tuple[int, int, int, int, int, int]]:
    """Inverse of encode_frame_v2: list of (id, r, g, b, w, transition). For tests/debug."""
    (count,) = struct.unpack_from(">H", data, 0)
    return [struct.unpack_from(">HBBBBH", data, 2 + 8 * i) for i in range(count)]


# --------------------------------------------------------------------------
# Token store
# --------------------------------------------------------------------------

def token_path(path: Optional[str] = None) -> str:
    return os.path.expanduser(path or DEFAULT_TOKEN_PATH)


def load_tokens(path: Optional[str] = None) -> dict:
    p = token_path(path)
    try:
        with open(p) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"devices": {}}
    data.setdefault("devices", {})
    return data


def save_token(host: str, token: str, path: Optional[str] = None, **extra) -> str:
    """Store token for host in a 0600 JSON file (parent dir 0700). Returns the path."""
    p = token_path(path)
    os.makedirs(os.path.dirname(p) or ".", mode=0o700, exist_ok=True)
    data = load_tokens(p)
    entry = data["devices"].get(host, {})
    entry.update(extra)
    entry["auth_token"] = token
    data["devices"][host] = entry
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return p


def lookup_device(host: Optional[str],
                  path: Optional[str] = None) -> Tuple[Optional[str], Optional[dict]]:
    """Return (host, entry). With host=None, pick the only saved device if there is one."""
    devices = load_tokens(path)["devices"]
    if host:
        return host, devices.get(host)
    if len(devices) == 1:
        h, e = next(iter(devices.items()))
        return h, e
    return None, None


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------

class NanoleafError(Exception):
    pass


@dataclass
class Response:
    status: int
    body: object  # parsed JSON, text, or None
    raw: bytes = b""


class NanoleafClient:
    def __init__(self, host: str, port: int = DEFAULT_HTTP_PORT, token: Optional[str] = None,
                 timeout: float = 3.0):
        self.host, self.port, self.token, self.timeout = host, port, token, timeout

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}/api/v1"

    def url(self, path: str = "") -> str:
        if self.token is None:
            raise NanoleafError("no auth token; run --pair first or pass --token")
        return f"{self.base}/{self.token}/{path.lstrip('/')}"

    def request(self, method: str, url: str, body: Optional[dict] = None) -> Response:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read() or b""
        text = raw.decode("utf-8", "replace").strip()
        parsed: object = None
        if text:
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = text
        return Response(status, parsed, raw)

    # --- pairing / info -------------------------------------------------
    def pair(self) -> Response:
        """POST /api/v1/new. Returns raw Response; token is body['auth_token'] on 2xx."""
        return self.request("POST", f"{self.base}/new")

    def info(self) -> Response:
        return self.request("GET", self.url(""))

    def length(self) -> Optional[int]:
        r = self.request("GET", self.url("length"))
        if 200 <= r.status < 300 and isinstance(r.body, dict) and "numLEDs" in r.body:
            return int(r.body["numLEDs"])
        return None

    def layout(self) -> Response:
        return self.request("GET", self.url("panelLayout/layout"))

    # --- state / effects -----------------------------------------------
    def set_state(self, on: Optional[bool] = None, brightness: Optional[int] = None,
                  hue: Optional[int] = None, sat: Optional[int] = None,
                  ct: Optional[int] = None) -> Response:
        body: dict = {}
        if on is not None:
            body["on"] = {"value": bool(on)}
        if brightness is not None:
            body["brightness"] = {"value": int(brightness)}
        if hue is not None:
            body["hue"] = {"value": int(hue)}
        if sat is not None:
            body["sat"] = {"value": int(sat)}
        if ct is not None:
            body["ct"] = {"value": int(ct)}
        return self.request("PUT", self.url("state"), body)

    def selected_effect(self) -> Optional[str]:
        r = self.request("GET", self.url("effects/select"))
        if 200 <= r.status < 300 and isinstance(r.body, str):
            return r.body.strip('"') or None
        return None

    def select_effect(self, name: str) -> Response:
        return self.request("PUT", self.url("effects"), {"select": name})

    def enable_ext_control(self, version: str = "v2") -> Response:
        return self.request("PUT", self.url("effects"), ext_control_body(version))


def ext_control_body(version: str = "v2") -> dict:
    return {"write": {"command": "display", "animType": "extControl",
                      "extControlVersion": version}}


def parse_info(info: dict) -> dict:
    """Flatten the device-info JSON into the fields we care about."""
    state = info.get("state", {}) if isinstance(info, dict) else {}

    def val(k):
        v = state.get(k)
        return v.get("value") if isinstance(v, dict) else v

    return {
        "name": info.get("name"),
        "model": info.get("model"),
        "firmware": info.get("firmwareVersion"),
        "hardware": info.get("hardwareVersion"),
        "serial": info.get("serialNo"),
        "on": val("on"),
        "brightness": val("brightness"),
        "hue": val("hue"),
        "sat": val("sat"),
        "ct": val("ct"),
        "color_mode": state.get("colorMode"),
        "has_panel_layout": "panelLayout" in info,
    }


def capture_state(client: NanoleafClient) -> dict:
    """Snapshot what we need to put the device back the way we found it."""
    r = client.info()
    if not (200 <= r.status < 300 and isinstance(r.body, dict)):
        raise NanoleafError(f"GET info failed: HTTP {r.status} {r.body!r}")
    snap = parse_info(r.body)
    snap["effect"] = client.selected_effect() if snap.get("color_mode") == "effect" else None
    return snap


def restore_state(client: NanoleafClient, snap: dict) -> List[Response]:
    """Best-effort restore: colour/effect first, then brightness, then on/off."""
    out = []
    mode = snap.get("color_mode")
    if mode == "effect" and snap.get("effect") and "extControl" not in str(snap.get("effect")):
        out.append(client.select_effect(snap["effect"]))
    elif mode == "ct" and snap.get("ct") is not None:
        out.append(client.set_state(ct=snap["ct"]))
    elif snap.get("hue") is not None:
        out.append(client.set_state(hue=snap["hue"], sat=snap.get("sat")))
    if snap.get("brightness") is not None:
        out.append(client.set_state(brightness=snap["brightness"]))
    if snap.get("on") is not None:
        out.append(client.set_state(on=snap["on"]))
    return out


# --------------------------------------------------------------------------
# Stream sender
# --------------------------------------------------------------------------

class StreamSender:
    """Thin UDP sender; records per-send wall time for host-side timing stats."""

    def __init__(self, host: str, port: int = DEFAULT_STREAM_PORT):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, packets: Sequence[bytes]) -> float:
        """Send packets, return elapsed seconds spent in sendto()."""
        t0 = time.perf_counter()
        for p in packets:
            self.sock.sendto(p, self.addr)
        return time.perf_counter() - t0

    def close(self):
        self.sock.close()


# --------------------------------------------------------------------------
# mDNS discovery (minimal, stdlib; one-shot "legacy unicast" query)
# --------------------------------------------------------------------------

T_A, T_PTR, T_TXT, T_SRV = 1, 12, 16, 33


def build_mdns_query(service: str = MDNS_SERVICE, qid: int = 0) -> bytes:
    hdr = struct.pack(">HHHHHH", qid, 0, 1, 0, 0, 0)
    q = b"".join(bytes([len(p)]) + p.encode() for p in service.split(".") if p) + b"\x00"
    # QTYPE PTR, QCLASS IN with the unicast-response (QU) bit set.
    return hdr + q + struct.pack(">HH", T_PTR, 0x8001)


def _read_name(data: bytes, off: int) -> Tuple[str, int]:
    labels, jumped, end, hops = [], False, off, 0
    while True:
        if off >= len(data):
            raise ValueError("truncated name")
        n = data[off]
        if n & 0xC0 == 0xC0:
            ptr = ((n & 0x3F) << 8) | data[off + 1]
            if not jumped:
                end = off + 2
            off, jumped, hops = ptr, True, hops + 1
            if hops > 32:
                raise ValueError("name pointer loop")
            continue
        off += 1
        if n == 0:
            break
        labels.append(data[off:off + n].decode("utf-8", "replace"))
        off += n
    return ".".join(labels), (end if jumped else off)


def parse_mdns_records(data: bytes) -> List[dict]:
    """Parse all resource records (answers + authority + additional) of a DNS packet."""
    _, _, qd, an, ns, ar = struct.unpack_from(">HHHHHH", data, 0)
    off = 12
    for _ in range(qd):
        _, off = _read_name(data, off)
        off += 4
    recs = []
    for _ in range(an + ns + ar):
        name, off = _read_name(data, off)
        rtype, _cls, _ttl, rdlen = struct.unpack_from(">HHIH", data, off)
        off += 10
        rd = data[off:off + rdlen]
        rec = {"name": name, "type": rtype}
        if rtype == T_PTR:
            rec["target"], _ = _read_name(data, off)
        elif rtype == T_SRV:
            _prio, _w, port = struct.unpack_from(">HHH", data, off)
            rec["port"] = port
            rec["target"], _ = _read_name(data, off + 6)
        elif rtype == T_A and rdlen == 4:
            rec["address"] = socket.inet_ntoa(rd)
        elif rtype == T_TXT:
            txt, i = {}, 0
            while i < len(rd):
                ln = rd[i]
                item = rd[i + 1:i + 1 + ln].decode("utf-8", "replace")
                k, _, v = item.partition("=")
                if k:
                    txt[k] = v
                i += 1 + ln
            rec["txt"] = txt
        recs.append(rec)
        off += rdlen
    return recs


def summarize_services(records: Iterable[dict], service: str = MDNS_SERVICE) -> List[dict]:
    """Join PTR/SRV/TXT/A records into [{instance, host, address, port, txt}]."""
    records = list(records)
    instances = {r["target"] for r in records
                 if r["type"] == T_PTR and r["name"].lower() == service.lower()}
    addrs: Dict[str, str] = {r["name"].lower(): r["address"] for r in records if r["type"] == T_A}
    out = []
    for inst in sorted(instances):
        srv = next((r for r in records if r["type"] == T_SRV and r["name"] == inst), {})
        txt = next((r["txt"] for r in records if r["type"] == T_TXT and r["name"] == inst), {})
        host = srv.get("target")
        out.append({"instance": inst, "host": host,
                    "address": addrs.get((host or "").lower()),
                    "port": srv.get("port"), "txt": txt})
    return out


def discover(timeout: float = 3.0, service: str = MDNS_SERVICE) -> List[dict]:
    """Send a one-shot mDNS PTR query and collect responses for `timeout` seconds."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.settimeout(0.25)
    qid = random.randint(1, 0xFFFF)
    records: List[dict] = []
    try:
        deadline = time.monotonic() + timeout
        sock.sendto(build_mdns_query(service, qid), MDNS_ADDR)
        resent = False
        while time.monotonic() < deadline:
            if not resent and time.monotonic() > deadline - timeout / 2:
                sock.sendto(build_mdns_query(service, qid), MDNS_ADDR)
                resent = True
            try:
                data, src = sock.recvfrom(9000)
            except socket.timeout:
                continue
            try:
                recs = parse_mdns_records(data)
            except (ValueError, struct.error, IndexError):
                continue
            for r in recs:
                r.setdefault("source", src[0])
            records.extend(recs)
    finally:
        sock.close()
    services = summarize_services(records, service)
    # Fall back to the responder's source IP if no A record was included.
    for s in services:
        if not s["address"]:
            src = next((r.get("source") for r in records
                        if r["type"] == T_SRV and r["name"] == s["instance"]), None)
            s["address"] = src
    return services
