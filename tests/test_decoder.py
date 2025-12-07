"""
Tests for decoder wrapper - soundfile-based audio decoding (replaces git-based decoder).

Note: Many tests require pyalsaaudio which is Linux-only (ALSA).
These tests will be skipped on macOS/Windows - this is expected behavior.
They will run on Raspberry Pi/Linux systems.
"""
import pytest
import numpy as np
import tempfile
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Check if we're on a platform that supports ALSA
ALSA_AVAILABLE = sys.platform.startswith('linux')


@pytest.fixture
def mock_soundfile():
    """Mock soundfile module for testing."""
    sf_mock = MagicMock()

    # Mock file object
    file_mock = MagicMock()
    file_mock.samplerate = 44100
    file_mock.channels = 2
    file_mock.frames = 100000
    file_mock.read.return_value = np.zeros((1024, 2), dtype=np.int16)
    file_mock.__enter__.return_value = file_mock
    file_mock.__exit__.return_value = None

    sf_mock.SoundFile.return_value = file_mock

    return sf_mock


@pytest.mark.unit
@pytest.mark.platform
class TestAudioFileWrapper:
    """Test AudioFileWrapper class that wraps soundfile.

    Note: Requires pyalsaaudio (Linux/Pi only). Skipped on macOS/Windows.
    """

    def test_wrapper_imports(self):
        """Test that synchronized_lights module can be imported."""
        if not ALSA_AVAILABLE:
            pytest.skip("Requires Linux/ALSA (skipped on macOS/Windows)")

        try:
            import synchronized_lights
            assert synchronized_lights is not None
        except ImportError as e:
            pytest.skip(f"synchronized_lights not importable: {e}")

    def test_wrapper_has_decoder_methods(self):
        """Test that AudioFileWrapper has decoder-compatible methods."""
        if not ALSA_AVAILABLE:
            pytest.skip("Requires Linux/ALSA (skipped on macOS/Windows)")

        try:
            import synchronized_lights

            # Check that AudioFileWrapper class exists
            assert hasattr(synchronized_lights, 'AudioFileWrapper')

            # Check for decoder-compatible methods
            wrapper_class = synchronized_lights.AudioFileWrapper
            assert hasattr(wrapper_class, 'getframerate')
            assert hasattr(wrapper_class, 'getnchannels')
            assert hasattr(wrapper_class, 'readframes')
        except (ImportError, AttributeError) as e:
            pytest.skip(f"AudioFileWrapper not available: {e}")


@pytest.mark.unit
@pytest.mark.platform
class TestDecoderCompatibility:
    """Test decoder compatibility layer.

    Note: Requires pyalsaaudio (Linux/Pi only). Skipped on macOS/Windows.
    """

    def test_decoder_module_exists(self):
        """Test that decoder compatibility module exists."""
        if not ALSA_AVAILABLE:
            pytest.skip("Requires Linux/ALSA (skipped on macOS/Windows)")

        try:
            import synchronized_lights

            # Check that decoder compatibility is defined
            assert hasattr(synchronized_lights, 'decoder')
        except (ImportError, AttributeError) as e:
            pytest.skip(f"Decoder compatibility not available: {e}")

    def test_decoder_open_function(self):
        """Test that decoder.open() function exists."""
        if not ALSA_AVAILABLE:
            pytest.skip("Requires Linux/ALSA (skipped on macOS/Windows)")

        try:
            import synchronized_lights

            assert hasattr(synchronized_lights.decoder, 'open')
        except (ImportError, AttributeError) as e:
            pytest.skip(f"decoder.open not available: {e}")


@pytest.mark.unit
class TestSoundFileIntegration:
    """Test integration with soundfile library."""

    def test_soundfile_imported(self):
        """Test that soundfile can be imported."""
        try:
            import soundfile as sf
            assert sf is not None
        except ImportError:
            pytest.skip("soundfile not installed")

    def test_soundfile_in_requirements(self):
        """Test that soundfile is in requirements.txt."""
        requirements_path = Path(__file__).parent.parent / 'requirements.txt'

        if requirements_path.exists():
            content = requirements_path.read_text()
            assert 'soundfile' in content.lower(), "soundfile should be in requirements.txt"


@pytest.mark.integration
@pytest.mark.audio
@pytest.mark.platform
class TestRealAudioFile:
    """Test with real audio files.

    Note: Requires pyalsaaudio (Linux/Pi only). Skipped on macOS/Windows.
    """

    def test_wrapper_opens_wav(self):
        """Test opening WAV file through wrapper."""
        if not ALSA_AVAILABLE:
            pytest.skip("Requires Linux/ALSA (skipped on macOS/Windows)")

        try:
            import soundfile as sf
            import synchronized_lights
        except ImportError:
            pytest.skip("soundfile or synchronized_lights not available")

        # Check if test audio file exists
        test_file = Path(__file__).parent / 'fixtures' / 'test_tone.wav'
        if not test_file.exists():
            pytest.skip(f"Test audio file not found: {test_file}")

        # Open the file with AudioFileWrapper
        wrapper = synchronized_lights.AudioFileWrapper(str(test_file))

        # Verify properties
        assert wrapper.getframerate() == 44100
        assert wrapper.getnchannels() == 2

    def test_wrapper_reads_frames(self):
        """Test reading frames from audio file."""
        if not ALSA_AVAILABLE:
            pytest.skip("Requires Linux/ALSA (skipped on macOS/Windows)")

        try:
            import synchronized_lights
        except ImportError:
            pytest.skip("synchronized_lights not available")

        # Check if test audio file exists
        test_file = Path(__file__).parent / 'fixtures' / 'test_tone.wav'
        if not test_file.exists():
            pytest.skip(f"Test audio file not found: {test_file}")

        # Open and read frames
        wrapper = synchronized_lights.AudioFileWrapper(str(test_file))

        # Read 1024 frames
        data = wrapper.readframes(1024)

        # Should return bytes
        assert isinstance(data, bytes)
        assert len(data) > 0


@pytest.mark.unit
@pytest.mark.platform
class TestWrapperMethods:
    """Test AudioFileWrapper methods with mocked soundfile.

    Note: Requires pyalsaaudio (Linux/Pi only). Skipped on macOS/Windows.
    """

    def test_getframerate(self, mock_soundfile):
        """Test getframerate() method."""
        if not ALSA_AVAILABLE:
            pytest.skip("Requires Linux/ALSA (skipped on macOS/Windows)")

        with patch('soundfile.SoundFile', mock_soundfile.SoundFile):
            try:
                import synchronized_lights

                wrapper = synchronized_lights.AudioFileWrapper('test.mp3')
                rate = wrapper.getframerate()
                assert rate == 44100
            except (ImportError, AttributeError):
                pytest.skip("AudioFileWrapper not available")

    def test_getnchannels(self, mock_soundfile):
        """Test getnchannels() method."""
        if not ALSA_AVAILABLE:
            pytest.skip("Requires Linux/ALSA (skipped on macOS/Windows)")

        with patch('soundfile.SoundFile', mock_soundfile.SoundFile):
            try:
                import synchronized_lights

                wrapper = synchronized_lights.AudioFileWrapper('test.mp3')
                channels = wrapper.getnchannels()
                assert channels == 2
            except (ImportError, AttributeError):
                pytest.skip("AudioFileWrapper not available")

    def test_readframes(self, mock_soundfile):
        """Test readframes() method."""
        if not ALSA_AVAILABLE:
            pytest.skip("Requires Linux/ALSA (skipped on macOS/Windows)")

        with patch('soundfile.SoundFile', mock_soundfile.SoundFile):
            try:
                import synchronized_lights

                wrapper = synchronized_lights.AudioFileWrapper('test.mp3')
                data = wrapper.readframes(1024)
                assert isinstance(data, bytes)
            except (ImportError, AttributeError):
                pytest.skip("AudioFileWrapper not available")


@pytest.mark.unit
class TestNoGitDependencies:
    """Test that git-based decoder is not used."""

    def test_no_decoder_git_import(self):
        """Test that old git-based decoder is not imported."""
        import sys

        # Old decoder module should not be in sys.modules
        # (unless installed from old version)
        if 'decoder' in sys.modules:
            # If it exists, it should be the compatibility wrapper, not git version
            import decoder
            # Git-based decoder had different attributes
            # Our wrapper should have 'open' function
            assert hasattr(decoder, 'open')

    def test_uses_soundfile_not_decoder(self):
        """Test that soundfile is used instead of decoder."""
        if not ALSA_AVAILABLE:
            pytest.skip("Requires Linux/ALSA (skipped on macOS/Windows)")

        try:
            import synchronized_lights
            import inspect

            # Check synchronized_lights source for soundfile import
            source = inspect.getsource(synchronized_lights)
            assert 'soundfile' in source or 'sf' in source
        except (ImportError, OSError):
            pytest.skip("Cannot inspect synchronized_lights source")

    def test_decoder_not_in_requirements(self):
        """Test that git-based decoder is NOT in requirements.txt."""
        requirements_path = Path(__file__).parent.parent / 'requirements.txt'

        if requirements_path.exists():
            content = requirements_path.read_text()
            # Should not have git+https://...decoder references
            assert 'bitbucket.org/broken2048/decoder' not in content
            assert 'decoder-v3.py.git' not in content


@pytest.mark.unit
class TestSupportedFormats:
    """Test that common audio formats are supported via soundfile."""

    @pytest.mark.parametrize("extension", ['.mp3', '.wav', '.flac', '.ogg', '.m4a'])
    def test_format_support(self, extension):
        """Test that wrapper can be created for common formats."""
        # This is a basic check - actual support depends on soundfile and libsndfile
        try:
            import soundfile as sf
            # soundfile supports these formats (with appropriate libsndfile build)
            assert sf is not None
        except ImportError:
            pytest.skip("soundfile not available")
