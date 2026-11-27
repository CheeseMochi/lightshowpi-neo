"""
Lab state on the Mac: bundles, ratings, local patterns/mixes, and pushing to the Pi.

Everything here is plain files under research/ (git-ignored where personal):
    research/out/<song>/manifest.json   bundles built by research/bundle.py
    research/ratings.jsonl              one JSON line per rating (latest wins)
    research/patterns.local.toml        your patterns and mixes
    research/layout.json                the Pi's /api/layout, saved by `lab pi layout`
"""

import json
import os
import time
from typing import Dict, List, Optional

from . import RESEARCH_DIR
from .pi import PiClient, PiError, pi_variant_name

OUT_DIR = os.path.join(RESEARCH_DIR, "out")
RATINGS_FILE = os.path.join(RESEARCH_DIR, "ratings.jsonl")
LAYOUT_FILE = os.path.join(RESEARCH_DIR, "layout.json")
SETTINGS_FILE = os.path.join(RESEARCH_DIR, "lab.local.json")
AUDIO_EXTS = (".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".mp4")
MAX_SONGS = 3000          # a whole ~/Music library would make the song list unusable
MAX_RECENT = 8


# ---------------------------------------------------------------------------
# Bundles and songs
# ---------------------------------------------------------------------------

def bundles(out_dir: str = OUT_DIR) -> Dict[str, dict]:
    """song stem -> manifest, for every built bundle."""
    found = {}
    if not os.path.isdir(out_dir):
        return found
    for name in sorted(os.listdir(out_dir)):
        path = os.path.join(out_dir, name, "manifest.json")
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    manifest = json.load(f)
            except ValueError:
                continue
            manifest["built_at"] = os.path.getmtime(path)
            found[name] = manifest
    return found


def audio_files(music_dir: str, limit: int = MAX_SONGS) -> List[str]:
    """
    Audio files under ``music_dir`` (relative paths, sorted), skipping hidden
    folders, sequences/ and app bundles (e.g. a Music.app library package).
    Stops after ``limit`` files; see ``scan_music``.
    """
    return scan_music(music_dir, limit)[0]


def scan_music(music_dir: str, limit: int = MAX_SONGS):
    """(relative audio paths sorted, True if the scan stopped at ``limit``)."""
    found, truncated = [], False
    for root, dirs, files in os.walk(music_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "sequences"
                         and not d.endswith((".app", ".musiclibrary", ".photoslibrary")))
        for f in files:
            if f.lower().endswith(AUDIO_EXTS) and not f.startswith("."):
                if len(found) >= limit:
                    truncated = True
                    break
                found.append(os.path.relpath(os.path.join(root, f), music_dir))
        if truncated:
            break
    return sorted(found, key=str.lower), truncated


# ---------------------------------------------------------------------------
# Lab settings (git-ignored research/lab.local.json): the music folder
# ---------------------------------------------------------------------------

def load_settings(path: str = SETTINGS_FILE) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict, path: str = SETTINGS_FILE) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
    os.replace(tmp, path)


def resolve_music_dir(path: str) -> str:
    """Absolute, user-expanded folder path; ValueError if it isn't a folder."""
    resolved = os.path.abspath(os.path.expanduser(os.path.expandvars(path.strip())))
    if not os.path.isdir(resolved):
        raise ValueError(f"not a folder: {resolved}")
    return resolved


def remember_music_dir(path: str, settings_path: str = SETTINGS_FILE) -> dict:
    """Make ``path`` the current music folder and move it to the front of the recent list."""
    settings = load_settings(settings_path)
    recent = [p for p in settings.get("recent_music_dirs", []) if p != path]
    settings["music_dir"] = path
    settings["recent_music_dirs"] = ([path] + recent)[:MAX_RECENT]
    save_settings(settings, settings_path)
    return settings


def initial_music_dir(explicit: Optional[str], default: str,
                      settings_path: str = SETTINGS_FILE) -> str:
    """--music, else $LSP_MUSIC_DIR, else the last folder used in the lab, else ``default``."""
    for candidate in (explicit, os.environ.get("LSP_MUSIC_DIR"),
                      load_settings(settings_path).get("music_dir")):
        if candidate:
            try:
                return resolve_music_dir(candidate)
            except ValueError:
                continue
    return os.path.abspath(default)


def song_stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------

def rate(song: str, variant: str, stars: int, note: str = "",
         path: str = RATINGS_FILE) -> dict:
    """Record a 1-5 rating (0 clears) for a variant of a song."""
    if not 0 <= int(stars) <= 5:
        raise ValueError("stars must be 0..5 (0 clears)")
    entry = {"song": song, "variant": variant, "stars": int(stars), "note": note,
             "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def ratings(song: Optional[str] = None, path: str = RATINGS_FILE) -> Dict[str, Dict[str, dict]]:
    """song -> variant -> latest rating (cleared ratings are dropped)."""
    latest: Dict[str, Dict[str, dict]] = {}
    if not os.path.exists(path):
        return latest
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if song and r.get("song") != song:
                continue
            latest.setdefault(r["song"], {})[r["variant"]] = r
    return {s: {v: r for v, r in vs.items() if r.get("stars")} for s, vs in latest.items()}


# ---------------------------------------------------------------------------
# Local patterns / mixes (TOML writer for the small subset the lab uses)
# ---------------------------------------------------------------------------

def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)          # TOML basic strings use the same escapes
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[\n  " + ",\n  ".join(_toml_value(v) for v in value) + ",\n]"
    raise TypeError(f"can't write {value!r} to TOML")


def dump_local(patterns: Dict[str, dict], mixes: Dict[str, dict]) -> str:
    lines = ["# Your patterns and mixes for the composer (written by research/lab;",
             "# you can edit it by hand too, but comments are not kept). Layout and",
             "# options: see research/patterns.toml.", ""]
    for table, entries in (("patterns", patterns), ("mixes", mixes)):
        for name, spec in entries.items():
            lines.append(f"[{table}.{name}]")
            for key, value in spec.items():
                lines.append(f"{key} = {_toml_value(value)}")
            lines.append("")
    return "\n".join(lines)


def save_local(library_or_none, patterns: Dict[str, dict], mixes: Dict[str, dict],
               path: str) -> None:
    """Validate (through variants.load_library) and write the local patterns file atomically."""
    from lspcompose import variants
    text = dump_local(patterns, mixes)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    try:
        variants.load_library(variants.BUILTIN_FILE, tmp)   # raises PatternError if invalid
    except Exception:
        os.remove(tmp)
        raise
    os.replace(tmp, path)


def local_entries(path: str):
    """(patterns, mixes) defined in the local file only."""
    from lspcompose import variants
    data = variants._read_toml(path)
    return dict(data.get("patterns", {})), dict(data.get("mixes", {}))


# ---------------------------------------------------------------------------
# Push to the Pi
# ---------------------------------------------------------------------------

def resolve_pi_song(client: PiClient, song: str, as_song: Optional[str] = None,
                    songs: Optional[List[dict]] = None) -> dict:
    """The Pi playlist entry for a local song (or the name given with as_song)."""
    matches = client.find_song(as_song or song, songs)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise PiError(404, f"no song on the Pi matches {as_song or song!r}; "
                           "use --as with the Pi's song name (see `lab pi songs`)")
    names = ", ".join(f"{m['index']}: {m['name']}" for m in matches)
    raise PiError(409, f"{as_song or song!r} matches several Pi songs ({names}); pick one with --as")


def push(client: PiClient, song: str, variants: Optional[List[str]] = None,
         as_song: Optional[str] = None, replace: bool = False, out_dir: str = OUT_DIR) -> dict:
    """Upload a bundle's full-song CSVs to the matching song on the Pi."""
    manifest_path = os.path.join(out_dir, song, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(f"no bundle for {song!r} in {out_dir} (build it first)")
    with open(manifest_path) as f:
        manifest = json.load(f)
    names = variants or [v["name"] for v in manifest["variants"]]
    target = resolve_pi_song(client, song, as_song)
    results = []
    for name in names:
        csv_path = os.path.join(out_dir, song, "full", name, "full.csv")
        if not os.path.isfile(csv_path):
            results.append({"variant": name, "ok": False, "error": "not in this bundle"})
            continue
        with open(csv_path, "rb") as f:
            data = f.read()
        pi_name = pi_variant_name(name)
        try:
            info = client.upload(target["index"], pi_name, data, replace=replace)
            results.append({"variant": name, "pi_name": pi_name, "ok": True, "info": info})
        except PiError as e:
            results.append({"variant": name, "pi_name": pi_name, "ok": False,
                            "status": e.status, "error": e.detail})
    return {"song": target, "results": results}
