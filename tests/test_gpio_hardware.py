#!/usr/bin/env python
"""
GPIO loopback tests for the LightShowPi Neo hardware test bench.

These tests drive real GPIO pins and read them back through jumper wires, so
they only run on a Raspberry Pi wired as described in tests/bench.yaml, and
only when LSP_BENCH=1 (tests marked ``gpio`` are skipped otherwise, see
tests/conftest.py).

    # On the bench Pi, from the repo root (user must be in the gpio group,
    # or run pytest with sudo)
    LSP_BENCH=1 pytest -m gpio

    # Same thing, convenience wrapper
    python tests/test_gpio_hardware.py

What is covered, for every [output, input] pair in bench.yaml:
  - digital HIGH/LOW loopback through gpio_adapter
  - hardware_controller.Channel on/off, active_low and inverted overrides
  - soft PWM duty cycle (gpio_adapter and Channel), measured by sampling the
    input pin and comparing the fraction of HIGH samples (tolerance-based)

The input side is claimed and read directly through lgpio so that failures
raise instead of being logged and swallowed by the adapter.

The bench.yaml loader and validator also have plain unit tests here, which
run everywhere.
"""
import glob
import importlib.util
import os
import re
import signal
import sys
import time
from pathlib import Path

import pytest

BENCH_CONFIG_ENV = "LSP_BENCH_CONFIG"
DEFAULT_BENCH_CONFIG = Path(__file__).with_name("bench.yaml")

BENCH_DEFAULTS = {
    "settle_seconds": 0.005,
    "pwm_sample_seconds": 0.1,
    "pwm_tolerance": 0.10,
    "pwm_min_samples": 200,
}

# BCM GPIOs available on the 40-pin header
HEADER_GPIO_RANGE = range(0, 28)


# ============================================================================
# bench.yaml loading
# ============================================================================

_PAIR_RE = re.compile(r"^\s*-\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]\s*$")
_KEY_RE = re.compile(r"^([A-Za-z_]\w*)\s*:\s*(.*?)\s*$")


def parse_simple_yaml(text):
    """Parse the small YAML subset bench.yaml uses, without PyYAML.

    Supports top-level ``key: number`` lines, a top-level ``key:`` followed by
    ``- [a, b]`` items, and ``#`` comments. Anything else raises ValueError.
    """
    data = {}
    current_list = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        match = _PAIR_RE.match(line)
        if match:
            if current_list is None:
                raise ValueError(f"bench config: list item outside a list: {raw!r}")
            current_list.append([int(match.group(1)), int(match.group(2))])
            continue
        match = _KEY_RE.match(line)
        if not match:
            raise ValueError(f"bench config: unsupported line: {raw!r}")
        key, value = match.groups()
        if value == "":
            current_list = data[key] = []
        else:
            current_list = None
            try:
                data[key] = int(value)
            except ValueError:
                data[key] = float(value)
    # Match PyYAML, which loads an empty "key:" as None
    return {k: (v if v != [] else None) for k, v in data.items()}


def validate_bench_config(data):
    """Validate parsed bench config and merge defaults.

    Returns a dict with ``pairs`` as a list of (output, input) tuples plus the
    numeric settings. Raises ValueError with a readable message on bad input.
    """
    data = dict(data or {})
    pairs = data.pop("pairs", None)

    unknown = set(data) - set(BENCH_DEFAULTS)
    if unknown:
        raise ValueError(f"bench config: unknown keys {sorted(unknown)}")

    if not pairs:
        raise ValueError("bench config: 'pairs' must list at least one [output, input] pair")

    normalized = []
    for pair in pairs:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(isinstance(p, int) and not isinstance(p, bool) for p in pair)):
            raise ValueError(f"bench config: pair {pair!r} must be [output BCM, input BCM]")
        if not all(p in HEADER_GPIO_RANGE for p in pair):
            raise ValueError(f"bench config: pair {pair!r} uses a pin outside BCM 0-27")
        normalized.append((pair[0], pair[1]))

    pins = [p for pair in normalized for p in pair]
    duplicates = sorted({p for p in pins if pins.count(p) > 1})
    if duplicates:
        raise ValueError(f"bench config: BCM pins used more than once: {duplicates}")

    config = dict(BENCH_DEFAULTS)
    config.update(data)
    for key, value in config.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"bench config: {key} must be a positive number, got {value!r}")
    config["pairs"] = normalized
    return config


def load_bench_config(path=None):
    """Load and validate bench.yaml (PyYAML if installed, else the simple parser)."""
    path = Path(path or os.environ.get(BENCH_CONFIG_ENV) or DEFAULT_BENCH_CONFIG)
    text = path.read_text()
    try:
        import yaml
    except ImportError:
        data = parse_simple_yaml(text)
    else:
        data = yaml.safe_load(text)
    return validate_bench_config(data)


BENCH = load_bench_config()
PAIRS = BENCH["pairs"]
PAIR_IDS = [f"bcm{o}-bcm{i}" for o, i in PAIRS]


# ============================================================================
# Hardware fixtures and helpers
# ============================================================================

def hardware_skip_reason():
    """Return why GPIO hardware tests can't run here, or None if they can."""
    if not sys.platform.startswith("linux"):
        return f"GPIO bench tests need a Raspberry Pi (running on {sys.platform})"
    try:
        model = Path("/proc/device-tree/model").read_text(errors="ignore").strip("\x00\n ")
    except OSError:
        model = ""
    if "Raspberry Pi" not in model:
        return f"GPIO bench tests need a Raspberry Pi (model: {model or 'unknown'})"
    if importlib.util.find_spec("lgpio") is None:
        return "lgpio is not installed in this environment (see bin/link_lgpio.sh)"
    if not glob.glob("/dev/gpiochip*"):
        return "no /dev/gpiochip* devices found"
    return None


@pytest.fixture
def gpio():
    """gpio_adapter initialised in BCM mode; all pins released afterwards."""
    reason = hardware_skip_reason()
    if reason:
        pytest.skip(reason)

    import gpio_adapter
    gpio_adapter.wiringPiSetupGpio()
    try:
        yield gpio_adapter
    finally:
        gpio_adapter.cleanup()  # stops PWM and closes the chip, releasing claims


@pytest.fixture
def channel_cls(gpio, monkeypatch):
    """hardware_controller.Channel, wired to the initialised gpio_adapter."""
    previous_sigint = signal.getsignal(signal.SIGINT)
    import hardware_controller
    # hardware_controller ignores SIGINT at import; keep the runner cancellable
    signal.signal(signal.SIGINT, previous_sigint)
    # It falls back to the no-op wiring_pi stub if platform detection misses
    monkeypatch.setattr(hardware_controller, "wiringpi", gpio)
    return hardware_controller.Channel


class LoopbackInput:
    """The input side of a loopback pair, claimed and read directly via lgpio."""

    def __init__(self, gpio_adapter, pin):
        self.lgpio = gpio_adapter.lgpio
        self.handle = gpio_adapter._chip_handle
        self.pin = pin
        try:
            # Pull-down so a missing jumper reads LOW and fails the HIGH check
            self.lgpio.gpio_claim_input(self.handle, pin, self.lgpio.SET_PULL_DOWN)
        except self.lgpio.error:
            self.lgpio.gpio_claim_input(self.handle, pin)

    def read(self):
        return self.lgpio.gpio_read(self.handle, self.pin)

    def duty(self, seconds):
        """Sample the pin for ``seconds``; return (fraction HIGH, sample count)."""
        highs = samples = 0
        read, handle, pin = self.lgpio.gpio_read, self.handle, self.pin
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            highs += read(handle, pin)
            samples += 1
        return highs / max(samples, 1), samples


def settle():
    time.sleep(BENCH["settle_seconds"])


def assert_level(lb, expected, out_pin, what):
    actual = lb.read()
    assert actual == expected, (
        f"{what}: expected BCM {lb.pin} to read {expected}, got {actual}. "
        f"Check the jumper BCM {out_pin} -> BCM {lb.pin}.")


def assert_duty(lb, expected, out_pin, what):
    # Let soft PWM run a couple of periods (100 Hz) before sampling
    time.sleep(max(BENCH["settle_seconds"], 0.03))
    measured, samples = lb.duty(BENCH["pwm_sample_seconds"])
    assert samples >= BENCH["pwm_min_samples"], (
        f"{what}: only {samples} samples in {BENCH['pwm_sample_seconds']}s; "
        "raise pwm_sample_seconds or lower pwm_min_samples in bench.yaml")
    tolerance = BENCH["pwm_tolerance"]
    assert abs(measured - expected) <= tolerance, (
        f"{what}: duty on BCM {lb.pin} measured {measured:.3f}, expected "
        f"{expected:.3f} +/- {tolerance} ({samples} samples, output BCM {out_pin})")


# ============================================================================
# Hardware tests (gpio marker: skipped unless LSP_BENCH=1)
# ============================================================================

@pytest.mark.gpio
@pytest.mark.parametrize("out_pin, in_pin", PAIRS, ids=PAIR_IDS)
class TestDigitalLoopback:
    """Digital writes through gpio_adapter, read back on the paired input."""

    def test_high_low(self, gpio, out_pin, in_pin):
        lb = LoopbackInput(gpio, in_pin)
        gpio.pinModePY(out_pin, gpio.OUTPUT)
        for _ in range(3):
            for level in (gpio.HIGH, gpio.LOW):
                gpio.digitalWritePY(out_pin, level)
                settle()
                assert_level(lb, level, out_pin, f"digitalWrite {level}")


@pytest.mark.gpio
@pytest.mark.parametrize("out_pin, in_pin", PAIRS, ids=PAIR_IDS)
class TestChannelLoopback:
    """hardware_controller.Channel (on/off mode) driving a real pin."""

    def test_on_off(self, channel_cls, gpio, out_pin, in_pin):
        lb = LoopbackInput(gpio, in_pin)
        channel = channel_cls(out_pin, False, False, 100)
        channel.set_as_output()
        channel.set_action(brightness=1.0)
        settle()
        assert_level(lb, 1, out_pin, "channel on")
        channel.set_action(brightness=0.0)
        settle()
        assert_level(lb, 0, out_pin, "channel off")

    def test_active_low(self, channel_cls, gpio, out_pin, in_pin):
        lb = LoopbackInput(gpio, in_pin)
        channel = channel_cls(out_pin, False, True, 100)
        channel.set_as_output()
        channel.set_action(brightness=1.0)
        settle()
        assert_level(lb, 0, out_pin, "active_low channel on")
        channel.set_action(brightness=0.0)
        settle()
        assert_level(lb, 1, out_pin, "active_low channel off")

    def test_inverted_override(self, channel_cls, gpio, out_pin, in_pin):
        lb = LoopbackInput(gpio, in_pin)
        channel = channel_cls(out_pin, False, False, 100)
        channel.set_as_output()
        channel.set_inverted(True)

        channel.set_action(use_overrides=True, brightness=1.0)
        settle()
        assert_level(lb, 0, out_pin, "inverted channel on (overrides)")
        channel.set_action(use_overrides=True, brightness=0.0)
        settle()
        assert_level(lb, 1, out_pin, "inverted channel off (overrides)")

        # Inversion only applies when overrides are in use
        channel.set_action(use_overrides=False, brightness=1.0)
        settle()
        assert_level(lb, 1, out_pin, "inverted channel on (no overrides)")


@pytest.mark.gpio
@pytest.mark.parametrize("out_pin, in_pin", PAIRS, ids=PAIR_IDS)
class TestSoftPwmLoopback:
    """Soft PWM duty cycle, approximated by sampling the paired input."""

    @pytest.mark.parametrize("duty", [0, 25, 50, 75, 100])
    def test_adapter_duty(self, gpio, out_pin, in_pin, duty):
        lb = LoopbackInput(gpio, in_pin)
        assert gpio.softPwmCreatePY(out_pin, 0, 100) == 0, "softPwmCreatePY failed"
        gpio.softPwmWritePY(out_pin, duty)
        assert_duty(lb, duty / 100.0, out_pin, f"softPwmWrite {duty}/100")

    @pytest.mark.parametrize("active_low", [False, True], ids=["active_high", "active_low"])
    @pytest.mark.parametrize("brightness", [0.25, 0.75])
    def test_channel_duty(self, channel_cls, gpio, out_pin, in_pin, active_low, brightness):
        lb = LoopbackInput(gpio, in_pin)
        channel = channel_cls(out_pin, True, active_low, 100)
        channel.set_as_output()
        assert out_pin in gpio._pwm_pins, "Channel.set_as_output did not create soft PWM"
        channel.set_action(brightness=brightness)
        expected = 1.0 - brightness if active_low else brightness
        assert_duty(lb, expected, out_pin,
                    f"PWM channel brightness {brightness} (active_low={active_low})")


# ============================================================================
# bench.yaml unit tests (run everywhere)
# ============================================================================

@pytest.mark.unit
class TestBenchConfig:
    """The bench config loader and the checked-in bench.yaml."""

    def test_checked_in_bench_yaml_is_valid(self):
        config = load_bench_config(DEFAULT_BENCH_CONFIG)
        assert config["pairs"], "bench.yaml must define at least one pair"
        assert (17, 27) in config["pairs"]

    def test_simple_parser_matches_pyyaml(self):
        yaml = pytest.importorskip("yaml")
        text = DEFAULT_BENCH_CONFIG.read_text()
        assert parse_simple_yaml(text) == yaml.safe_load(text)

    def test_simple_parser_reads_pairs_and_settings(self):
        text = (
            "# comment\n"
            "pairs:\n"
            "  - [17, 27]  # pin 11 -> 13\n"
            "#  - [5, 6]\n"
            "  - [22, 23]\n"
            "pwm_tolerance: 0.2\n"
            "pwm_min_samples: 50\n"
        )
        assert parse_simple_yaml(text) == {
            "pairs": [[17, 27], [22, 23]],
            "pwm_tolerance": 0.2,
            "pwm_min_samples": 50,
        }

    @pytest.mark.parametrize("text", [
        "  - [1, 2]\n",                 # list item with no list
        "pairs:\n  - 17\n",             # not a pair
        "pwm:\n  tolerance: 0.1\n",     # nesting is not supported
        "pwm_tolerance: high\n",        # not a number
    ])
    def test_simple_parser_rejects_unsupported(self, text):
        with pytest.raises(ValueError):
            parse_simple_yaml(text)

    def test_defaults_are_applied(self):
        config = validate_bench_config({"pairs": [[17, 27]]})
        assert config["pairs"] == [(17, 27)]
        for key, value in BENCH_DEFAULTS.items():
            assert config[key] == value

    @pytest.mark.parametrize("data, message", [
        ({}, "at least one"),
        ({"pairs": None}, "at least one"),
        ({"pairs": [[17]]}, "must be"),
        ({"pairs": [[17, "27"]]}, "must be"),
        ({"pairs": [[17, 40]]}, "outside"),
        ({"pairs": [[17, 27], [27, 22]]}, "more than once"),
        ({"pairs": [[17, 17]]}, "more than once"),
        ({"pairs": [[17, 27]], "pwm_tolerance": 0}, "positive"),
        ({"pairs": [[17, 27]], "typo_key": 1}, "unknown keys"),
    ])
    def test_validation_errors(self, data, message):
        with pytest.raises(ValueError, match=message):
            validate_bench_config(data)

    def test_env_var_overrides_path(self, tmp_path, monkeypatch):
        custom = tmp_path / "bench.yaml"
        custom.write_text("pairs:\n  - [5, 6]\n")
        monkeypatch.setenv(BENCH_CONFIG_ENV, str(custom))
        assert load_bench_config()["pairs"] == [(5, 6)]


if __name__ == "__main__":
    # Convenience: run the bench tests in this file on the Pi
    os.environ.setdefault("LSP_BENCH", "1")
    sys.exit(pytest.main([__file__, "-m", "gpio", "-v", *sys.argv[1:]]))
