# Contributing to LightShowPi Neo

Thank you for your interest in contributing to LightShowPi Neo! We welcome contributions from everyone, whether you're fixing a bug, adding a feature, improving documentation, or helping with testing.

> **Note:** LightShowPi Neo is an independent fork of the original LightShowPi. See [FORK.md](FORK.md) for details about the relationship between the projects.

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How Can I Contribute?](#how-can-i-contribute)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Commit Messages](#commit-messages)
- [Pull Request Process](#pull-request-process)
- [Community](#community)

---

## 🤝 Code of Conduct

### Standards

**Examples of behavior that contributes to a positive environment:**
- Using welcoming and inclusive language
- Being respectful of differing viewpoints and experiences
- Gracefully accepting constructive criticism
- Focusing on what is best for the community
- Showing empathy towards other community members

**Examples of unacceptable behavior:**
- Trolling, insulting/derogatory comments, and personal or political attacks
- Public or private harassment
- Publishing others' private information without explicit permission
- The use of sexualized language or imagery
- Other conduct which could reasonably be considered inappropriate

### Enforcement

Instances of abusive, harassing, or otherwise unacceptable behavior may be reported by contacting the project team via [GitHub Issues](../../issues). All complaints will be reviewed and investigated promptly and fairly.

---

## 💡 How Can I Contribute?

### Reporting Bugs

Before creating a bug report, please check the [existing issues](../../issues) to see if the problem has already been reported.

**When reporting a bug, include:**
- **Clear title and description**
- **Steps to reproduce** the issue
- **Expected behavior** vs. **actual behavior**
- **Environment details:**
  - Raspberry Pi model (3, 4, or 5)
  - Python version (`python --version`)
  - OS version (`cat /etc/os-release`)
  - LightShowPi version
- **Logs** (from `logs/` directory)
- **Configuration** (relevant sections from `config/overrides.cfg`)
- **Screenshots or videos** if applicable

**Use the bug report template** when creating a new issue.

### Suggesting Features

We love new ideas! Before creating a feature request:

1. **Check existing issues** for similar requests
2. **Consider if it fits** the project's scope and goals
3. **Think about implementation** - how would it work?

**When suggesting a feature, include:**
- **Clear use case** - why is this needed?
- **Proposed solution** - how should it work?
- **Alternatives** - what other approaches did you consider?
- **Additional context** - mockups, examples, etc.

**Use the feature request template** when creating a new issue.

### Improving Documentation

Documentation improvements are always welcome!

- **README.md** - Installation, usage, configuration
- **Code comments** - Explain complex logic
- **API documentation** - Document functions and classes
- **Examples** - Add usage examples
- **Tutorials** - Create step-by-step guides

---

## 🛠️ Development Setup

### Prerequisites

- **Hardware:** Raspberry Pi 3, 4, or 5 (for hardware testing)
- **Software:** Python 3.9+, git
- **Development Machine:** Can be macOS/Linux/Windows for code changes

### Fork and Clone

```bash
# Fork the repository on GitHub first, then:
git clone https://github.com/CheeseMochi/lightshowpi-neo.git
cd lightshowpi-neo

# Add upstream remote
git remote add upstream https://github.com/CheeseMochi/lightshowpi-neo.git
```

### Set Up Environment

```bash
# Set environment variable
export SYNCHRONIZED_LIGHTS_HOME=$(pwd)
echo "export SYNCHRONIZED_LIGHTS_HOME=$(pwd)" >> ~/.bashrc

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements-dev.txt
```

### Verify Setup

```bash
# Run tests
pytest -v

# Tests should pass (some may skip on non-Linux systems)
```

---

## ✏️ Making Changes

### Creating a Branch

Always create a new branch for your changes:

```bash
# Update your fork
git checkout main
git pull upstream main

# Create a feature branch
git checkout -b feature/your-feature-name

# Or a bugfix branch
git checkout -b fix/issue-123-description
```

### Branch Naming Conventions

- `feature/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation changes
- `test/` - Test improvements
- `refactor/` - Code refactoring

### Making Commits

Make small, focused commits that address one thing at a time:

```bash
# Stage your changes
git add path/to/changed/file

# Commit with a descriptive message
git commit -m "Add feature: description of what changed"

# Push to your fork
git push origin feature/your-feature-name
```

---

## 📝 Coding Standards

### Python Style Guide

We follow **PEP 8** with some project-specific conventions:

```python
# Use 4 spaces for indentation (no tabs)
def example_function(param1, param2):
    """Docstring explaining what this function does.

    Args:
        param1: Description of param1
        param2: Description of param2

    Returns:
        Description of return value
    """
    result = param1 + param2
    return result


# Class naming: PascalCase
class AudioProcessor:
    """Class for processing audio files."""

    def __init__(self, sample_rate=44100):
        self.sample_rate = sample_rate

    def process(self, audio_data):
        """Process audio data and return frequencies."""
        pass


# Constants: UPPER_CASE
DEFAULT_SAMPLE_RATE = 44100
MAX_FREQUENCY = 15000

# Variables/functions: snake_case
def calculate_fft(audio_samples):
    sample_count = len(audio_samples)
    return fft_result
```

### Code Formatting

```bash
# Format your code with black
black py/

# Check for style issues
flake8 py/

# Type checking (optional but recommended)
mypy py/
```

### Configuration Standards

```ini
# Configuration files use INI format
# Comments should explain non-obvious settings

[section_name]
# Comment explaining what this does
setting_name = value

# Prefer lowercase with underscores
gpio_pins = 0,1,2,3,4,5,6,7
sample_rate = 44100
```

### Logging

Use proper logging instead of print statements:

```python
import logging

log = logging.getLogger(__name__)

# Use appropriate log levels
log.debug("Detailed information for debugging")
log.info("General information about program execution")
log.warning("Something unexpected but not critical")
log.error("An error occurred but program can continue")
log.critical("Critical error, program may not continue")
```

---

## 🧪 Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_platform.py

# Run tests matching a pattern
pytest -k "test_config"

# Run with coverage
pytest --cov=py --cov-report=html
```

### Writing Tests

All new features should include tests:

```python
import pytest

@pytest.mark.unit
class TestYourFeature:
    """Test your new feature."""

    def test_basic_functionality(self):
        """Test that the basic functionality works."""
        result = your_function()
        assert result == expected_value

    def test_edge_case(self):
        """Test edge cases and error handling."""
        with pytest.raises(ValueError):
            your_function(invalid_input)
```

### Test Categories

Use pytest markers to categorize tests:

```python
@pytest.mark.unit         # Unit tests (fast, no hardware)
@pytest.mark.integration  # Integration tests
@pytest.mark.gpio         # Requires GPIO hardware
@pytest.mark.audio        # Requires audio hardware
@pytest.mark.platform     # Platform-specific (Pi only)
@pytest.mark.slow         # Slow tests (>1 second)
```

### Mocking Hardware

For GPIO and hardware tests, use mocks:

```python
from unittest.mock import Mock, patch

@patch('gpio_adapter.lgpio')
def test_gpio_function(mock_lgpio):
    """Test GPIO function without actual hardware."""
    mock_lgpio.gpio_claim_output.return_value = 0

    # Your test code here
    result = setup_pin(17)

    assert mock_lgpio.gpio_claim_output.called
```

---

## 📬 Commit Messages

### Format

```
<type>: <subject>

<body>

<footer>
```

### Types

- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `test:` - Adding or updating tests
- `refactor:` - Code refactoring
- `perf:` - Performance improvements
- `style:` - Code style changes (formatting, etc.)
- `build:` - Build system changes
- `ci:` - CI/CD changes

### Examples

**Good commit messages:**

```
feat: Add support for WS2812 LED strips

Adds configuration and control for individually addressable
WS2812 LED strips via SPI interface. Includes per-LED color
control and pattern support.

Closes #123
```

```
fix: Correct FFT frequency calculation for 48kHz audio

The FFT was using hardcoded 44.1kHz sample rate instead of
reading from the audio file. Now correctly detects and uses
actual sample rate.

Fixes #456
```

```
docs: Update README with Pi 5 compatibility info

Adds Pi 5 to supported hardware list and updates
performance recommendations.
```

**Bad commit messages:**

```
❌ Fixed stuff
❌ WIP
❌ More changes
❌ asdf
```

---

## 🔄 Pull Request Process

### Before Submitting

1. **Update your branch** with latest upstream changes:
   ```bash
   git checkout main
   git pull upstream main
   git checkout feature/your-feature
   git rebase main
   ```

2. **Run tests** and ensure they pass:
   ```bash
   pytest -v
   ```

3. **Format your code**:
   ```bash
   black py/
   flake8 py/
   ```

4. **Update documentation** if needed

5. **Add tests** for new functionality

### Submitting

1. **Push to your fork**:
   ```bash
   git push origin feature/your-feature
   ```

2. **Create Pull Request** on GitHub:
   - Use the PR template
   - Reference related issues
   - Describe what changed and why
   - Include screenshots/videos if relevant
   - Add `Closes #123` to auto-close issues

3. **Respond to review feedback**:
   - Make requested changes
   - Push additional commits to same branch
   - Respond to questions/comments

### PR Guidelines

**Good PRs:**
- ✅ Single focused change
- ✅ Tests included
- ✅ Documentation updated
- ✅ All tests passing
- ✅ Code formatted
- ✅ Clear description

**PRs that need work:**
- ❌ Multiple unrelated changes
- ❌ No tests
- ❌ Failing tests
- ❌ No description
- ❌ Merge conflicts

### Review Process

1. **Automated checks** run (tests, linting)
2. **Maintainer review** (usually within 1-2 weeks)
3. **Feedback and iteration**
4. **Approval and merge**

### After Merge

1. **Delete your branch**:
   ```bash
   git checkout main
   git branch -d feature/your-feature
   git push origin --delete feature/your-feature
   ```

2. **Update your main branch**:
   ```bash
   git pull upstream main
   ```

3. **Celebrate!** 🎉 You're now a LightShowPi Neo contributor!

---

## 💬 Community

### Getting Help

**LightShowPi Neo:**
- **Issues:** [GitHub Issues](../../issues)
- **Discussions:** [GitHub Discussions](../../discussions)

**Original LightShowPi Community:**
- **Reddit:** [r/LightShowPi](https://www.reddit.com/r/LightShowPi/)
- **Website:** [lightshowpi.org](http://lightshowpi.org/)

### Staying Updated

- **Watch the repository** for notifications
- **Star the repository** to show support
- **Follow releases** for new versions

---

## 🎓 First Time Contributing?

**New to open source? Here are some great resources:**
- [How to Contribute to Open Source](https://opensource.guide/how-to-contribute/)
- [First Contributions](https://github.com/firstcontributions/first-contributions)
- [GitHub Flow Guide](https://guides.github.com/introduction/flow/)

**Easy ways to start:**
- Fix typos in documentation
- Improve code comments
- Add tests to increase coverage
- Report bugs with detailed information
- Help answer questions in issues

---

## 📜 License

By contributing to LightShowPi Neo, you agree that your contributions will be licensed under the BSD 2-Clause License. See [LICENSE](LICENSE) for details.

---

## 🙏 Thank You!

Your contributions make LightShowPi Neo better for everyone. Whether you're submitting code, reporting bugs, improving documentation, or sharing your amazing light displays, we appreciate your involvement in the community!

---

**Questions about contributing?** Open a [discussion](../../discussions)
