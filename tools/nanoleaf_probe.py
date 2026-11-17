#!/usr/bin/env python3
"""Nanoleaf probe: find out whether per-LED real-time streaming works on a device.

Written for Nanoleaf Essentials Permanent Outdoor Lights (NL73K3), but works for
any Nanoleaf exposing the local Open API on port 16021. Stdlib only.

Typical session (each step prints what to do next):

    python tools/nanoleaf_probe.py --discover
    python tools/nanoleaf_probe.py --host 192.168.1.50 --pair     # then tap "Connect to API"
    python tools/nanoleaf_probe.py --host 192.168.1.50 --info
    python tools/nanoleaf_probe.py --host 192.168.1.50 --state on --brightness 60 --hue 120
    python tools/nanoleaf_probe.py --host 192.168.1.50 --stream-test
    python tools/nanoleaf_probe.py --stream-test --dry-run --leds 20   # no device needed

--stream-test runs numbered phases (P01, P02, ...) for each candidate UDP frame
layout and frame rate, prints what you should see for each, measures host-side
send timing, and restores the previous state at the end (also on Ctrl-C).
Report which phase IDs looked right.

Protocol details and sources are documented in py/nanoleaf_protocol.py:
  * Nanoleaf Matter WiFi Essentials Open API
    https://nanoleaf.atlassian.net/wiki/spaces/nlapid/pages/2296381472/
  * LedFx Essentials support (issue #1850, PR #1853, commit 0683715)
    https://github.com/LedFx/LedFx/issues/1850
  * Light Panels extControl v1 (Nanoleaf OpenAPI, OpenRGB NanoleafController.cpp)
"""

import argparse
import colorsys
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py"))
import nanoleaf_protocol as nl  # noqa: E402

RGB = Tuple[int, int, int]
OFF: RGB = (0, 0, 0)


# --------------------------------------------------------------------------
# Test patterns: pure functions (n_leds, frame_index) -> list of RGB
# --------------------------------------------------------------------------

def pattern_head(n: int, k: int) -> List[RGB]:
    """LED0 red, LED1 green, LED2 blue, last LED white, rest off."""
    c = [OFF] * n
    for i, col in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255)][:n]):
        c[i] = col
    if n > 3:
        c[-1] = (255, 255, 255)
    return c


def pattern_chase(n: int, k: int, width: int = 10) -> List[RGB]:
    """10-LED red block advancing one LED per frame (wraps)."""
    c = [OFF] * n
    for j in range(min(width, n)):
        c[(k + j) % n] = (255, 0, 0)
    return c


def pattern_rainbow(n: int, k: int) -> List[RGB]:
    """Rainbow across the whole strip, scrolling 2% of the strip per frame."""
    out = []
    for i in range(n):
        r, g, b = colorsys.hsv_to_rgb(((i / max(n, 1)) + k * 0.02) % 1.0, 1.0, 1.0)
        out.append((int(r * 255), int(g * 255), int(b * 255)))
    return out


PATTERNS = {
    "head": (pattern_head,
             "LED0 RED, LED1 GREEN, LED2 BLUE, last LED WHITE, rest off "
             "(checks indexing offset, colour order, LED count)"),
    "chase": (pattern_chase,
              "a 10-LED RED block moving away from LED0, one LED per frame "
              "(speed = fps LEDs/s; jumps = dropped frames)"),
    "rainbow": (pattern_rainbow,
                "full-strip RAINBOW scrolling smoothly (every LED addressed every frame)"),
}


# --------------------------------------------------------------------------
# Phase plan
# --------------------------------------------------------------------------

@dataclass
class Phase:
    pid: str
    layout: str
    fps: int
    pattern: str
    seconds: float
    split: bool = False
    stats: dict = field(default_factory=dict)
    result: str = "pending"

    @property
    def label(self) -> str:
        s = " split-datagrams" if self.split else ""
        return f"[layout {self.layout} @{self.fps}fps{s}] {self.pattern}"


def build_plan(layouts: Sequence[str], fps_list: Sequence[int], n_leds: int,
               scale: float = 1.0, split_payload: int = nl.SAFE_UDP_PAYLOAD) -> List[Phase]:
    """Most likely candidate first. head once per layout at the middle rate,
    chase+rainbow at every rate, and an extra split-datagram pass for v2 if a
    full frame does not fit in one MTU."""
    phases: List[Phase] = []
    fps_list = list(fps_list)
    mid = 10 if 10 in fps_list else fps_list[0]
    ordered = [mid] + [f for f in fps_list if f != mid]
    for lay in layouts:
        for i, fps in enumerate(ordered):
            pats = ["head", "chase", "rainbow"] if i == 0 else ["chase", "rainbow"]
            for p in pats:
                secs = {"head": 3.0, "chase": 5.0, "rainbow": 4.0}[p] * scale
                phases.append(Phase("", lay, fps, p, secs))
        layout = nl.LAYOUTS[lay]
        full = layout.header_size + layout.record_size * min(n_leds, layout.max_id + 1)
        if lay == "v2" and full > split_payload:
            for p in ("chase", "rainbow"):
                phases.append(Phase("", lay, mid, p, 4.0 * scale, split=True))
    for i, ph in enumerate(phases, 1):
        ph.pid = f"P{i:02d}"
    return phases


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t0 = time.monotonic()

    def stamp(self) -> str:
        return f"[+{time.monotonic() - self.t0:6.1f}s {time.strftime('%H:%M:%S')}]"


def log(clock: Clock, msg: str):
    print(f"{clock.stamp()} {msg}", flush=True)


def hexdump(b: bytes, limit: Optional[int]) -> str:
    h = b.hex(" ")
    if limit and len(b) > limit:
        h = b[:limit].hex(" ") + f" ... (+{len(b) - limit} bytes)"
    return h


def pct(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))]


# --------------------------------------------------------------------------
# Device helpers
# --------------------------------------------------------------------------

def http_ok(r: nl.Response) -> bool:
    return 200 <= r.status < 300


class DryClient:
    """Stands in for NanoleafClient in --dry-run: prints instead of calling."""

    def __init__(self, n_leds: int):
        self.n_leds = n_leds

    def _p(self, what: str) -> nl.Response:
        print(f"    DRY-RUN HTTP {what}", flush=True)
        return nl.Response(204, None)

    def enable_ext_control(self, version="v2"):
        return self._p(f"PUT /effects {json.dumps(nl.ext_control_body(version))}")

    def set_state(self, **kw):
        return self._p(f"PUT /state {kw}")


class DrySender:
    def __init__(self, hex_limit: Optional[int]):
        self.hex_limit = hex_limit

    def send(self, packets):
        t0 = time.perf_counter()
        for p in packets:
            print(f"    UDP {len(p):5d}B  {hexdump(p, self.hex_limit)}")
        return time.perf_counter() - t0

    def close(self):
        pass


def run_phase(phase: Phase, sender, n_leds: int, transition: Optional[int],
              sleep: bool = True) -> dict:
    layout = nl.LAYOUTS[phase.layout]
    fn = PATTERNS[phase.pattern][0]
    max_payload = nl.SAFE_UDP_PAYLOAD if phase.split else None
    period = 1.0 / phase.fps
    n_frames = max(1, int(round(phase.seconds * phase.fps)))
    send_times, overruns, pkts, nbytes = [], 0, 0, 0
    start = time.perf_counter()
    for k in range(n_frames):
        packets = nl.encode_packets(layout, nl.colors_to_pixels(fn(n_leds, k)),
                                    transition, max_payload)
        send_times.append(sender.send(packets))
        pkts += len(packets)
        nbytes += sum(len(p) for p in packets)
        if sleep:
            target = start + (k + 1) * period
            delay = target - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                overruns += 1
    elapsed = time.perf_counter() - start
    sent = len(send_times)
    return {
        "frames": sent,
        "achieved_fps": (sent / elapsed) if (sleep and elapsed > 0) else float("nan"),
        "packets_per_frame": pkts / max(sent, 1),
        "bytes_per_frame": nbytes / max(sent, 1),
        "send_us_mean": 1e6 * sum(send_times) / max(sent, 1),
        "send_us_p95": 1e6 * pct(send_times, 0.95),
        "send_us_max": 1e6 * max(send_times or [0.0]),
        "overruns": overruns,
    }


def blackout(sender, layout_name: str, n_leds: int, seconds: float, sleep: bool):
    """Stream all-off frames at 10 fps for `seconds` (keeps extControl alive)."""
    layout = nl.LAYOUTS[layout_name]
    frames = max(1, int(seconds * 10))
    pkt = nl.encode_packets(layout, nl.colors_to_pixels([OFF] * n_leds))
    for _ in range(frames):
        sender.send(pkt)
        if sleep:
            time.sleep(0.1)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_discover(args) -> int:
    print(f"Browsing mDNS for {nl.MDNS_SERVICE} ({args.timeout:.0f}s)...")
    try:
        found = nl.discover(timeout=args.timeout)
    except OSError as e:
        print(f"mDNS query failed: {e}")
        found = []
    if not found:
        print("No devices answered. Fallbacks:\n"
              "  macOS: dns-sd -B _nanoleafapi._tcp   then  dns-sd -L <name> _nanoleafapi._tcp\n"
              "  Pi:    avahi-browse -rt _nanoleafapi._tcp\n"
              "  or look up the IP in your router / Nanoleaf app, and use --host.")
        return 1
    for s in found:
        txt = s["txt"]
        print(f"- {s['instance']}\n    host={s['host']} address={s['address']} port={s['port']}\n"
              f"    model={txt.get('md', '?')} firmware={txt.get('srcvers', txt.get('fw', '?'))}"
              f" id={txt.get('id', '?')}\n    txt={txt}")
    return 0


def cmd_pair(args) -> int:
    client = nl.NanoleafClient(args.host, args.port, timeout=args.http_timeout)
    print("PAIRING\n"
          "  1. Leave this running.\n"
          "  2. In the Nanoleaf app open this device's settings and tap 'Connect to API'.\n"
          "     The device then accepts pairing for about 30 seconds.\n"
          f"  Trying POST {client.base}/new every 2 s for {args.pair_wait:.0f} s. Ctrl-C to stop.\n")
    start = time.monotonic()
    deadline = start + args.pair_wait
    attempt = 0
    try:
        while time.monotonic() < deadline:
            attempt += 1
            left = max(0, deadline - time.monotonic())
            prefix = f"  [{attempt:2d}] {left:3.0f}s left:"
            try:
                r = client.pair()
            except (OSError, nl.NanoleafError) as e:
                print(f"{prefix} connection error: {e}", flush=True)
                r = None
            if r is not None:
                if http_ok(r) and isinstance(r.body, dict) and r.body.get("auth_token"):
                    token = r.body["auth_token"]
                    path = nl.save_token(args.host, token, args.token_file, port=args.port)
                    print(f"{prefix} OK (HTTP {r.status}). Token saved to {path} (mode 600).")
                    return 0
                if http_ok(r):
                    print(f"{prefix} HTTP {r.status} but no auth_token in body: {r.raw!r}")
                    return 2
                hint = "pairing window not open: tap 'Connect to API' now" if r.status == 403 else ""
                print(f"{prefix} HTTP {r.status} {r.raw[:120]!r} {hint}".rstrip(), flush=True)
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n  Stopped.")
        return 130
    print("  Gave up: no token. Run --pair again, then tap 'Connect to API' while it's running\n"
          "  (some Essentials models: update the app/firmware if the option is missing).")
    return 1


def resolve(args) -> Tuple[str, Optional[str]]:
    host, entry = nl.lookup_device(args.host, args.token_file)
    if not host:
        sys.exit("No --host given and no single saved device; pass --host.")
    entry = entry or {}
    if args.port is None:
        args.port = int(entry.get("port") or nl.DEFAULT_HTTP_PORT)
    token = args.token or entry.get("auth_token")
    if not token and not args.dry_run:
        sys.exit(f"No token for {host}. Run --pair first or pass --token.")
    args.host = host
    return host, token


def cmd_info(args) -> int:
    host, token = resolve(args)
    c = nl.NanoleafClient(host, args.port, token, args.http_timeout)
    r = c.info()
    print(f"GET / -> HTTP {r.status}")
    if not http_ok(r):
        print(f"  {r.raw!r}")
        return 1
    print(json.dumps(r.body, indent=2))
    info = nl.parse_info(r.body)
    n = c.length()
    lay = c.layout()
    sel = c.selected_effect()
    print("\nSUMMARY")
    for k in ("name", "model", "firmware", "hardware", "on", "brightness", "color_mode",
              "hue", "sat", "ct"):
        print(f"  {k:12s} {info.get(k)}")
    print(f"  {'effect':12s} {sel}")
    print(f"  {'numLEDs':12s} {n}  (GET /length)")
    print(f"  {'panelLayout':12s} in info: {info['has_panel_layout']}; "
          f"GET /panelLayout/layout -> HTTP {lay.status}"
          + (f" {json.dumps(lay.body)[:300]}" if http_ok(lay) else ""))
    if n is None:
        print("  ! /length missing: pass --leds N to --stream-test (count the LEDs).")
    return 0


def cmd_state(args) -> int:
    host, token = resolve(args)
    c = nl.NanoleafClient(host, args.port, token, args.http_timeout)
    on = None if args.state is None else args.state == "on"
    r = c.set_state(on=on, brightness=args.brightness, hue=args.hue, sat=args.sat, ct=args.ct)
    print(f"PUT /state -> HTTP {r.status} {r.raw!r}")
    return 0 if http_ok(r) else 1


def cmd_stream_test(args) -> int:
    clock = Clock()
    dry = args.dry_run
    sleep = not dry
    if dry:
        host, token = args.host or "DRY-RUN", None
        client = DryClient(args.leds or 50)
        n_leds = args.leds or 50
        snap = None
    else:
        host, token = resolve(args)
        client = nl.NanoleafClient(host, args.port, token, args.http_timeout)
        log(clock, f"Capturing current state of {host} for restore...")
        snap = nl.capture_state(client)
        log(clock, f"  model={snap['model']} fw={snap['firmware']} on={snap['on']} "
                   f"bri={snap['brightness']} mode={snap['color_mode']} effect={snap['effect']}")
        n_leds = args.leds or client.length()
        if not n_leds:
            sys.exit("Could not read /length; pass --leds N.")
    layouts = [s.strip() for s in args.layouts.split(",") if s.strip()]
    for lay in layouts:
        if lay not in nl.LAYOUTS:
            sys.exit(f"Unknown layout {lay}; choose from {', '.join(nl.LAYOUTS)}")
    fps_list = [int(f) for f in args.fps.split(",")]
    plan = build_plan(layouts, fps_list, n_leds, args.phase_scale)
    gap = args.gap
    total = sum(p.seconds for p in plan) + gap * len(plan) + 2 * len(layouts)
    log(clock, f"STREAM TEST: {len(plan)} phases, {n_leds} LEDs, ~{total:.0f}s total. "
               "Watch the lights; note which phase IDs look right.")
    for p in plan:
        print(f"    {p.pid} {p.label:42s} {p.seconds:4.1f}s")
    print("  Tip: film the lights with your phone while this runs; the timestamps let you\n"
          "  match video to phases. Disable Home Assistant's Nanoleaf integration first,\n"
          "  it can overwrite state mid-stream (LedFx issue #1850).", flush=True)

    stopping = {"flag": False}

    def on_sigterm(*_):
        raise KeyboardInterrupt

    old_term = signal.signal(signal.SIGTERM, on_sigterm)
    sender = None
    try:
        if not dry:
            # One key per PUT (the Essentials doc only shows single-key bodies).
            r = client.set_state(on=True)
            log(clock, f"PUT /state on=true -> HTTP {r.status}")
            if not http_ok(r):
                log(clock, "!!! WARNING: could not switch the lights ON "
                           f"(HTTP {r.status} {r.raw[:120]!r}). A dark strip below does NOT "
                           "mean streaming failed. Fix with --state on and re-run.")
            r = client.set_state(brightness=args.brightness or 60)
            log(clock, f"PUT /state brightness={args.brightness or 60} -> HTTP {r.status}")
            if not http_ok(r):
                log(clock, f"!!! WARNING: brightness PUT failed (HTTP {r.status}).")
        for lay in layouts:
            layout = nl.LAYOUTS[lay]
            log(clock, f"=== layout {lay}: {layout.description}")
            r = client.enable_ext_control(layout.ext_control_version)
            log(clock, f"PUT /effects extControl {layout.ext_control_version} -> HTTP {r.status}"
                       f" {r.raw[:200]!r}")
            phases = [p for p in plan if p.layout == lay]
            if not http_ok(r):
                for p in phases:
                    p.result = f"SKIPPED (extControl {layout.ext_control_version} HTTP {r.status})"
                log(clock, f"  extControl {layout.ext_control_version} rejected -> skipping "
                           f"{', '.join(p.pid for p in phases)}")
                continue
            port = args.udp_port
            if isinstance(r.body, dict) and r.body.get("streamControlPort"):
                port = int(r.body["streamControlPort"])
                log(clock, f"  device asked for UDP port {port} "
                           f"({r.body.get('streamControlProtocol', 'udp')})")
            if sender:
                sender.close()
            sender = DrySender(None if args.hex_full else 48) if dry \
                else nl.StreamSender(host, port)
            blackout(sender, lay, n_leds, 1.0, sleep)
            for p in phases:
                log(clock, f"{p.pid} {p.label} -- watch for: {PATTERNS[p.pattern][1]}")
                p.stats = run_phase(p, sender, n_leds, args.transition, sleep)
                p.result = "ran"
                s = p.stats
                log(clock, f"{p.pid} done: {s['frames']} frames, "
                           f"{s['packets_per_frame']:.0f} pkt/frame, "
                           f"{s['bytes_per_frame']:.0f} B/frame, "
                           f"achieved {s['achieved_fps']:.1f} fps, "
                           f"sendto mean {s['send_us_mean']:.0f}us p95 {s['send_us_p95']:.0f}us "
                           f"max {s['send_us_max']:.0f}us, overruns {s['overruns']}")
                blackout(sender, lay, n_leds, gap, sleep)
    except KeyboardInterrupt:
        stopping["flag"] = True
        log(clock, "Interrupted.")
    finally:
        signal.signal(signal.SIGTERM, old_term)
        if sender:
            sender.close()
        if snap is not None and not args.no_restore:
            log(clock, "Restoring previous state...")
            try:
                for r in nl.restore_state(client, snap):
                    log(clock, f"  restore -> HTTP {r.status}")
            except Exception as e:  # noqa: BLE001 - best effort, always report
                log(clock, f"  restore failed: {e}. Pick a scene in the Nanoleaf app.")
        print_summary(plan, n_leds)
    return 130 if stopping["flag"] else 0


def print_summary(plan: List[Phase], n_leds: int):
    print("\nSUMMARY (host-side timing only: UDP has no ack, so the lights are the real result)")
    print(f"  LEDs: {n_leds}")
    print(f"  {'id':4s} {'phase':44s} {'result':8s} {'fps':>6s} {'B/frame':>8s} "
          f"{'pkt':>4s} {'send_us':>8s} {'max_us':>7s}")
    for p in plan:
        s = p.stats
        if s:
            print(f"  {p.pid:4s} {p.label:44s} {p.result:8s} {s['achieved_fps']:6.1f} "
                  f"{s['bytes_per_frame']:8.0f} {s['packets_per_frame']:4.0f} "
                  f"{s['send_us_mean']:8.0f} {s['send_us_max']:7.0f}")
        else:
            print(f"  {p.pid:4s} {p.label:44s} {p.result}")
    print("\nReport back: which phase IDs showed the expected pattern, and for P01 which LEDs\n"
          "lit red/green/blue/white (e.g. 'P01 ok, P02-P07 ok, P08+ nothing').")


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="See module docstring for the full workflow.")
    act = ap.add_argument_group("actions (pick one)")
    act.add_argument("--discover", action="store_true", help="mDNS browse for devices")
    act.add_argument("--pair", action="store_true", help="get and save an auth token")
    act.add_argument("--info", action="store_true", help="device info, LED count, layout")
    act.add_argument("--state", choices=["on", "off"], help="set power (with optional colour)")
    act.add_argument("--stream-test", action="store_true", help="run the UDP streaming probe")
    ap.add_argument("--host", help="device IP/hostname (default: only saved device)")
    ap.add_argument("--port", type=int, default=None,
                    help="HTTP port (default: saved port, else 16021)")
    ap.add_argument("--udp-port", type=int, default=nl.DEFAULT_STREAM_PORT, help="UDP port (60222)")
    ap.add_argument("--token", help="auth token (overrides saved token)")
    ap.add_argument("--token-file", default=None,
                    help=f"token store (default {nl.DEFAULT_TOKEN_PATH})")
    ap.add_argument("--brightness", type=int, help="0-100")
    ap.add_argument("--hue", type=int, help="0-360")
    ap.add_argument("--sat", type=int, help="0-100")
    ap.add_argument("--ct", type=int, help="colour temperature K")
    ap.add_argument("--leds", type=int, help="LED count override (default: GET /length)")
    ap.add_argument("--layouts", default="v2,v1", help="frame layouts to try, in order")
    ap.add_argument("--fps", default="5,10,20", help="frame rates to try")
    ap.add_argument("--transition", type=int, default=None,
                    help="transition time field in 100ms units (default: v2=0, v1=1)")
    ap.add_argument("--phase-scale", type=float, default=1.0, help="multiply phase durations")
    ap.add_argument("--gap", type=float, default=1.0, help="blackout seconds between phases")
    ap.add_argument("--no-restore", action="store_true", help="leave device in stream mode")
    ap.add_argument("--dry-run", action="store_true", help="print packets as hex, send nothing")
    ap.add_argument("--hex-full", action="store_true", help="dry-run: do not truncate hex")
    ap.add_argument("--timeout", type=float, default=3.0, help="discovery seconds")
    ap.add_argument("--http-timeout", type=float, default=3.0)
    ap.add_argument("--pair-wait", type=float, default=90.0, help="seconds to keep trying pair")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.dry_run and not args.stream_test:
        sys.exit("--dry-run is only supported with --stream-test")
    if args.discover:
        return cmd_discover(args)
    if args.pair:
        if not args.host:
            sys.exit("--pair needs --host")
        args.port = args.port or nl.DEFAULT_HTTP_PORT
        return cmd_pair(args)
    if args.info:
        return cmd_info(args)
    if args.stream_test:
        return cmd_stream_test(args)
    if args.state or any(v is not None for v in (args.brightness, args.hue, args.sat, args.ct)):
        return cmd_state(args)
    build_parser().print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
