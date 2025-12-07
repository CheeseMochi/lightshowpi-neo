"""
Pytest configuration and shared fixtures for LightShowPi tests.
"""
import sys
import os
from pathlib import Path
import pytest
import tempfile

# Set up SYNCHRONIZED_LIGHTS_HOME environment variable before any imports
REPO_ROOT = Path(__file__).parent.parent
os.environ['SYNCHRONIZED_LIGHTS_HOME'] = str(REPO_ROOT)

# Add py directory to Python path
PY_DIR = REPO_ROOT / "py"
sys.path.insert(0, str(PY_DIR))


@pytest.fixture
def temp_config_file():
    """Create a temporary config file for testing."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.cfg', delete=False) as f:
        f.write("""[hardware]
gpio_pins = 0,1,2,3,4,5,6,7
pin_modes = onoff

[audio_processing]
use_gpu = False
chunk_size = 4096
min_frequency = 20
max_frequency = 15000

[lightshow]
mode = playlist
""")
        temp_path = f.name

    yield temp_path

    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def mock_cpuinfo_pi3(tmp_path):
    """Create a mock /proc/cpuinfo file for Pi 3."""
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("""processor	: 0
model name	: ARMv7 Processor rev 4 (v7l)
BogoMIPS	: 38.40
Features	: half thumb fastmult vfp edsp neon vfpv3 tls vfpv4 idiva idivt vfpd32 lpae evtstrm crc32
CPU implementer	: 0x41
CPU architecture: 7
CPU variant	: 0x0
CPU part	: 0xd03
CPU revision	: 4

Hardware	: BCM2837
Revision	: a02082
Serial		: 0000000012345678
Model		: Raspberry Pi 3 Model B Rev 1.2
""")
    return cpuinfo


@pytest.fixture
def mock_cpuinfo_pi4(tmp_path):
    """Create a mock /proc/cpuinfo file for Pi 4."""
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("""processor	: 0
BogoMIPS	: 108.00
Features	: fp asimd evtstrm crc32 cpuid
CPU implementer	: 0x41
CPU architecture: 8
CPU variant	: 0x0
CPU part	: 0xd08
CPU revision	: 3

Hardware	: BCM2711
Revision	: c03111
Serial		: 100000001234abcd
Model		: Raspberry Pi 4 Model B Rev 1.1
""")
    return cpuinfo


@pytest.fixture
def mock_cpuinfo_pi5(tmp_path):
    """Create a mock /proc/cpuinfo file for Pi 5."""
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("""processor	: 0
BogoMIPS	: 108.00
Features	: fp asimd evtstrm crc32 cpuid
CPU implementer	: 0x41
CPU architecture: 8
CPU variant	: 0x4
CPU part	: 0xd0b
CPU revision	: 1

Hardware	: BCM2712
Revision	: c04170
Serial		: 100000001234abcd
Model		: Raspberry Pi 5 Model B Rev 1.0
""")
    return cpuinfo


@pytest.fixture
def mock_cpuinfo_pi2(tmp_path):
    """Create a mock /proc/cpuinfo file for Pi 2 (unsupported)."""
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("""processor	: 0
model name	: ARMv7 Processor rev 5 (v7l)
BogoMIPS	: 57.60
Features	: half thumb fastmult vfp edsp neon vfpv3 tls vfpv4 idiva idivt vfpd32 lpae evtstrm
CPU implementer	: 0x41
CPU architecture: 7
CPU variant	: 0x0
CPU part	: 0xc07
CPU revision	: 5

Hardware	: BCM2709
Revision	: a01041
Serial		: 00000000deadbeef
Model		: Raspberry Pi 2 Model B Rev 1.1
""")
    return cpuinfo


@pytest.fixture
def mock_cpuinfo_pi1(tmp_path):
    """Create a mock /proc/cpuinfo file for Pi 1 (unsupported)."""
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("""processor	: 0
model name	: ARMv6-compatible processor rev 7 (v6l)
BogoMIPS	: 697.95
Features	: half thumb fastmult vfp edsp java tls
CPU implementer	: 0x41
CPU architecture: 7
CPU variant	: 0x0
CPU part	: 0xb76
CPU revision	: 7

Hardware	: BCM2708
Revision	: 0010
Serial		: 00000000cafebabe
Model		: Raspberry Pi Model B Plus Rev 1.2
""")
    return cpuinfo
