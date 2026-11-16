"""Chunk timing helpers for the show loop.

The show loop reads audio in chunks and delays the lights by a whole number of
chunks (``light_delay``). How many chunks make up one second depends on what a
"chunk" is for the audio source:

* file playback (``decoder.readframes``) and ALSA ``audio-in``: ``chunk_size``
  **frames** per read, so ``sample_rate / chunk_size`` chunks per second;
* ``stream-in``: ``chunk_size`` **bytes** per read, so
  ``bytes_per_second / chunk_size`` chunks per second.

The legacy code used the byte formula everywhere, which makes ``light_delay``
2x too long for mono files and 4x too long for stereo files. Existing shows are
tuned against that, so the legacy maths stays the default this season
(``light_delay_legacy_math = True``) and the corrected maths is opt-in.
"""

BYTES_PER_SAMPLE = 2  # S16_LE everywhere in the show loop

UNIT_FRAMES = 'frames'
UNIT_BYTES = 'bytes'


def chunks_per_second(sample_rate, num_channels, chunk_size, unit=UNIT_FRAMES,
                      legacy=True):
    """Return how many chunks of audio make up one second.

    :param sample_rate: audio sample rate in Hz
    :param num_channels: number of interleaved audio channels
    :param chunk_size: size of one chunk, in ``unit``
    :param unit: ``'frames'`` or ``'bytes'``, what one read returns
    :param legacy: use the original byte-based formula regardless of ``unit``
    :rtype: float
    """
    bytes_per_second = BYTES_PER_SAMPLE * num_channels * sample_rate
    if legacy or unit == UNIT_BYTES:
        return bytes_per_second / chunk_size
    if unit == UNIT_FRAMES:
        return sample_rate / chunk_size
    raise ValueError("unknown chunk unit: %r" % (unit,))


def light_delay_chunks(light_delay_s, sample_rate, num_channels, chunk_size,
                       unit=UNIT_FRAMES, legacy=True):
    """Return ``light_delay`` (seconds) as a whole number of chunks."""
    return int(light_delay_s * chunks_per_second(sample_rate, num_channels,
                                                 chunk_size, unit, legacy))


def expected_chunk_bytes(num_channels, chunk_size, unit=UNIT_FRAMES):
    """Return the byte length of one complete chunk from the audio source."""
    if unit == UNIT_BYTES:
        return chunk_size
    if unit == UNIT_FRAMES:
        return chunk_size * num_channels * BYTES_PER_SAMPLE
    raise ValueError("unknown chunk unit: %r" % (unit,))
