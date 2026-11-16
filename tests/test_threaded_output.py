"""Tests for py/outputs/threaded.py: non-blocking writes, fps cap, fault isolation."""
import threading
import time

import numpy as np
import pytest

from outputs.base import Output
from outputs.threaded import ThreadedOutput

pytestmark = pytest.mark.unit


class Recorder(Output):
    name = "rec"

    def __init__(self, delay=0.0, fail=None, max_fps=None):
        self.delay = delay
        self.fail = fail or (lambda n: False)  # fail(call_number) -> bool
        self.max_fps = max_fps
        self.calls = []
        self.times = []
        self.closed = False
        self.started = False
        self.lock = threading.Lock()

    def start(self):
        self.started = True

    def _do(self, what):
        with self.lock:
            n = len(self.calls)
        if self.delay:
            time.sleep(self.delay)
        if self.fail(n):
            with self.lock:
                self.calls.append(("FAIL", what))
            raise ConnectionError("device gone")
        with self.lock:
            self.calls.append(what)
            self.times.append(time.monotonic())

    def write_frame(self, levels):
        self._do(float(levels[0]))

    def all_on(self):
        self._do("on")

    def all_off(self):
        self._do("off")

    def close(self):
        self.closed = True


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def frame(v):
    return np.array([v], dtype="float32")


def test_write_frame_never_blocks_on_slow_output():
    inner = Recorder(delay=0.2)
    out = ThreadedOutput(inner)
    out.start()
    try:
        start = time.monotonic()
        for i in range(50):
            out.write_frame(frame(i))
        assert time.monotonic() - start < 0.05
    finally:
        out.close()


def test_newest_frame_wins_and_final_command_is_delivered():
    inner = Recorder(delay=0.05)
    out = ThreadedOutput(inner)
    out.start()
    for i in range(20):
        out.write_frame(frame(i))
    out.all_off()
    out.close()
    assert inner.calls[-1] == "off"
    assert len(inner.calls) < 20  # intermediate frames were dropped
    assert out.frames_dropped > 0
    assert inner.started and inner.closed


def test_fps_cap():
    inner = Recorder()
    out = ThreadedOutput(inner, max_fps=20)
    out.start()
    try:
        end = time.monotonic() + 0.5
        i = 0
        while time.monotonic() < end:
            out.write_frame(frame(i))
            i += 1
            time.sleep(0.001)
    finally:
        out.close()
    gaps = np.diff(inner.times)
    assert len(inner.times) <= 0.5 * 20 + 2
    assert gaps.min() >= 1 / 20 - 0.01


def test_max_fps_defaults_to_inner():
    assert ThreadedOutput(Recorder(max_fps=10)).max_fps == 10


def test_exception_is_isolated_and_output_recovers(caplog):
    # first 3 attempts fail (device off Wi-Fi), then it comes back
    inner = Recorder(fail=lambda n: n < 3)
    out = ThreadedOutput(inner, initial_backoff_s=0.02, max_backoff_s=0.05)
    out.start()
    try:
        with caplog.at_level("WARNING"):
            for i in range(200):
                out.write_frame(frame(i))  # must never raise
                time.sleep(0.002)
            assert wait_for(lambda: out.frames_sent > 0)
    finally:
        out.close()
    assert out.failures == 3
    assert out.healthy
    assert "device gone" in caplog.text
    assert "recovered after 3" in caplog.text


def test_backoff_doubles_and_caps():
    t = [0.0]
    out = ThreadedOutput(Recorder(), initial_backoff_s=1.0, max_backoff_s=4.0, clock=lambda: t[0])
    for _ in range(5):
        out._record_failure(OSError("x"))
    assert out._backoff_s == 4.0
    assert out.consecutive_failures == 5
    assert not out.healthy


def test_failure_logging_is_rate_limited(caplog):
    t = [0.0]
    out = ThreadedOutput(Recorder(), log_interval_s=30.0, clock=lambda: t[0])
    with caplog.at_level("WARNING"):
        for _ in range(10):
            out._record_failure(OSError("x"))
            t[0] += 1.0
        t[0] += 30.0
        out._record_failure(OSError("x"))
    assert caplog.text.count("OSError") == 2


def test_start_failure_does_not_kill_worker():
    class BadStart(Recorder):
        def start(self):
            raise OSError("no route")
    inner = BadStart()
    out = ThreadedOutput(inner, initial_backoff_s=0.01)
    out.start()
    out.write_frame(frame(1))
    assert wait_for(lambda: inner.calls == [1.0])
    out.close()


def test_close_is_idempotent_and_close_errors_swallowed():
    class BadClose(Recorder):
        def close(self):
            raise OSError("already gone")
    out = ThreadedOutput(BadClose())
    out.start()
    out.close()
    out.close()


def test_write_without_start_still_delivers():
    inner = Recorder()
    out = ThreadedOutput(inner)
    out.write_frame(frame(0.5))  # no start(): lazily started
    assert wait_for(lambda: inner.calls == [0.5])
    out.close()


def test_frame_is_copied_before_queueing():
    inner = Recorder(delay=0.05)
    out = ThreadedOutput(inner)
    out.start()
    out.write_frame(frame(0.0))  # occupies the worker
    buf = frame(0.25)
    out.write_frame(buf)
    buf[0] = 0.99  # caller reuses its buffer
    out.close()
    assert inner.calls[-1] == 0.25
