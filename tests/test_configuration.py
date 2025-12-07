"""
Tests for configuration_manager.py - Configuration loading and validation.
"""
import pytest
import tempfile
import os
from pathlib import Path


@pytest.fixture
def basic_config():
    """Create a basic configuration file for testing."""
    config_content = """[hardware]
gpio_pins = 0,1,2,3,4,5,6,7
pin_modes = onoff
active_low_mode = no
devices = {}

[audio_processing]
use_gpu = False
chunk_size = 4096
min_frequency = 20
max_frequency = 15000

[lightshow]
mode = playlist
playlist_path = /tmp/test.playlist
randomize_playlist = no
light_delay = 0.0
log_level = INFO

[network]
networking = off
port = 8888
buffer = 1024
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.cfg', delete=False) as f:
        f.write(config_content)
        temp_path = f.name

    yield temp_path

    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.unit
class TestConfigurationLoading:
    """Test configuration file loading."""

    def test_load_basic_config(self, basic_config):
        """Test loading a basic configuration file."""
        import configuration_manager as cm

        config = cm.Configuration(basic_config)
        assert config is not None

    def test_hardware_section_loads(self, basic_config):
        """Test that hardware section loads correctly."""
        import configuration_manager as cm

        config = cm.Configuration(basic_config)
        # Access hardware configuration through hardware section
        assert hasattr(config, 'hardware')
        assert hasattr(config.hardware, 'gpio_pins')

    def test_audio_processing_section_loads(self, basic_config):
        """Test that audio processing section loads."""
        import configuration_manager as cm

        config = cm.Configuration(basic_config)
        # Check GPU is disabled through audio_processing section
        assert hasattr(config, 'audio_processing')
        assert config.audio_processing.use_gpu == False
        assert config.audio_processing.chunk_size == 4096

    def test_lightshow_section_loads(self, basic_config):
        """Test that lightshow section loads."""
        import configuration_manager as cm

        config = cm.Configuration(basic_config)
        assert hasattr(config, 'lightshow')
        assert config.lightshow.mode == 'playlist'


@pytest.mark.unit
class TestRemovedFunctionality:
    """Test that removed functionality (FM/SMS/Twitter) is truly gone."""

    def test_no_fm_attribute(self, basic_config):
        """Test that FM configuration is not present."""
        import configuration_manager as cm

        config = cm.Configuration(basic_config)
        # FM attribute should not exist
        assert not hasattr(config, 'fm')

    def test_no_fm_section(self, basic_config):
        """Test that config file doesn't require FM section."""
        import configuration_manager as cm
        import configparser

        # Should load without FM section and not crash
        config = cm.Configuration(basic_config)
        parser = configparser.ConfigParser()
        parser.read(basic_config)

        # FM section should not exist
        assert not parser.has_section('fm')

    def test_no_sms_section(self, basic_config):
        """Test that config file doesn't have SMS section."""
        import configparser

        parser = configparser.ConfigParser()
        parser.read(basic_config)

        # SMS section should not exist
        assert not parser.has_section('sms')

    def test_songname_command_no_twitter(self, basic_config):
        """Test that songname_command doesn't reference Twitter."""
        import configparser

        parser = configparser.ConfigParser()
        parser.read(basic_config)

        if parser.has_option('lightshow', 'songname_command'):
            cmd = parser.get('lightshow', 'songname_command')
            # Should not contain twitter references
            assert 'twitter' not in cmd.lower()
            assert 'tweet' not in cmd.lower()


@pytest.mark.unit
class TestGPUConfiguration:
    """Test GPU configuration (should always be False)."""

    def test_use_gpu_false(self, basic_config):
        """Test that use_gpu is False."""
        import configuration_manager as cm

        config = cm.Configuration(basic_config)
        assert config.audio_processing.use_gpu == False

    def test_gpu_false_in_file(self, basic_config):
        """Test that config file has use_gpu = False."""
        import configparser

        parser = configparser.ConfigParser()
        parser.read(basic_config)

        use_gpu = parser.getboolean('audio_processing', 'use_gpu')
        assert use_gpu == False


@pytest.mark.unit
class TestChunkSize:
    """Test chunk_size configuration for Pi 3+."""

    def test_chunk_size_4096(self, basic_config):
        """Test that chunk_size is set to 4096 for Pi 3+ performance."""
        import configuration_manager as cm

        config = cm.Configuration(basic_config)
        assert config.audio_processing.chunk_size == 4096

    def test_chunk_size_configurable(self):
        """Test that chunk_size can be changed if needed."""
        config_content = """[hardware]
gpio_pins = 0,1,2,3

[audio_processing]
use_gpu = False
chunk_size = 8192
min_frequency = 20
max_frequency = 15000
custom_channel_mapping =
custom_channel_frequencies =

[lightshow]
mode = playlist
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.cfg', delete=False) as f:
            f.write(config_content)
            temp_path = f.name

        try:
            import configuration_manager as cm
            config = cm.Configuration(temp_path)
            assert config.audio_processing.chunk_size == 8192
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


@pytest.mark.unit
class TestDefaultsConfig:
    """Test loading the actual defaults.cfg file."""

    def test_defaults_cfg_exists(self):
        """Test that defaults.cfg exists."""
        defaults_path = Path(__file__).parent.parent / 'config' / 'defaults.cfg'
        assert defaults_path.exists(), "defaults.cfg should exist"

    def test_defaults_cfg_loads(self):
        """Test that defaults.cfg loads without errors."""
        import configuration_manager as cm

        defaults_path = Path(__file__).parent.parent / 'config' / 'defaults.cfg'
        if defaults_path.exists():
            config = cm.Configuration(str(defaults_path))
            assert config is not None

    def test_defaults_no_fm_section(self):
        """Test that defaults.cfg has no FM section."""
        import configparser

        defaults_path = Path(__file__).parent.parent / 'config' / 'defaults.cfg'
        if defaults_path.exists():
            parser = configparser.ConfigParser()
            parser.read(str(defaults_path))

            assert not parser.has_section('fm'), "FM section should be removed"

    def test_defaults_no_sms_section(self):
        """Test that defaults.cfg has no SMS section."""
        import configparser

        defaults_path = Path(__file__).parent.parent / 'config' / 'defaults.cfg'
        if defaults_path.exists():
            parser = configparser.ConfigParser()
            parser.read(str(defaults_path))

            assert not parser.has_section('sms'), "SMS section should be removed"

    def test_defaults_use_gpu_false(self):
        """Test that defaults.cfg has use_gpu = False."""
        import configparser

        defaults_path = Path(__file__).parent.parent / 'config' / 'defaults.cfg'
        if defaults_path.exists():
            parser = configparser.ConfigParser()
            parser.read(str(defaults_path))

            use_gpu = parser.getboolean('audio_processing', 'use_gpu')
            assert use_gpu == False, "use_gpu should be False in defaults"

    def test_defaults_chunk_size_4096(self):
        """Test that defaults.cfg has chunk_size = 4096."""
        import configparser

        defaults_path = Path(__file__).parent.parent / 'config' / 'defaults.cfg'
        if defaults_path.exists():
            parser = configparser.ConfigParser()
            parser.read(str(defaults_path))

            chunk_size = parser.getint('audio_processing', 'chunk_size')
            assert chunk_size == 4096, "chunk_size should be 4096 for Pi 3+"
