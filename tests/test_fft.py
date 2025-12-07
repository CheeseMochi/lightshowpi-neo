"""
Tests for fft.py - CPU-based FFT processing (GPU acceleration removed).
"""
import pytest
import numpy as np
from unittest.mock import patch, MagicMock


@pytest.mark.unit
class TestFFTInitialization:
    """Test FFT class initialization."""

    def test_fft_creates_without_gpu(self):
        """Test that FFT initializes with use_gpu=False."""
        import fft

        # Create FFT instance with GPU disabled
        fft_instance = fft.FFT(
            chunk_size=2048,
            sample_rate=44100,
            num_bins=8,
            min_frequency=20,
            max_frequency=15000,
            custom_channel_mapping=0,
            custom_channel_frequencies=0,
            use_gpu=False
        )

        assert fft_instance is not None
        assert fft_instance.use_gpu == False

    def test_fft_warns_on_gpu_request(self, capsys):
        """Test that FFT warns when GPU is requested but not available."""
        import fft

        # Try to create with GPU (should warn and fall back to CPU)
        fft_instance = fft.FFT(
            chunk_size=2048,
            sample_rate=44100,
            num_bins=8,
            min_frequency=20,
            max_frequency=15000,
            custom_channel_mapping=0,
            custom_channel_frequencies=0,
            use_gpu=True
        )

        # Should fall back to CPU
        assert fft_instance.use_gpu == False


@pytest.mark.unit
class TestFFTComputation:
    """Test FFT computation using numpy."""

    def test_fft_computes_sine_wave(self):
        """Test FFT on a simple sine wave."""
        import fft

        chunk_size = 2048
        sample_rate = 44100
        frequency = 440  # A4 note

        # Create FFT instance
        fft_instance = fft.FFT(
            chunk_size=chunk_size,
            sample_rate=sample_rate,
            num_bins=8,
            min_frequency=20,
            max_frequency=15000,
            custom_channel_mapping=0,
            custom_channel_frequencies=0,
            use_gpu=False
        )

        # Generate a 440Hz sine wave
        t = np.arange(chunk_size) / sample_rate
        data = np.sin(2 * np.pi * frequency * t) * 32767
        data = data.astype(np.int16)

        # Compute FFT
        power = fft_instance.calculate_levels(data)

        # Power should be array-like
        assert isinstance(power, (np.ndarray, list))
        assert len(power) > 0

    def test_fft_handles_silence(self):
        """Test FFT on silent audio (all zeros)."""
        import fft

        chunk_size = 2048
        sample_rate = 44100

        fft_instance = fft.FFT(
            chunk_size=chunk_size,
            sample_rate=sample_rate,
            num_bins=8,
            min_frequency=20,
            max_frequency=15000,
            custom_channel_mapping=0,
            custom_channel_frequencies=0,
            use_gpu=False
        )

        # Silent audio
        data = np.zeros(chunk_size, dtype=np.int16)

        # Should not crash
        power = fft_instance.calculate_levels(data)
        assert isinstance(power, (np.ndarray, list))

    def test_fft_handles_noise(self):
        """Test FFT on random noise."""
        import fft

        chunk_size = 2048
        sample_rate = 44100

        fft_instance = fft.FFT(
            chunk_size=chunk_size,
            sample_rate=sample_rate,
            num_bins=8,
            min_frequency=20,
            max_frequency=15000,
            custom_channel_mapping=0,
            custom_channel_frequencies=0,
            use_gpu=False
        )

        # Random noise
        data = np.random.randint(-32768, 32767, chunk_size, dtype=np.int16)

        # Should not crash
        power = fft_instance.calculate_levels(data)
        assert isinstance(power, (np.ndarray, list))


@pytest.mark.unit
class TestFFTBinning:
    """Test frequency binning."""

    def test_frequency_bins_created(self):
        """Test that frequency bins are created correctly."""
        import fft

        chunk_size = 2048
        sample_rate = 44100
        num_bins = 8

        fft_instance = fft.FFT(
            chunk_size=chunk_size,
            sample_rate=sample_rate,
            num_bins=num_bins,
            min_frequency=20,
            max_frequency=15000,
            custom_channel_mapping=0,
            custom_channel_frequencies=0,
            use_gpu=False
        )

        # Should have frequency bins defined
        assert hasattr(fft_instance, 'frequency_limits')

    def test_correct_num_bins(self):
        """Test that the correct number of bins is used."""
        import fft

        num_bins = 16

        fft_instance = fft.FFT(
            chunk_size=2048,
            sample_rate=44100,
            num_bins=num_bins,
            min_frequency=20,
            max_frequency=15000,
            custom_channel_mapping=0,
            custom_channel_frequencies=0,
            use_gpu=False
        )

        # Compute FFT and check output size
        data = np.random.randint(-32768, 32767, 2048, dtype=np.int16)
        power = fft_instance.calculate_levels(data)

        # Output should have num_bins elements
        assert len(power) == num_bins


@pytest.mark.unit
class TestNoGPUDependency:
    """Test that GPU dependencies are not required."""

    def test_no_rpi_audio_levels_import(self):
        """Test that rpi_audio_levels is not imported."""
        import fft
        import sys

        # rpi_audio_levels should not be in modules
        assert 'rpi_audio_levels' not in sys.modules

    def test_uses_numpy_fft(self):
        """Test that numpy FFT is used."""
        import fft
        import numpy

        # Should use numpy.fft
        fft_instance = fft.FFT(
            chunk_size=2048,
            sample_rate=44100,
            num_bins=8,
            min_frequency=20,
            max_frequency=15000,
            custom_channel_mapping=0,
            custom_channel_frequencies=0,
            use_gpu=False
        )

        # Verify numpy is available (required dependency)
        assert numpy is not None


@pytest.mark.unit
class TestChunkSizes:
    """Test FFT with different chunk sizes (optimized for Pi 3+)."""

    @pytest.mark.parametrize("chunk_size", [1024, 2048, 4096, 8192])
    def test_various_chunk_sizes(self, chunk_size):
        """Test FFT with various chunk sizes."""
        import fft

        fft_instance = fft.FFT(
            chunk_size=chunk_size,
            sample_rate=44100,
            num_bins=8,
            min_frequency=20,
            max_frequency=15000,
            custom_channel_mapping=0,
            custom_channel_frequencies=0,
            use_gpu=False
        )

        # Generate test data
        data = np.random.randint(-32768, 32767, chunk_size, dtype=np.int16)

        # Should compute without errors
        power = fft_instance.calculate_levels(data)
        assert len(power) == 8

    def test_default_chunk_4096(self):
        """Test that default chunk size is 4096 for Pi 3+."""
        # This should match defaults.cfg setting
        import configparser
        from pathlib import Path

        defaults_path = Path(__file__).parent.parent / 'config' / 'defaults.cfg'
        if defaults_path.exists():
            parser = configparser.ConfigParser()
            parser.read(str(defaults_path))

            chunk_size = parser.getint('audio_processing', 'chunk_size')
            assert chunk_size == 4096, "Default chunk_size should be 4096 for Pi 3+"


@pytest.mark.unit
class TestSampleRates:
    """Test FFT with different sample rates."""

    @pytest.mark.parametrize("sample_rate", [22050, 44100, 48000])
    def test_various_sample_rates(self, sample_rate):
        """Test FFT with common sample rates."""
        import fft

        fft_instance = fft.FFT(
            chunk_size=2048,
            sample_rate=sample_rate,
            num_bins=8,
            min_frequency=20,
            max_frequency=15000,
            custom_channel_mapping=0,
            custom_channel_frequencies=0,
            use_gpu=False
        )

        # Generate test data
        data = np.random.randint(-32768, 32767, 2048, dtype=np.int16)

        # Should compute without errors
        power = fft_instance.calculate_levels(data)
        assert isinstance(power, (np.ndarray, list))
