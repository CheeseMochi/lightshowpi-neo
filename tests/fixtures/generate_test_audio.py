#!/usr/bin/env python3
"""
Generate test audio files for decoder tests.

Creates simple test audio files with known properties:
- Stereo 440Hz sine wave (1 second)
- Sample rate: 44100 Hz
- Channels: 2 (stereo)
- Format: WAV (uncompressed, widely supported)
"""
import numpy as np
import soundfile as sf
from pathlib import Path


def generate_test_tone(
    filename='test_tone.wav',
    frequency=440.0,
    duration=1.0,
    sample_rate=44100,
    channels=2
):
    """Generate a test tone audio file.

    Args:
        filename: Output filename
        frequency: Tone frequency in Hz (default 440Hz = A4)
        duration: Duration in seconds
        sample_rate: Sample rate in Hz
        channels: Number of channels (1=mono, 2=stereo)
    """
    # Generate time array
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)

    # Generate sine wave
    audio = np.sin(2 * np.pi * frequency * t)

    # Convert to stereo if needed
    if channels == 2:
        audio = np.column_stack([audio, audio])

    # Normalize to 16-bit range
    audio = (audio * 32767).astype(np.int16)

    # Get output path
    output_path = Path(__file__).parent / filename

    # Write audio file
    sf.write(str(output_path), audio, sample_rate, subtype='PCM_16')

    print(f"Generated: {output_path}")
    print(f"  Duration: {duration}s")
    print(f"  Sample rate: {sample_rate} Hz")
    print(f"  Channels: {channels}")
    print(f"  Frequency: {frequency} Hz")
    print(f"  File size: {output_path.stat().st_size} bytes")

    return output_path


def main():
    """Generate all test audio fixtures."""
    # Standard test tone (440Hz, 1 second, stereo)
    generate_test_tone('test_tone.wav', frequency=440.0, duration=1.0, channels=2)

    # Short test tone (0.5 seconds) for quick tests
    generate_test_tone('test_tone_short.wav', frequency=440.0, duration=0.5, channels=2)

    # Mono test tone
    generate_test_tone('test_tone_mono.wav', frequency=440.0, duration=1.0, channels=1)


if __name__ == '__main__':
    main()
