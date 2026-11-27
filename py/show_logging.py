"""Log file setup for the show process (synchronized_lights.py)."""

import logging
import os
import sys

LOG_FILENAME = "music_and_lights.play.dbg"
LOG_FORMAT = "[%(asctime)s] %(levelname)s {%(pathname)s:%(lineno)d} - %(message)s"


def configure_show_logging(log_dir, level_name=None):
    """
    Send the root logger to ``<log_dir>/music_and_lights.play.dbg``.

    ``force=True`` matters: a module that logs while it is imported (e.g.
    hardware_controller's "LED module not available" warning when bibliopixel
    isn't installed) makes Python attach a default stderr handler, and a plain
    basicConfig() call afterwards silently does nothing, so no log file is
    written.

    Args:
        log_dir: Directory for the log file (created if missing)
        level_name: --log value (DEBUG, INFO, ...); None or unknown -> INFO

    If the file can't be opened (e.g. a root run left it owned by root and
    the API now runs the show as a normal user), logging goes to stderr with
    a warning instead of crashing the show: the API captures stderr.

    Returns:
        str: Path of the log file, or None if logging fell back to stderr
    """
    path = os.path.join(log_dir, LOG_FILENAME)
    level = getattr(logging, str(level_name or "INFO").upper(), None)
    if not isinstance(level, int):
        level = logging.INFO
    try:
        os.makedirs(log_dir, exist_ok=True)
        logging.basicConfig(filename=path, format=LOG_FORMAT, level=level, force=True)
        return path
    except OSError as e:
        logging.basicConfig(stream=sys.stderr, format=LOG_FORMAT, level=level, force=True)
        logging.warning("Cannot write show log %s (%s); logging to stderr. "
                        "Fix with: sudo chown -R $USER %s", path, e, log_dir)
        return None
