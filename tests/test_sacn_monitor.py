"""Tests for tools/sacn_monitor.py (bench sACN packet monitor)."""

import importlib.util
from pathlib import Path

import pytest

from e131packet import E131Packet

_spec = importlib.util.spec_from_file_location(
    "sacn_monitor", Path(__file__).parent.parent / "tools" / "sacn_monitor.py")
mon = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mon)


def packet(universe=100, seq=0, data=(0, 128, 255)):
    return E131Packet(name="LightShowPi", universe=universe, data=list(data), sequence=seq).packet_data


@pytest.mark.unit
class TestSacnMonitor:

    def test_parses_what_the_server_sends(self):
        assert mon.parse_packet(packet(101, 7, [1, 2, 0, 0])) == (101, 7, [1, 2, 0, 0])

    @pytest.mark.parametrize("data", [b"", b"x" * 200])
    def test_rejects_non_sacn(self, data):
        assert mon.parse_packet(data) is None

    @pytest.mark.parametrize("prev, seq, gap", [(None, 5, 0), (5, 6, 0), (5, 8, 2), (255, 0, 0), (254, 1, 2)])
    def test_sequence_gaps_wrap(self, prev, seq, gap):
        assert mon.seq_gap(prev, seq) == gap

    def test_stats_report_and_reset(self):
        stats = mon.Stats()
        for t, seq in enumerate((0, 1, 3)):
            stats.add(100, seq, [10, 20], "192.168.1.5", now=t * 0.1)
        lines, snapshot = stats.report()
        assert snapshot == {100: 3}
        assert "gaps   1" in lines[0] and "192.168.1.5" in lines[0]
        assert stats.report()[1] == {}
        assert stats.total_gaps[100] == 1

    def test_out_of_order_packet_is_not_counted_as_mass_loss(self):
        stats = mon.Stats()
        stats.add(100, 10, [], "a", now=0.0)
        stats.add(100, 9, [], "a", now=0.1)
        assert stats.total_gaps[100] == 0

    def test_song_change_restart_is_not_loss(self):
        """The next song's process restarts at sequence 0 after a pause."""
        stats = mon.Stats()
        stats.add(100, 200, [], "a", now=0.0)
        stats.add(100, 0, [], "a", now=4.0)
        stats.add(100, 1, [], "a", now=4.1)
        assert stats.total_gaps[100] == 0

    def test_verdict_ignores_idle_seconds_between_songs(self):
        per_second = [11] * 20 + [0, 0, 0] + [10] * 20 + [4]  # song, gap, song, partial second
        ok, msg = mon.verdict(per_second, received=430, missed=1, min_fps=8)
        assert ok, msg
        assert "3 idle s" in msg

    @pytest.mark.parametrize("per_second, missed, reason", [
        ([11] * 5, 0, "only 5 s"),
        ([5] * 30, 0, "median 5"),
        ([11] * 30, 20, "missed (limit"),
    ])
    def test_verdict_failures(self, per_second, missed, reason):
        ok, msg = mon.verdict(per_second, received=sum(per_second), missed=missed, min_fps=8)
        assert not ok and reason in msg
