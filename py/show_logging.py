"""Log file setup for the show process (synchronized_lights.py)."""

import logging
import os

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

    Returns:
        str: Path of the log file
    """
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, LOG_FILENAME)
    level = getattr(logging, str(level_name or "INFO").upper(), None)
    if not isinstance(level, int):
        level = logging.INFO
    logging.basicConfig(filename=path, format=LOG_FORMAT, level=level, force=True)
    return path
