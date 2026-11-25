"""
Safe reading and writing of the show's config files for the web config editor.

Everything here takes the config directory as an argument (the router injects
it), so tests run against a temp dir and never touch the real ``config/``.

Writes are:
- validated with the same parser the show uses
  (``RawConfigParser(allow_no_value=True)``); a file that doesn't parse is
  never written, because the next song would crash on it,
- checked for lost updates: the caller sends the sha256 of the text it
  loaded, and a file that changed since is refused (``StaleFileError``),
- backed up to ``config/backups/<name>.<timestamp>`` (last ``BACKUP_KEEP``
  per file),
- atomic: a temp file in the same directory, then ``os.replace``, keeping the
  original file mode.

Only file names on the allowlist can be read or written (see
``list_files``/``check_name``), so there is no path traversal.
"""

import configparser
import glob
import hashlib
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

BACKUP_DIR_NAME = "backups"
BACKUP_KEEP = 20
OVERRIDES = "overrides.cfg"
DEFAULTS = "defaults.cfg"
LED_TEMPLATE = "led1.defaults.cfg"

# Never listed or written: runtime state and the bench config
EXCLUDED = {"state.cfg", "testbed.cfg"}

# Values of keys containing any of these words are masked in the effective view
SECRET_WORDS = ("token", "key", "password", "secret")
MASK = "********"

# Keys an [output:<name>] section may use (see the example at the end of
# defaults.cfg and py/outputs/nanoleaf.py). Unknown keys there only warn.
OUTPUT_KEYS = {
    "type", "enabled", "host", "token", "token_file", "port", "channels",
    "colors", "leds", "brightness", "reverse", "color_order", "max_fps",
    "transition", "restore",
}

# The keys synchronized_lights.py really reads from [ambient]
AMBIENT_OWN_KEYS = {"channels", "brightness"}

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.cfg$")
_BACKUP_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.cfg)\."
                        r"(?P<stamp>\d{8}-\d{6}-\d{6})$")

# Serializes check-hash-then-write inside this process
_write_lock = threading.Lock()


class ConfigFileError(Exception):
    """Base error for config file operations."""


class InvalidNameError(ConfigFileError):
    """The name is not a plain .cfg file name on the allowlist."""


class ReadOnlyError(ConfigFileError):
    """The file can be viewed but not written (defaults, templates)."""


class ParseError(ConfigFileError):
    """The new text does not parse the way the show reads it."""


class StaleFileError(ConfigFileError):
    """The file changed since the client loaded it."""


class ProtectedKeyError(ConfigFileError):
    """The edit adds, changes or removes a key whose value the show runs as root."""


# Keys whose values the show executes (as root under sudo/cron): a shell command
# or a Python script it imports. The web editor can't set, change or remove them
# in any section; edit them over SSH. Saving a file that already has them
# unchanged is fine.
PROTECTED_KEYS = frozenset({
    "songname_command",       # os.system(...) on every song
    "stream_command_string",  # subprocess.Popen in stream-in mode
    "preshow_script",         # imported and run by prepostshow.py
    "postshow_script",
})


def _protected_values(text: Optional[str]) -> Dict[tuple, Optional[str]]:
    """{(section, key): value} for PROTECTED_KEYS in ``text`` (unparsable = none)."""
    if not text:
        return {}
    try:
        parser = parse_text(text)
    except ParseError:
        return {}
    return {(section, key): (parser.get(section, key) or "").strip()
            for section in parser.sections()
            for key in parser.options(section) if key in PROTECTED_KEYS}


def check_protected(old_text: Optional[str], new_text: str) -> None:
    """
    Refuse a change to any PROTECTED_KEYS value between ``old_text`` and ``new_text``.

    Raises:
        ProtectedKeyError: naming the keys that changed
    """
    old, new = _protected_values(old_text), _protected_values(new_text)
    changed = sorted(f"[{sec}] {key}" for sec, key in set(old) | set(new)
                     if old.get((sec, key)) != new.get((sec, key)))
    if changed:
        raise ProtectedKeyError(
            "These settings run commands as root and can't be changed from the web: "
            + ", ".join(changed) + ". Edit them over SSH on the Pi.")


def sha256_text(text: Optional[str]) -> str:
    """sha256 hex digest of ``text`` as UTF-8 (``None`` means a missing file)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def new_parser() -> configparser.RawConfigParser:
    """A parser configured exactly like the show's (configuration_manager)."""
    return configparser.RawConfigParser(allow_no_value=True)


def valid_name(name: str) -> bool:
    """True if ``name`` is a plain ``*.cfg`` file name (no paths, no traversal)."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return False
    # The regex already forbids separators, NULs and a leading dot; '..' inside
    # a name is harmless on its own but never needed, so refuse it too.
    return ".." not in name


def is_read_only(name: str) -> bool:
    """Defaults and ``*.defaults.cfg`` templates are shown but never written."""
    return name == DEFAULTS or name.endswith(".defaults.cfg")


def led_config_names(config_dir: str) -> List[str]:
    """LED config file names listed in ``[configs] led_config`` (merged config)."""
    merged = load_merged(config_dir)
    raw = merged.get("configs", "led_config", fallback="") or ""
    return [n.strip() for n in raw.split(",") if n.strip()]


def list_files(config_dir: str) -> List[dict]:
    """
    The editable (and viewable) config files.

    overrides.cfg (even if it doesn't exist yet), the LED configs named in
    ``[configs] led_config`` and any other ``config/*.cfg`` except state.cfg
    and testbed.cfg. defaults.cfg and ``*.defaults.cfg`` are listed read-only.
    """
    names = {OVERRIDES}
    for path in glob.glob(os.path.join(config_dir, "*.cfg")):
        names.add(os.path.basename(path))
    led_names = set()
    try:
        led_names = {n for n in led_config_names(config_dir) if valid_name(n)}
    except Exception as e:  # a broken overrides.cfg must not hide the editor
        log.warning("Could not read [configs] led_config: %s", e)
    names |= led_names

    files = []
    for name in sorted(names):
        if name in EXCLUDED or not valid_name(name):
            continue
        path = os.path.join(config_dir, name)
        exists = os.path.isfile(path)
        files.append({
            "name": name,
            "exists": exists,
            "read_only": is_read_only(name),
            "kind": _kind(name, led_names),
            "size": os.path.getsize(path) if exists else 0,
            "modified": (datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
                         if exists else None),
        })
    order = {"overrides": 0, "led": 1, "other": 2, "defaults": 3}
    files.sort(key=lambda f: (order[f["kind"]], f["name"]))
    return files


def _kind(name: str, led_names) -> str:
    if name == OVERRIDES:
        return "overrides"
    if is_read_only(name):
        return "defaults"
    if name in led_names or re.match(r"^led\d*\.cfg$", name, re.IGNORECASE):
        return "led"
    return "other"


def check_name(config_dir: str, name: str, write: bool = False) -> str:
    """
    Return the absolute path for ``name`` if it is on the allowlist.

    Raises:
        InvalidNameError: Not a plain .cfg name, excluded, or not listed
        ReadOnlyError: ``write`` is True and the file is read-only
    """
    if not valid_name(name) or name in EXCLUDED:
        raise InvalidNameError(f"Not an editable config file: {name!r}")
    allowed = {f["name"] for f in list_files(config_dir)}
    if name not in allowed:
        raise InvalidNameError(f"Not an editable config file: {name!r}")
    if write and is_read_only(name):
        raise ReadOnlyError(f"{name} is read-only; put changes in {OVERRIDES}")
    path = os.path.join(config_dir, name)
    # Belt and braces: the resolved path must sit directly in config_dir
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(config_dir):
        raise InvalidNameError(f"Not an editable config file: {name!r}")
    return path


def read_file(config_dir: str, name: str) -> dict:
    """Raw text of an allowlisted file plus its sha256 (blank if missing)."""
    path = check_name(config_dir, name)
    text = _read_text(path)
    return {
        "name": name,
        "content": text or "",
        "exists": text is not None,
        "sha256": sha256_text(text),
        "read_only": is_read_only(name),
    }


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def parse_text(text: str, source: str = "<config>") -> configparser.RawConfigParser:
    """Parse like the show does. Raises ParseError with a readable message."""
    parser = new_parser()
    try:
        parser.read_string(text, source=source)
    except configparser.Error as e:
        raise ParseError(str(e).strip()) from e
    return parser


def _template_keys(config_dir: str, name: str) -> Dict[str, set]:
    """Known sections and keys a file of this kind should use."""
    is_led = _kind(name, set(led_config_names_safe(config_dir))) == "led"
    template = LED_TEMPLATE if is_led else DEFAULTS
    parser = new_parser()
    path = os.path.join(config_dir, template)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            parser.read_file(f)
    known = {s: set(parser.options(s)) for s in parser.sections()}
    # defaults.cfg opens [ambient] in the middle of the [lightshow] settings, so
    # keys the show reads from [lightshow] (light_delay, decay_factor,
    # always_on_channels...) are documented under [ambient]. Accept them in
    # [lightshow] too until defaults.cfg is reordered.
    if "lightshow" in known and "ambient" in known:
        known["lightshow"] |= known["ambient"] - AMBIENT_OWN_KEYS
    return known


def led_config_names_safe(config_dir: str) -> List[str]:
    """led_config_names() that returns [] when the merged config is broken."""
    try:
        return led_config_names(config_dir)
    except Exception:
        return []


def validate(config_dir: str, name: str, text: str) -> List[str]:
    """
    Parse ``text`` and return warnings for sections and keys the show doesn't know.

    ``[output:*]`` sections are checked against OUTPUT_KEYS; LED configs against
    led1.defaults.cfg; everything else against defaults.cfg.

    Raises:
        ParseError: The text does not parse
    """
    parser = parse_text(text, source=name)
    known = _template_keys(config_dir, name)
    warnings = []
    for section in parser.sections():
        if section.lower().startswith("output:"):
            keys = OUTPUT_KEYS
            if not (parser.get(section, "type", fallback="") or "").strip():
                warnings.append(f"[{section}] has no 'type' (e.g. type = nanoleaf)")
        elif section in known:
            keys = known[section]
        else:
            warnings.append(f"Unknown section [{section}]")
            continue
        for key in parser.options(section):
            if key not in keys:
                warnings.append(f"Unknown key '{key}' in [{section}]")
    return warnings


def backup_dir(config_dir: str) -> str:
    return os.path.join(config_dir, BACKUP_DIR_NAME)


def list_backups(config_dir: str, name: str) -> List[dict]:
    """Backups of ``name``, newest first."""
    check_name(config_dir, name)
    bdir = backup_dir(config_dir)
    if not os.path.isdir(bdir):
        return []
    backups = []
    for entry in os.listdir(bdir):
        m = _BACKUP_RE.match(entry)
        if not m or m.group("name") != name:
            continue
        path = os.path.join(bdir, entry)
        stamp = datetime.strptime(m.group("stamp"), "%Y%m%d-%H%M%S-%f")
        backups.append({
            "id": entry,
            "created": stamp.isoformat(),
            "size": os.path.getsize(path),
        })
    backups.sort(key=lambda b: b["id"], reverse=True)
    return backups


def read_backup(config_dir: str, name: str, backup_id: str) -> str:
    """Text of one backup. ``backup_id`` must be one list_backups() returns."""
    if backup_id not in {b["id"] for b in list_backups(config_dir, name)}:
        raise InvalidNameError(f"No such backup: {backup_id!r}")
    with open(os.path.join(backup_dir(config_dir), backup_id), encoding="utf-8") as f:
        return f.read()


def _make_backup(config_dir: str, name: str, text: str) -> str:
    bdir = backup_dir(config_dir)
    os.makedirs(bdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_id = f"{name}.{stamp}"
    # Two saves in the same microsecond: bump until unique
    while os.path.exists(os.path.join(bdir, backup_id)):
        stamp = _bump(stamp)
        backup_id = f"{name}.{stamp}"
    _atomic_write(os.path.join(bdir, backup_id), text, default_mode=0o644)
    _rotate_backups(config_dir, name)
    return backup_id


def _bump(stamp: str) -> str:
    dt = datetime.strptime(stamp, "%Y%m%d-%H%M%S-%f")
    return (dt + timedelta(microseconds=1)).strftime("%Y%m%d-%H%M%S-%f")


def _rotate_backups(config_dir: str, name: str, keep: int = BACKUP_KEEP) -> None:
    for old in list_backups(config_dir, name)[keep:]:
        try:
            os.remove(os.path.join(backup_dir(config_dir), old["id"]))
        except OSError as e:
            log.warning("Could not remove old backup %s: %s", old["id"], e)


def _atomic_write(path: str, text: str, default_mode: int = 0o644) -> None:
    """Write ``text`` to ``path`` atomically, keeping its mode (and owner if we can)."""
    directory = os.path.dirname(path)
    try:
        st = os.stat(path)
        mode, owner = st.st_mode & 0o7777, (st.st_uid, st.st_gid)
    except FileNotFoundError:
        mode, owner = default_mode, None
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        if owner is not None:
            try:
                os.chown(tmp, *owner)
            except OSError:
                pass  # not root: the file keeps our ownership
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _record_config_change(name: str, user: Optional[str], backup_id: Optional[str],
                          action: str = "save") -> None:
    """
    Hook for the event log: record that a config file changed.

    Recorded as a ``config_change`` event (never raises; a no-op when
    ``[events] enabled = false``).
    """
    log.info("Config %s %s by %s (backup %s)", name, action, user or "unknown", backup_id)
    try:
        import event_log
        event_log.record("config_change", source="api", user=user, file=name,
                         action=action, backup=backup_id)
    except Exception:  # the event log must never break a config save
        log.debug("config_change event not recorded", exc_info=True)


def write_file(config_dir: str, name: str, text: str, base_sha256: Optional[str],
               user: Optional[str] = None, action: str = "save") -> dict:
    """
    Validate, back up and atomically write ``text`` to an allowlisted file.

    Args:
        base_sha256: sha256 of the text the client loaded (None skips the check;
            only internal callers that just read the file pass None)

    Returns:
        dict: name, sha256 of the new text, backup id (None for a new file), warnings

    Raises:
        InvalidNameError, ReadOnlyError, ParseError, StaleFileError, ProtectedKeyError
    """
    path = check_name(config_dir, name, write=True)
    if not text.endswith("\n"):
        text += "\n"
    warnings = validate(config_dir, name, text)
    with _write_lock:
        current = _read_text(path)
        if base_sha256 is not None and base_sha256 != sha256_text(current):
            raise StaleFileError(
                f"{name} changed since you loaded it. Reload, then re-apply your edits.")
        check_protected(current, text)
        backup_id = _make_backup(config_dir, name, current) if current is not None else None
        _atomic_write(path, text)
    _record_config_change(name, user, backup_id, action)
    return {"name": name, "sha256": sha256_text(text), "backup": backup_id,
            "warnings": warnings}


def restore_backup(config_dir: str, name: str, backup_id: str,
                   user: Optional[str] = None) -> dict:
    """Write a backup back over ``name`` (the current text is backed up first)."""
    text = read_backup(config_dir, name, backup_id)
    return write_file(config_dir, name, text, None, user=user, action=f"restore {backup_id}")


def load_merged(config_dir: str, overrides: str = OVERRIDES) -> configparser.RawConfigParser:
    """defaults.cfg with overrides.cfg on top, read the way the show reads them."""
    parser = new_parser()
    with open(os.path.join(config_dir, DEFAULTS), encoding="utf-8") as f:
        parser.read_file(f)
    parser.read(os.path.join(config_dir, overrides), encoding="utf-8")
    return parser


def is_secret(key: str) -> bool:
    k = key.lower()
    return any(word in k for word in SECRET_WORDS)


def mask(key: str, value: Optional[str]) -> Optional[str]:
    """Mask a non-blank secret value."""
    if value and is_secret(key):
        return MASK
    return value


def effective_config(config_dir: str) -> dict:
    """
    The merged config the next song will read, with secrets masked.

    Each value says where it came from (``defaults`` or ``overrides``). LED
    configs are shown on top of led1.defaults.cfg.
    """
    defaults = new_parser()
    with open(os.path.join(config_dir, DEFAULTS), encoding="utf-8") as f:
        defaults.read_file(f)
    merged = load_merged(config_dir)
    sections = {}
    for section in merged.sections():
        sections[section] = {
            key: {
                "value": mask(key, value),
                "source": ("defaults" if defaults.has_option(section, key)
                           and defaults.get(section, key) == value else "overrides"),
            }
            for key, value in merged.items(section)
        }

    led = {}
    for led_name in led_config_names(config_dir):
        if not valid_name(led_name):
            continue
        parser = new_parser()
        template = os.path.join(config_dir, LED_TEMPLATE)
        if os.path.isfile(template):
            parser.read(template, encoding="utf-8")
        found = parser.read(os.path.join(config_dir, led_name), encoding="utf-8")
        led[led_name] = {
            "exists": bool(found),
            "sections": {s: {k: mask(k, v) for k, v in parser.items(s)}
                         for s in parser.sections()},
        }
    return {"sections": sections, "led_configs": led}


def _section_id(name: str) -> str:
    """
    Compare section names the way the show matches outputs: ``[Output: nl]``
    and ``[output:nl]`` are the same output (prefix case-insensitive, spaces
    around the colon ignored).
    """
    name = name.strip()
    if ":" in name:
        prefix, rest = name.split(":", 1)
        return f"{prefix.strip().lower()}:{rest.strip()}"
    return name


def set_option_text(text: str, section: str, key: str, value: str) -> str:
    """
    Set ``key = value`` in ``[section]`` by editing the text, keeping comments.

    Replaces the first ``key =`` line in the section, else adds the line after
    the section's last setting, else appends a new section.
    ConfigParser.write() would drop every comment, so this edits lines.
    """
    lines = text.splitlines()
    header = re.compile(r"^\s*\[(?P<s>[^\]]+)\]\s*$")
    wanted = _section_id(section)
    key_re = re.compile(r"^\s*" + re.escape(key) + r"\s*[=:]", re.IGNORECASE)
    start = end = None
    for i, line in enumerate(lines):
        m = header.match(line)
        if m:
            if start is not None:
                end = i
                break
            if _section_id(m.group("s")) == wanted:
                start = i
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += [f"[{section}]", f"{key} = {value}"]
        return "\n".join(lines) + "\n"
    if end is None:
        end = len(lines)
    last_setting = start
    for i in range(start + 1, end):
        if key_re.match(lines[i]):
            lines[i] = f"{key} = {value}"
            return "\n".join(lines) + "\n"
        stripped = lines[i].strip()
        if stripped and not stripped.startswith(("#", ";")):
            last_setting = i
    lines.insert(last_setting + 1, f"{key} = {value}")
    return "\n".join(lines) + "\n"


def set_option(config_dir: str, name: str, section: str, key: str, value: str,
               user: Optional[str] = None) -> dict:
    """Set one option in an allowlisted file through the safe write path."""
    path = check_name(config_dir, name, write=True)
    with _write_lock:
        current = _read_text(path)
    new_text = set_option_text(current or "", section, key, value)
    return write_file(config_dir, name, new_text, sha256_text(current), user=user,
                      action=f"set [{section}] {key}")


def gpio_channel_count(merged: configparser.RawConfigParser) -> int:
    """Number of relay channels from ``[hardware] gpio_pins`` (not LED channels)."""
    pins = merged.get("hardware", "gpio_pins", fallback="") or ""
    return len([p for p in pins.split(",") if p.strip()])


def split_csv(value: Optional[str]) -> List[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


__all__ = [
    "ConfigFileError", "InvalidNameError", "ReadOnlyError", "ParseError", "StaleFileError",
    "list_files", "read_file", "write_file", "list_backups", "read_backup",
    "restore_backup", "effective_config", "load_merged", "set_option",
    "set_option_text", "valid_name", "sha256_text", "gpio_channel_count",
]
