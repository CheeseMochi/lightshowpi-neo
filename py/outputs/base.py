"""Base class for show outputs."""


class Output(object):
    """One destination for show frames.

    Subclasses implement :meth:`write_frame`; the rest have safe defaults.

    :ivar name: short label for logs
    :ivar latency_s: how far behind the frame the output physically lags
    :ivar max_fps: highest useful frame rate, or None for unlimited
    """

    name = "output"
    latency_s = 0.0
    max_fps = None

    def start(self):
        """Open connections or threads. Called once before the first frame."""

    def write_frame(self, levels):
        """Show one frame.

        :param levels: per-channel levels, 0.0 to 1.0
        :type levels: numpy.ndarray
        """
        raise NotImplementedError

    def all_on(self):
        """Turn the whole output on (used by --state on and test patterns)."""

    def all_off(self):
        """Turn the whole output off."""

    def close(self):
        """Release resources. Safe to call more than once."""

    def __repr__(self):
        return "<%s %s>" % (type(self).__name__, self.name)
