"""
Per-song sequence selection: FFT, the legacy CSV beside the audio, or a named
variant CSV (optionally rotated or randomized across several).

A song opts in with ``sequence`` in the ``[custom_lightshow]`` section of its
``.<song-file>.cfg``::

    sequence = fft                          # always the FFT show, ignore any CSV
    sequence = csv                          # the legacy <song>.csv beside the audio
    sequence = onset_bands                  # <sequences_dir>/<song>/onset_bands.csv
    sequence = rotate:onset_bands,stems,fft # next one each time the song plays
    sequence = random:onset_bands,stems     # a random one each time

``<song>`` is the audio file name without its extension. With no ``sequence``
key the show keeps its original behaviour (see synchronized_lights.get_song);
this module is only consulted when the key is set, and then never raises:
a missing or malformed variant falls back to the legacy CSV if it is valid,
else to FFT, with a logged warning.

Pure apart from file reads and the state callbacks, so it can be tested and
reused by the API and tools without the audio stack.
"""

import configparser
import logging
import os
import random as _random
import re
from collections import namedtuple

log = logging.getLogger(__name__)

FFT = 'fft'
LEGACY = 'csv'
MODES = ('rotate', 'random')
RESERVED = frozenset((FFT, LEGACY) + MODES)

SECTION = 'custom_lightshow'
KEY = 'sequence'

# Variant names become file names, so keep them to a safe charset
VARIANT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')

# Rotation index state key prefix; one key per song (state.cfg option names are
# lower-cased by configparser, so sanitize to [a-z0-9_]).
ROTATION_PREFIX = 'seq_rot_'

# One-shot audition: the API sets ``sequence_once = <song stem>|<variant>``
# before ``play_now``; the next get_song() takes (and always clears) it.
ONCE_KEY = 'sequence_once'
ONCE_SEP = '|'

Selection = namedtuple('Selection', 'sequence_type filename name')
"""sequence_type is 'auto' (FFT) or 'csv'; filename is the CSV to play or None;
name is what played: 'fft', 'csv' (legacy file) or the variant name."""

Spec = namedtuple('Spec', 'mode choices')
"""mode is 'single', 'rotate' or 'random'; choices is a tuple of names, each
'fft', 'csv' or a variant name."""


def song_stem(song_path):
    """Audio file name without directory or extension ('carol.mp3' -> 'carol')."""
    return os.path.splitext(os.path.basename(song_path))[0]


def legacy_csv_path(song_path):
    """The legacy sequence file beside the audio (same rule as get_song)."""
    return os.path.abspath(song_path).rsplit('.', 1)[0] + '.csv'


def variant_dir(sequences_dir, song_path):
    """Directory holding a song's variant CSVs."""
    return os.path.join(sequences_dir, song_stem(song_path))


def variant_path(sequences_dir, song_path, variant):
    """Path of one variant CSV."""
    return os.path.join(variant_dir(sequences_dir, song_path), variant + '.csv')


def is_valid_variant_name(name):
    """True for a usable variant file name (not reserved, safe charset)."""
    return bool(name) and name not in RESERVED and bool(VARIANT_RE.match(name))


def list_variants(sequences_dir, song_path):
    """Sorted variant names that have a CSV for this song (empty if none)."""
    folder = variant_dir(sequences_dir, song_path)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found = []
    for entry in names:
        stem, ext = os.path.splitext(entry)
        if ext == '.csv' and is_valid_variant_name(stem) and \
                os.path.isfile(os.path.join(folder, entry)):
            found.append(stem)
    return sorted(found)


def parse_spec(value):
    """Parse a ``sequence`` value into a Spec.

    :raises ValueError: on an empty, malformed or unsafe value
    """
    text = (value or '').strip()
    if not text:
        raise ValueError('empty sequence setting')

    mode = 'single'
    body = text
    if ':' in text:
        mode, body = (part.strip() for part in text.split(':', 1))
        mode = mode.lower()
        if mode not in MODES:
            raise ValueError("unknown sequence mode '%s' (use rotate: or random:)" % mode)

    choices = [c.strip() for c in body.split(',') if c.strip()]
    if not choices:
        raise ValueError("no sequences listed in '%s'" % text)
    if mode == 'single' and len(choices) > 1:
        raise ValueError("several sequences need rotate: or random: ('%s')" % text)

    for i, choice in enumerate(choices):
        if choice.lower() in (FFT, LEGACY):
            choices[i] = choice.lower()
        elif not is_valid_variant_name(choice):
            raise ValueError("invalid sequence name '%s'" % choice)
    return Spec(mode, tuple(choices))


def read_sequence_setting(config_filename):
    """Return the raw ``sequence`` value from a per-song cfg, or None.

    None (missing file, section or key, a blank value, or an unreadable file)
    means "keep the legacy behaviour".
    """
    if not config_filename or not os.path.isfile(config_filename):
        return None
    config = configparser.RawConfigParser(allow_no_value=True)
    try:
        with open(config_filename) as f:
            config.read_file(f)
        value = config.get(SECTION, KEY, fallback=None)
    except (configparser.Error, OSError, UnicodeDecodeError) as error:
        log.warning("Could not read sequence setting from %s: %s", config_filename, error)
        return None
    if value is None or not value.strip():
        return None
    return value.strip()


def check_csv(path, channel_count, full=True):
    """Validate a sequence CSV; return None if usable, else a reason string.

    Checks it exists, is non-empty and has ``channel_count`` numeric columns on
    the first row (the legacy check) or, with ``full``, on every row, so a bad
    row can't crash the show mid-song.
    """
    if not os.path.isfile(path):
        return 'not found'
    rows = 0
    try:
        with open(path) as f:
            for line in f:
                rows += 1
                fields = line.rstrip().split(',')
                if len(fields) != channel_count:
                    return 'row %d has %d columns, expected %d (gpio_len)' % (
                        rows, len(fields), channel_count)
                try:
                    [float(x) for x in fields]
                except ValueError:
                    return 'row %d is not numeric' % rows
                if not full:
                    break
    except (OSError, UnicodeDecodeError) as error:
        return str(error)
    if rows == 0:
        return 'empty file'
    return None


def once_value(song_path, variant):
    """State value for a one-shot audition of ``variant`` on this song."""
    return song_stem(song_path) + ONCE_SEP + variant


def take_once(song_path, get_state, update_state):
    """Return the one-shot audition variant for this song, or None.

    Reads state ``sequence_once`` (``<song stem>|<variant>``, see once_value).
    Whenever the key is set it is cleared, matched or not, so a stale request
    (for another song, or with a bad name) never sticks. Never raises.
    """
    try:
        value = get_state(ONCE_KEY, '') or ''
    except Exception as error:  # never let state stop the show
        log.warning("Could not read %s: %s", ONCE_KEY, error)
        return None
    if not value:
        return None
    try:
        update_state(ONCE_KEY, '')
    except Exception as error:
        log.warning("Could not clear %s: %s", ONCE_KEY, error)
    # A stem may contain the separator; a variant name never does
    stem, sep, variant = value.rpartition(ONCE_SEP)
    if not sep or stem != song_stem(song_path):
        log.info("Ignoring one-shot sequence '%s' (not for %s)", value,
                 os.path.basename(song_path))
        return None
    if variant.lower() in RESERVED or not is_valid_variant_name(variant):
        log.warning("Ignoring one-shot sequence with invalid variant '%s'", variant)
        return None
    return variant


def rotation_key(song_path):
    """State key holding the next rotation index for this song."""
    stem = re.sub(r'[^a-z0-9_]', '_', song_stem(song_path).lower())
    return ROTATION_PREFIX + stem


def _pick(spec, song_path, get_state, update_state, rng):
    """Choose one name from the spec, advancing the rotation index if rotating."""
    if spec.mode == 'single' or len(spec.choices) == 1:
        return spec.choices[0]
    if spec.mode == 'random':
        return rng.choice(spec.choices)

    key = rotation_key(song_path)
    try:
        index = int(get_state(key, '0') or 0)
    except (TypeError, ValueError):
        index = 0
    index %= len(spec.choices)
    try:
        update_state(key, str((index + 1) % len(spec.choices)))
    except Exception as error:  # never let a state write stop the show
        log.warning("Could not save sequence rotation for %s: %s", song_path, error)
    return spec.choices[index]


def _legacy_fallback(song_path, channel_count):
    """The legacy CSV beside the audio if it is valid, else FFT."""
    legacy = legacy_csv_path(song_path)
    if os.path.isfile(legacy):
        problem = check_csv(legacy, channel_count, full=False)
        if problem is None:
            return Selection('csv', legacy, LEGACY)
        log.warning("Legacy sequence %s unusable (%s); using FFT", legacy, problem)
    return Selection('auto', None, FFT)


def choose(setting, song_path, sequences_dir, channel_count,
           get_state=lambda key, default='': default,
           update_state=lambda key, value: None,
           rng=_random):
    """Resolve a ``sequence`` setting to what this play of the song uses.

    Never raises: any problem logs a warning and falls back to the legacy
    CSV beside the audio (if valid) or FFT.

    :param setting: raw ``sequence`` value (not None; see read_sequence_setting)
    :param song_path: path of the audio file
    :param sequences_dir: root folder of ``<song>/<variant>.csv``
    :param channel_count: expected CSV columns (gpio_len)
    :param get_state: ``get_state(key, default)`` for the rotation index
    :param update_state: ``update_state(key, value)`` to persist it
    :rtype: Selection
    """
    try:
        spec = parse_spec(setting)
    except ValueError as error:
        log.warning("Ignoring sequence setting for %s: %s", song_path, error)
        return _legacy_fallback(song_path, channel_count)

    try:
        name = _pick(spec, song_path, get_state, update_state, rng)

        if name == FFT:
            return Selection('auto', None, FFT)
        if name == LEGACY:
            return _legacy_fallback(song_path, channel_count)

        path = variant_path(sequences_dir, song_path, name)
        problem = check_csv(path, channel_count, full=True)
        if problem is None:
            log.info("Sequence for %s: %s (%s)", os.path.basename(song_path), name, path)
            return Selection('csv', path, name)
        log.warning("Sequence variant '%s' for %s unusable (%s: %s); falling back",
                    name, song_path, path, problem)
    except Exception as error:  # defensive: the show must keep playing
        log.warning("Sequence selection failed for %s: %s; falling back", song_path, error)
    return _legacy_fallback(song_path, channel_count)
