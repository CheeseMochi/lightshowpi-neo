"""
Small in-process rate limiting for the API.

A sliding-window limiter keyed by client IP. It is deliberately simple (no
extra dependency to install on the Pis): one API process, in-memory state that
resets on restart, and a cap on the number of tracked keys so a flood of
distinct addresses can't grow memory without bound.
"""

import threading
import time
from collections import OrderedDict, deque
from typing import Optional

from fastapi import Request

# Peers whose forwarded-for header we trust. cloudflared runs on the Pi and
# connects to the API over loopback; Cloudflare's edge sets CF-Connecting-IP
# and overwrites any value a client sends.
TRUSTED_PROXY_PEERS = {"127.0.0.1", "::1"}


def client_ip(request: Request) -> str:
    """
    Best-effort real client IP for a request.

    ``CF-Connecting-IP`` is used only when the TCP peer is loopback (that is,
    the request came through cloudflared on this Pi). A LAN client connecting
    directly can't spoof it.
    """
    peer = request.client.host if request.client else ""
    if peer in TRUSTED_PROXY_PEERS:
        forwarded = request.headers.get("cf-connecting-ip", "").strip()
        if forwarded:
            return forwarded
    return peer or "unknown"


def is_tunnel_request(request: Request) -> bool:
    """
    True when the request may have come in through the Cloudflare tunnel.

    Any non-blank ``CF-Connecting-IP`` header counts, whatever the peer address.
    Cloudflare always sets it (and overwrites a client's copy), while LAN
    clients and local tools such as the button manager never send it. Keying
    on the header alone fails toward asking for the CAPTCHA: the peer address
    can't be relied on, since uvicorn rewrites it from the proxy's
    X-Forwarded-For (which a visitor can partly control), and a LAN client that
    sends the header only makes itself solve a CAPTCHA.
    """
    return bool(request.headers.get("cf-connecting-ip", "").strip())


class RateLimiter:
    """Sliding-window counter: at most ``max_events`` per ``window_s`` per key."""

    def __init__(self, max_events: int, window_s: float, max_keys: int = 10000):
        self.max_events = max_events
        self.window_s = window_s
        self.max_keys = max_keys
        self._events: "OrderedDict[str, deque]" = OrderedDict()
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque:
        events = self._events.get(key)
        if events is None:
            return deque()
        while events and now - events[0] >= self.window_s:
            events.popleft()
        if not events:
            del self._events[key]
        return events

    def retry_after(self, key: str, now: Optional[float] = None) -> float:
        """Seconds until ``key`` may act again (0 when not limited)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            events = self._prune(key, now)
            if len(events) < self.max_events:
                return 0.0
            return max(0.0, self.window_s - (now - events[0]))

    def is_limited(self, key: str, now: Optional[float] = None) -> bool:
        """True if ``key`` has used up its allowance in the current window."""
        return self.retry_after(key, now) > 0

    def record(self, key: str, now: Optional[float] = None) -> None:
        """Count one event for ``key``."""
        now = time.monotonic() if now is None else now
        with self._lock:
            events = self._prune(key, now)
            if key not in self._events:
                if len(self._events) >= self.max_keys:
                    self._events.popitem(last=False)  # drop the oldest key
                self._events[key] = events
            events.append(now)
            self._events.move_to_end(key)

    def hit(self, key: str, now: Optional[float] = None) -> bool:
        """Record an event and return True if it was within the limit."""
        now = time.monotonic() if now is None else now
        if self.is_limited(key, now):
            return False
        self.record(key, now)
        return True

    def reset(self) -> None:
        """Forget all keys (tests, or after a config change)."""
        with self._lock:
            self._events.clear()
