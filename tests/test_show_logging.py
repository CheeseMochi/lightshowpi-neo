"""The show's log file is written even after an import-time warning (bench 2026-11-19)."""

import logging

import pytest

import show_logging


@pytest.fixture
def clean_root():
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    root.handlers[:] = []
    yield root
    for h in root.handlers:
        h.close()
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])


@pytest.mark.unit
class TestShowLogging:

    def test_file_written_after_import_time_warning(self, tmp_path, clean_root):
        logging.warning("LED module not available")  # attaches a stderr handler
        path = show_logging.configure_show_logging(str(tmp_path / "logs"))
        logging.info("Network client mode starting")
        for h in clean_root.handlers:
            h.flush()
        text = open(path).read()
        assert "Network client mode starting" in text
        assert path.endswith("logs/music_and_lights.play.dbg")

    @pytest.mark.parametrize("name, level", [(None, logging.INFO), ("debug", logging.DEBUG),
                                             ("WARNING", logging.WARNING), ("bogus", logging.INFO)])
    def test_log_level_from_cli(self, tmp_path, clean_root, name, level):
        show_logging.configure_show_logging(str(tmp_path), name)
        assert clean_root.level == level
