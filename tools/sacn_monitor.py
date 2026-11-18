#!/usr/bin/env python3
"""
Watch sACN (E1.31) packets arriving on this Pi: a bench check for Pi-to-Pi
sACN networking that needs no lights.

Prints one line per second per universe: packets/s, sequence gaps (lost or
out-of-order packets), the sender and the first channel levels (0-255).
Standard library only, so it runs with any python3.

    python3 tools/sacn_monitor.py                       # until Ctrl-C
    python3 tools/sacn_monitor.py --seconds 60 --universe 100 --min-fps 8

The server sends one frame per audio chunk: sample rate / chunk_size, so about
10.8 fps with the default chunk_size 4096 at 44.1 kHz (21.5 fps at 2048).

With --seconds and --min-fps it exits 0 (PASS) when, on --universe (default
100, the GPIO default): at least 10 seconds had packets, the median packets
per active second is >= --min-fps, and under 1% of packets were missed.
Seconds with no packets (between songs, while the server starts the next one)
are reported as idle, not failures, and a new song's sequence restart isn't
counted as loss. Don't run it while the lightshow client is listening on the
same port: unicast packets go to only one of the two sockets.
"""

import argparse
import socket
import statistics
import struct
import sys
import time
from collections import defaultdict

ACN_ID = b"ASC-E1.17\x00\x00\x00"
# Silence longer than this starts a new stream: the next song's process
# restarts the sequence at 0, which must not count as lost packets.
RESYNC_SECONDS = 1.0
MIN_ACTIVE_SECONDS = 10
MAX_LOSS = 0.01


def parse_packet(data: bytes):
    """Return (universe, sequence, levels) for an E1.31 data packet, else None."""
    if len(data) < 126 or struct.unpack("!H", data[0:2])[0] != 0x0010 or data[4:16] != ACN_ID:
        return None
    if data[125] != 0x00:  # DMX start code
        return None
    universe = struct.unpack("!H", data[113:115])[0]
    count = struct.unpack("!H", data[123:125])[0] - 1  # property count includes the start code
    return universe, data[111], list(data[126:126 + max(count, 0)])


def seq_gap(prev, seq):
    """Packets missed between two sequence numbers (E1.31 wraps at 256)."""
    if prev is None:
        return 0
    return (seq - prev - 1) % 256


class Stats:
    """Per-universe counters for one reporting interval and the whole run."""

    def __init__(self):
        self.count = defaultdict(int)
        self.gaps = defaultdict(int)
        self.last_seq = {}
        self.last_time = {}
        self.last_levels = {}
        self.sender = {}
        self.total = defaultdict(int)
        self.total_gaps = defaultdict(int)

    def add(self, universe, seq, levels, sender, now=None):
        now = time.monotonic() if now is None else now
        prev = self.last_seq.get(universe)
        if now - self.last_time.get(universe, now) > RESYNC_SECONDS or \
                self.sender.get(universe, sender) != sender:
            prev = None
        gap = seq_gap(prev, seq)
        if gap > 128:  # a late, out-of-order packet rather than ~200 lost ones
            gap = 0
        self.count[universe] += 1
        self.total[universe] += 1
        self.gaps[universe] += gap
        self.total_gaps[universe] += gap
        self.last_seq[universe] = seq
        self.last_time[universe] = now
        self.last_levels[universe] = levels
        self.sender[universe] = sender

    def report(self, channels=8):
        lines = []
        for universe in sorted(self.count):
            levels = " ".join(f"{v:3d}" for v in self.last_levels[universe][:channels])
            lines.append(f"universe {universe:5d}  {self.count[universe]:3d} pkt/s  "
                         f"gaps {self.gaps[universe]:3d}  from {self.sender[universe]:15s}  [{levels}]")
        snapshot = dict(self.count)
        self.count.clear()
        self.gaps.clear()
        return lines, snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=5568)
    parser.add_argument("--seconds", type=float, default=0, help="stop after this long (0 = Ctrl-C)")
    parser.add_argument("--universe", type=int, default=100, help="universe checked by --min-fps")
    parser.add_argument("--min-fps", type=float, default=0,
                        help="with --seconds: fail if the median packets per active second "
                             "on --universe is lower (default chunk_size 4096: use 8)")
    parser.add_argument("--channels", type=int, default=8, help="levels to show per line")
    args = parser.parse_args(argv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", args.port))
    sock.settimeout(0.2)
    print(f"Listening for sACN on UDP {args.port} (Ctrl-C to stop)")

    stats = Stats()
    per_second = []
    start = next_report = time.monotonic()
    next_report += 1.0
    try:
        while not args.seconds or time.monotonic() - start < args.seconds:
            try:
                data, (sender, _) = sock.recvfrom(1500)
                parsed = parse_packet(data)
                if parsed:
                    stats.add(*parsed, sender)
            except socket.timeout:
                pass
            if time.monotonic() >= next_report:
                next_report += 1.0
                lines, snapshot = stats.report(args.channels)
                per_second.append(snapshot.get(args.universe, 0))
                print("\n".join(lines) if lines else "(no sACN packets)", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()

    print("\nTotal:")
    for universe in sorted(stats.total):
        print(f"  universe {universe}: {stats.total[universe]} packets, "
              f"{stats.total_gaps[universe]} missed")
    if not stats.total:
        print("  no sACN packets received")

    if args.seconds and args.min_fps:
        ok, message = verdict(per_second, stats.total[args.universe],
                              stats.total_gaps[args.universe], args.min_fps)
        print(f"{'PASS' if ok else 'FAIL'}: universe {args.universe}: {message}")
        return 0 if ok else 1
    return 0


def verdict(per_second, received, missed, min_fps):
    """(ok, message) for one universe from its packets-per-second samples."""
    active = [n for n in per_second if n > 0]
    idle = len(per_second) - len(active)
    median = statistics.median(active) if active else 0
    loss = missed / (received + missed) if received + missed else 0.0
    problems = []
    if len(active) < MIN_ACTIVE_SECONDS:
        problems.append(f"only {len(active)} s with packets (need {MIN_ACTIVE_SECONDS})")
    if median < min_fps:
        problems.append(f"median {median:g} pkt/s < {min_fps:g}")
    if loss >= MAX_LOSS:
        problems.append(f"{loss:.1%} missed (limit {MAX_LOSS:.0%})")
    summary = (f"{len(active)} active s, {idle} idle s, median {median:g} pkt/s, "
               f"{missed} missed of {received + missed} ({loss:.2%})")
    return not problems, summary + ("; " + "; ".join(problems) if problems else "")


if __name__ == "__main__":
    sys.exit(main())
