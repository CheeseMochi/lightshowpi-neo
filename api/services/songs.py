"""
Song library service: the playlist, disabled songs and per-song settings.

Kept small and local on purpose. It never calls ``Configuration.get_playlist``
(which ``sys.exit``s on a missing file) or ``write_playlist`` (which truncates
before it locks, so a show starting mid-write could read an empty playlist).

Files:
- The playlist (``[lightshow] playlist_path``): tab-separated
  ``name<TAB>path[<TAB>votes...]`` lines, the format the show reads. It is
  rewritten in place on the same inode under ``fcntl.lockf(LOCK_EX)``, the
  lock the show takes (shared) to read it, with a ``.bak`` copy first.
- ``<playlist>.disabled``: songs taken out of the show, same format. The show
  never reads it, so disabling a song is the same as removing its line.
- ``.<song-file>.cfg`` beside the audio: ``[custom_lightshow]`` settings the
  show reads at song start. Written atomically (temp file + rename) with a
  ``.bak`` copy; other sections (``[fft]``, ``[custom_audio_processing]``)
  are kept.
"""

import configparser
import csv
import fcntl
import io
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, List, Optional

import sequence_select

log = logging.getLogger(__name__)

SECTION = 'custom_lightshow'

CHANNEL_KEYS = ('always_on_channels', 'always_off_channels', 'invert_channels',
                'network_always_on', 'network_always_off', 'network_invert')
SHOW_PAIRS = (('preshow_configuration', 'preshow_script'),
              ('postshow_configuration', 'postshow_script'))
SETTINGS_KEYS = (('sequence',) + CHANNEL_KEYS + ('attenuate_pct',)
                 + tuple(k for pair in SHOW_PAIRS for k in pair))

_CHANNELS_RE = re.compile(r'^-?\d+(,-?\d+)*$')

# fcntl locks are per process: serialize this process's own writers too
_write_lock = threading.Lock()

_duration_cache: Dict[tuple, Optional[float]] = {}


class SongError(Exception):
    """A request the library can't satisfy; ``status`` is the HTTP code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class SongLibrary:
    """Paths and limits the song endpoints work against."""

    playlist_path: str
    home_dir: str
    sequences_dir: str
    gpio_len: int

    @property
    def disabled_path(self) -> str:
        return self.playlist_path + '.disabled'

    # ------------------------------------------------------------------
    # paths
    # ------------------------------------------------------------------

    def resolve(self, song_path: str) -> str:
        """Absolute audio path for a playlist entry (as the show resolves it)."""
        path = song_path.replace('$SYNCHRONIZED_LIGHTS_HOME', self.home_dir or '')
        if not os.path.isabs(path):
            path = os.path.join(self.home_dir or '', path)
        return os.path.abspath(path)

    def display_path(self, song_path: str) -> str:
        """Path relative to the repo when it lives inside it."""
        path = self.resolve(song_path)
        home = os.path.abspath(self.home_dir or '/')
        if path.startswith(home + os.sep):
            return os.path.relpath(path, home)
        return path

    @staticmethod
    def cfg_path(abs_song: str) -> str:
        """The per-song config beside the audio (same rule as the show)."""
        return os.path.join(os.path.dirname(abs_song), '.' + os.path.basename(abs_song) + '.cfg')

    # ------------------------------------------------------------------
    # playlist
    # ------------------------------------------------------------------

    def read(self) -> Dict[str, List[List[str]]]:
        """Active and disabled rows, read under shared locks."""
        return {'active': _read_rows(self.playlist_path, missing_ok=False),
                'disabled': _read_rows(self.disabled_path, missing_ok=True)}

    def describe(self, row: List[str], index: int, enabled: bool) -> dict:
        """API view of one playlist row."""
        abs_path = self.resolve(row[1])
        exists = os.path.isfile(abs_path)
        cfg = self.cfg_path(abs_path)
        settings = read_settings(cfg)
        legacy = sequence_select.legacy_csv_path(abs_path)
        return {
            'index': index,
            'enabled': enabled,
            'name': row[0],
            'path': self.display_path(row[1]),
            'exists': exists,
            'has_config': os.path.isfile(cfg),
            'sequence': settings.get('sequence') or None,
            'variants': sequence_select.list_variants(self.sequences_dir, abs_path),
            'legacy_csv': os.path.isfile(legacy),
            'duration': _duration(abs_path) if exists else None,
            'votes': len([v for v in row[2].split(',') if v]) if len(row) > 2 else 0,
        }

    def list(self) -> dict:
        """Every song in the playlist, then the disabled ones."""
        rows = self.read()
        return {
            'playlist_path': self.playlist_path,
            'sequences_dir': self.sequences_dir,
            'songs': [self.describe(r, i, True) for i, r in enumerate(rows['active'])],
            'disabled': [self.describe(r, i, False) for i, r in enumerate(rows['disabled'])],
        }

    def row(self, index: int, enabled: bool = True) -> List[str]:
        rows = self.read()['active' if enabled else 'disabled']
        if not 0 <= index < len(rows):
            raise SongError('No song at index %d' % index, 404)
        return rows[index]

    def reorder(self, order: List[int]) -> None:
        """Rewrite the playlist in ``order`` (a permutation of current indices)."""
        def apply(active, disabled):
            if sorted(order) != list(range(len(active))):
                raise SongError('order must list every song index once '
                                '(the playlist has %d songs; reload and retry)' % len(active), 409)
            active[:] = [active[i] for i in order]
        self._modify(apply)

    def update(self, index: int, enabled_list: bool, name: Optional[str] = None,
               enabled: Optional[bool] = None) -> None:
        """Rename a song and/or move it between the playlist and the disabled list."""
        if name is not None:
            name = _clean_name(name)

        def apply(active, disabled):
            rows = active if enabled_list else disabled
            if not 0 <= index < len(rows):
                raise SongError('No song at index %d' % index, 404)
            if name is not None:
                rows[index][0] = name
            if enabled is not None and enabled != enabled_list:
                if not enabled and len(active) <= 1:
                    raise SongError("Can't disable the last song in the playlist", 409)
                row = rows.pop(index)
                (active if enabled else disabled).append(row)
        self._modify(apply)

    def _modify(self, apply) -> None:
        """Run ``apply(active, disabled)`` and rewrite both files, all under lock."""
        with _write_lock:
            if not os.path.isfile(self.playlist_path):
                raise SongError('Playlist not found: %s' % self.playlist_path, 404)
            with open(self.playlist_path, 'r+') as pfp, \
                    open(self.disabled_path, 'a+') as dfp:
                fcntl.lockf(pfp, fcntl.LOCK_EX)
                fcntl.lockf(dfp, fcntl.LOCK_EX)
                try:
                    pfp.seek(0)
                    dfp.seek(0)
                    p_text = pfp.read()
                    active = _parse_rows(p_text)
                    disabled = _parse_rows(dfp.read())
                    before = (_format_rows(active), _format_rows(disabled))
                    apply(active, disabled)
                    after = (_format_rows(active), _format_rows(disabled))
                    if after[0] != before[0]:
                        _atomic_copy_text(self.playlist_path + '.bak', p_text)
                        _rewrite(pfp, after[0])
                    if after[1] != before[1]:
                        _rewrite(dfp, after[1])
                finally:
                    fcntl.lockf(dfp, fcntl.LOCK_UN)
                    fcntl.lockf(pfp, fcntl.LOCK_UN)

    # ------------------------------------------------------------------
    # per-song settings
    # ------------------------------------------------------------------

    def get_settings(self, index: int) -> dict:
        abs_path = self.resolve(self.row(index)[1])
        cfg = self.cfg_path(abs_path)
        values = read_settings(cfg)
        return {
            'index': index,
            'config_path': self.display_path(cfg),
            'exists': os.path.isfile(cfg),
            'settings': {k: values.get(k, '') for k in SETTINGS_KEYS},
            'variants': sequence_select.list_variants(self.sequences_dir, abs_path),
            'legacy_csv': os.path.isfile(sequence_select.legacy_csv_path(abs_path)),
        }

    def put_settings(self, index: int, changes: Dict[str, Optional[str]]) -> dict:
        abs_path = self.resolve(self.row(index)[1])
        if not os.path.isdir(os.path.dirname(abs_path)):
            raise SongError('Song folder not found for %s' % abs_path, 404)
        variants = sequence_select.list_variants(self.sequences_dir, abs_path)
        clean = {k: validate_setting(k, v, variants) for k, v in changes.items()}
        with _write_lock:
            write_settings(self.cfg_path(abs_path), clean)
        return self.get_settings(index)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _parse_rows(text: str) -> List[List[str]]:
    """Playlist rows the way the show accepts them (2-4 fields), as strings."""
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter='\t'):
        if 2 <= len(row) <= 4:
            rows.append(row)
    return rows


def _format_rows(rows: List[List[str]]) -> str:
    out = io.StringIO()
    csv.writer(out, delimiter='\t', lineterminator='\n').writerows(rows)
    return out.getvalue()


def _read_rows(path: str, missing_ok: bool) -> List[List[str]]:
    if not os.path.isfile(path):
        if missing_ok:
            return []
        raise SongError('Playlist not found: %s' % path, 404)
    with open(path) as fp:
        fcntl.lockf(fp, fcntl.LOCK_SH)
        try:
            return _parse_rows(fp.read())
        finally:
            fcntl.lockf(fp, fcntl.LOCK_UN)


def _rewrite(fp, text: str) -> None:
    fp.seek(0)
    fp.truncate()
    fp.write(text)
    fp.flush()
    os.fsync(fp.fileno())


def _atomic_copy_text(path: str, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file and rename (keeps the old mode)."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=folder, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 200 or any(c in name for c in '\t\r\n'):
        raise SongError('Song name must be 1-200 characters with no tabs or newlines', 422)
    return name


def _duration(abs_path: str) -> Optional[float]:
    """Song length in seconds from its tags, cached by mtime; None if unknown."""
    try:
        key = (abs_path, os.stat(abs_path).st_mtime_ns)
    except OSError:
        return None
    if key not in _duration_cache:
        value = None
        try:
            import mutagen
            meta = mutagen.File(abs_path)
            if meta is not None and getattr(meta, 'info', None) is not None:
                value = round(float(meta.info.length), 2)
        except Exception:
            value = None
        _duration_cache[key] = value
    return _duration_cache[key]


def read_settings(cfg_path: str) -> Dict[str, str]:
    """``[custom_lightshow]`` values from a per-song cfg ('' for valueless keys)."""
    if not os.path.isfile(cfg_path):
        return {}
    config = configparser.RawConfigParser(allow_no_value=True)
    try:
        with open(cfg_path) as f:
            config.read_file(f)
    except (configparser.Error, OSError, UnicodeDecodeError) as error:
        log.warning("Could not read %s: %s", cfg_path, error)
        return {}
    if not config.has_section(SECTION):
        return {}
    return {k: (v or '') for k, v in config.items(SECTION)}


def validate_setting(key: str, value: Optional[str], variants: List[str]) -> str:
    """Normalize one setting; '' means remove. Raises SongError (422) if invalid.

    The show crashes on some values a form produces naturally (an empty
    channel list, a non-number), so everything is checked here.
    """
    if key not in SETTINGS_KEYS:
        raise SongError('Unknown setting: %s' % key, 422)
    text = (value or '').strip()
    if not text:
        return ''
    if '\n' in text or '\r' in text:
        raise SongError('%s must be a single line' % key, 422)

    if key == 'sequence':
        try:
            spec = sequence_select.parse_spec(text)
        except ValueError as error:
            raise SongError('sequence: %s' % error, 422)
        missing = [c for c in spec.choices if c not in (sequence_select.FFT, sequence_select.LEGACY)
                   and c not in variants]
        if missing:
            raise SongError('sequence: no CSV for %s in the sequences folder' % ', '.join(missing), 422)
        choices = ','.join(spec.choices)
        return choices if spec.mode == 'single' else '%s:%s' % (spec.mode, choices)

    if key in CHANNEL_KEYS:
        compact = text.replace(' ', '')
        if not _CHANNELS_RE.match(compact):
            raise SongError('%s must be comma-separated channel numbers' % key, 422)
        return compact

    if key == 'attenuate_pct':
        try:
            number = float(text)
        except ValueError:
            raise SongError('attenuate_pct must be a number', 422)
        if not 0 <= number <= 100:
            raise SongError('attenuate_pct must be between 0 and 100', 422)
        return text

    if key.endswith('_configuration'):
        try:
            json.loads(text)
        except ValueError as error:
            raise SongError('%s must be JSON: %s' % (key, error), 422)
        return text

    return text  # *_script: a path; the show skips it if the file is missing


def write_settings(cfg_path: str, changes: Dict[str, str]) -> None:
    """Apply validated ``[custom_lightshow]`` changes ('' removes a key).

    Keeps the other sections, writes a ``.bak`` first and replaces the file
    atomically. A pre/postshow pair is written whole: the show reads
    ``postshow_configuration`` and ``postshow_script`` together and crashes
    if only one of them is present.
    """
    config = configparser.RawConfigParser(allow_no_value=True)
    if os.path.isfile(cfg_path):
        with open(cfg_path) as f:
            config.read_file(f)
    if not config.has_section(SECTION):
        config.add_section(SECTION)

    for key, value in changes.items():
        if value:
            config.set(SECTION, key, value)
        else:
            config.remove_option(SECTION, key)

    for pair in SHOW_PAIRS:
        present = [k for k in pair if (config.get(SECTION, k, fallback='') or '').strip()]
        for k in pair:
            if present:
                if not config.has_option(SECTION, k) or config.get(SECTION, k) is None:
                    config.set(SECTION, k, '')
            else:
                config.remove_option(SECTION, k)

    out = io.StringIO()
    config.write(out)
    try:
        if os.path.isfile(cfg_path):
            shutil.copy2(cfg_path, cfg_path + '.bak')
        _atomic_copy_text(cfg_path, out.getvalue())
    except PermissionError as error:
        raise SongError('No permission to write %s (%s); check the file owner '
                        '(the show runs as root)' % (cfg_path, error), 403)
