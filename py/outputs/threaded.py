"""ThreadedOutput: run a slow or unreliable Output off the audio loop.

The show loop must never wait on a network device. ThreadedOutput gives the
wrapped output its own worker thread and a single-slot mailbox:

* ``write_frame``/``all_on``/``all_off`` only drop a command in the slot and
  return immediately; a newer command replaces an unsent one (newest wins).
* The worker sends at most ``max_fps`` commands per second.
* If the wrapped output raises, the error is logged (rate-limited), the output
  is skipped for a back-off period that doubles up to ``max_backoff_s``, and
  the newest command is retried after that. The exception never reaches the
  show loop, so a Wi-Fi device dropping out can't stall the audio.
"""
import logging
import threading
import time

from outputs.base import Output

log = logging.getLogger(__name__)

_FRAME = "frame"
_ON = "on"
_OFF = "off"


class ThreadedOutput(Output):
    """Wrap an Output so it runs on its own thread.

    :param inner: the Output to drive
    :param max_fps: cap on commands per second; defaults to ``inner.max_fps``
    :param initial_backoff_s: first pause after a failure
    :param max_backoff_s: longest pause between retries
    :param log_interval_s: minimum time between repeated failure log lines
    :param clock: monotonic clock, injectable for tests
    """

    def __init__(self, inner, max_fps=None, initial_backoff_s=1.0, max_backoff_s=30.0,
                 log_interval_s=30.0, clock=time.monotonic):
        self.inner = inner
        self.name = "threaded:%s" % getattr(inner, "name", "output")
        self.max_fps = max_fps if max_fps is not None else getattr(inner, "max_fps", None)
        self.latency_s = getattr(inner, "latency_s", 0.0)
        self.initial_backoff_s = initial_backoff_s
        self.max_backoff_s = max_backoff_s
        self.log_interval_s = log_interval_s
        self._clock = clock

        self._cond = threading.Condition()
        self._pending = None
        self._stopping = False
        self._thread = None

        # Health, readable from other threads (e.g. for a status endpoint)
        self.frames_sent = 0
        self.frames_dropped = 0
        self.failures = 0
        self.consecutive_failures = 0
        self.last_error = None
        self._backoff_s = initial_backoff_s
        self._retry_at = 0.0
        self._last_log_at = None

    # -- Output interface (all non-blocking) --------------------------------

    def start(self):
        """Start the inner output and the worker thread."""
        if self._thread is not None:
            return
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def write_frame(self, levels):
        # Copy: the worker reads the frame later, after the caller may reuse it
        copy = getattr(levels, 'copy', None)
        self._post((_FRAME, copy() if copy else list(levels)))

    def all_on(self):
        self._post((_ON, None))

    def all_off(self):
        self._post((_OFF, None))

    def close(self, timeout=2.0):
        """Send any pending command, stop the worker and close the inner output."""
        thread = self._thread
        with self._cond:
            self._stopping = True
            self._cond.notify()
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                log.warning("%s: worker did not stop within %.1fs", self.name, timeout)
        self._thread = None
        try:
            self.inner.close()
        except Exception as err:  # never let shutdown raise
            log.warning("%s: close failed: %s", self.name, err)

    @property
    def healthy(self):
        """True unless the last attempt to send failed."""
        return self.consecutive_failures == 0

    # -- internals -----------------------------------------------------------

    def _post(self, command):
        # Start lazily so a caller that skipped Hardware.initialize() still
        # gets its frames shown.
        if self._thread is None:
            self.start()
        with self._cond:
            if self._pending is not None:
                self.frames_dropped += 1
            self._pending = command
            self._cond.notify()

    def _take(self):
        """Wait for a command; return None when stopping with nothing pending."""
        with self._cond:
            while self._pending is None and not self._stopping:
                self._cond.wait()
            command, self._pending = self._pending, None
            return command

    def _run(self):
        try:
            self.inner.start()
        except Exception as err:
            self._record_failure(err)

        min_interval = 1.0 / self.max_fps if self.max_fps else 0.0
        last_sent = None
        while True:
            command = self._take()
            if command is None:
                return

            deadline = self._retry_at
            if last_sent is not None and min_interval:
                deadline = max(deadline, last_sent + min_interval)
            # Sleep until the deadline (fps cap or back-off). New frames only
            # replace the command; close() cuts the wait short.
            with self._cond:
                while not self._stopping:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        break
                    self._cond.wait(remaining)
                if self._pending is not None:
                    command, self._pending = self._pending, None
                    self.frames_dropped += 1

            self._send(command)
            last_sent = self._clock()

    def _send(self, command):
        kind, levels = command
        try:
            if kind == _FRAME:
                self.inner.write_frame(levels)
            elif kind == _ON:
                self.inner.all_on()
            else:
                self.inner.all_off()
        except Exception as err:
            self._record_failure(err)
            return
        self.frames_sent += 1
        if self.consecutive_failures:
            log.warning("%s: recovered after %d failed attempts",
                        self.name, self.consecutive_failures)
        self.consecutive_failures = 0
        self._backoff_s = self.initial_backoff_s
        self._retry_at = 0.0

    def _record_failure(self, err):
        now = self._clock()
        self.failures += 1
        self.consecutive_failures += 1
        self.last_error = "%s: %s" % (type(err).__name__, err)
        self._retry_at = now + self._backoff_s
        if (self._last_log_at is None or self.consecutive_failures == 1
                or now - self._last_log_at >= self.log_interval_s):
            log.warning("%s: %s (attempt %d, retrying in %.1fs)", self.name,
                        self.last_error, self.consecutive_failures, self._backoff_s)
            self._last_log_at = now
        self._backoff_s = min(self._backoff_s * 2, self.max_backoff_s)
