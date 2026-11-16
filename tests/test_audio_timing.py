"""Tests for py/audio_timing.py: light_delay chunk maths and full-chunk sizes."""
import pytest

import audio_timing as at

pytestmark = pytest.mark.unit


class TestChunksPerSecond:
    def test_legacy_matches_original_formula(self):
        # Original: ((16 * channels * rate) / 8) / chunk_size
        for channels in (1, 2):
            expected = ((16 * channels * 44100) / 8) / 2048
            assert at.chunks_per_second(44100, channels, 2048, legacy=True) == expected

    def test_legacy_ignores_unit(self):
        frames = at.chunks_per_second(44100, 2, 2048, at.UNIT_FRAMES, legacy=True)
        byts = at.chunks_per_second(44100, 2, 2048, at.UNIT_BYTES, legacy=True)
        assert frames == byts

    def test_corrected_frames(self):
        assert at.chunks_per_second(44100, 2, 2048, at.UNIT_FRAMES, legacy=False) == 44100 / 2048

    def test_corrected_bytes_matches_legacy(self):
        # stream-in reads bytes, where the original formula was already right
        assert (at.chunks_per_second(44100, 2, 2048, at.UNIT_BYTES, legacy=False)
                == at.chunks_per_second(44100, 2, 2048, legacy=True))

    def test_unknown_unit(self):
        with pytest.raises(ValueError):
            at.chunks_per_second(44100, 2, 2048, 'samples', legacy=False)


class TestLightDelayChunks:
    def test_zero_delay_is_zero_either_way(self):
        assert at.light_delay_chunks(0.0, 44100, 2, 2048, legacy=True) == 0
        assert at.light_delay_chunks(0.0, 44100, 2, 2048, legacy=False) == 0

    def test_legacy_is_4x_for_stereo_file(self):
        legacy = at.light_delay_chunks(1.0, 44100, 2, 2048, legacy=True)
        fixed = at.light_delay_chunks(1.0, 44100, 2, 2048, legacy=False)
        assert legacy == 86   # int(176400 / 2048)
        assert fixed == 21    # int(44100 / 2048)
        assert legacy == pytest.approx(4 * fixed, abs=4)

    def test_corrected_delay_is_real_seconds(self):
        chunks = at.light_delay_chunks(0.5, 48000, 2, 1000, legacy=False)
        assert chunks * 1000 / 48000 == pytest.approx(0.5)


class TestExpectedChunkBytes:
    def test_alsa_frames(self):
        # audio-in: ALSA returns chunk_size frames of S16_LE
        assert at.expected_chunk_bytes(2, 2048, at.UNIT_FRAMES) == 8192
        assert at.expected_chunk_bytes(1, 2048, at.UNIT_FRAMES) == 4096

    def test_stream_bytes(self):
        assert at.expected_chunk_bytes(2, 2048, at.UNIT_BYTES) == 2048

    def test_old_check_never_matched_alsa_period(self):
        # Regression: audio-in compared len(data) (bytes) to chunk_size (frames),
        # so a full ALSA period was always discarded and the lights never moved.
        alsa_period = b'\x00' * (2048 * 2 * 2)
        assert len(alsa_period) != 2048
        assert len(alsa_period) == at.expected_chunk_bytes(2, 2048, at.UNIT_FRAMES)
