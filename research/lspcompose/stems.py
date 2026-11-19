"""
Stems: one instrument ("voice") per light.

Demucs (htdemucs_6s) separates the song into drums, bass, vocals, guitar,
piano and other. Each stem is analysed into small features (a log-mel
spectrogram), and from those we build voices:

    kick        drums stem, lowest bands (< ~150 Hz)
    snare_hats  drums stem, bands above ~1.5 kHz
    bass        bass stem
    vocals_low  vocals stem below ~350 Hz (a bass singer, Pentatonix-style)
    vocals_high vocals stem above ~350 Hz
    guitar, piano, other

A song gets the voices that are actually present (loud enough relative to
the mix), in the order above, one per channel. Drum voices blink on hits;
tonal voices switch on at each new note/syllable and hold while it sounds.

Only the separation needs torch/demucs (Mac side, cached per song); the
renderer is numpy and runs on cached features.
"""

import hashlib
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from . import safety
from .strategies import (Layout, _moving_median, cut, dark_frames, fast_energy_db,
                         relay_safe)
from .timing import row_times, seconds_to_rows

STEMS_VERSION = 1
MODEL = "htdemucs_6s"
HOP = 512
N_MELS = 64

# voice: (stem, low Hz, high Hz, kind)
VOICES = {
    "kick": ("drums", 0, 150, "hit"),
    "snare_hats": ("drums", 1500, 20000, "hit"),
    "bass": ("bass", 0, 20000, "tone"),
    "vocals_low": ("vocals", 0, 350, "tone"),
    "vocals_high": ("vocals", 350, 20000, "tone"),
    "guitar": ("guitar", 0, 20000, "tone"),
    "piano": ("piano", 0, 20000, "tone"),
    "other": ("other", 0, 20000, "tone"),
}
VOICE_LABELS = {"kick": "kick drum", "snare_hats": "snare/hats", "bass": "bass",
                "vocals_low": "low voice", "vocals_high": "high voice", "guitar": "guitar",
                "piano": "piano", "other": "other"}


@dataclass
class StemFeatures:
    mel_times: np.ndarray                 # seconds per frame (same grid for all stems)
    mel_freqs: np.ndarray                 # Hz, centre of each mel band
    mel_db: Dict[str, np.ndarray]         # stem -> [N_MELS, frames] dB (shared reference)


def available() -> bool:
    """True when torch and demucs are installed (Mac research venv)."""
    try:
        import demucs  # noqa: F401
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def separate_features(pcm: np.ndarray, sample_rate: int) -> StemFeatures:
    """Run Demucs on int16 [frames, channels] audio and keep per-stem mel features."""
    import librosa
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    model = get_model(MODEL)
    model.eval()
    audio = pcm.astype(np.float32).T / 32768.0
    if audio.shape[0] == 1:
        audio = np.repeat(audio, 2, axis=0)
    audio = audio[:2]
    if sample_rate != model.samplerate:
        audio = librosa.resample(audio, orig_sr=sample_rate, target_sr=model.samplerate)
    with torch.no_grad():
        out = apply_model(model, torch.from_numpy(np.ascontiguousarray(audio))[None],
                          device="cpu", split=True, overlap=0.25, progress=False)[0].numpy()
    sr = model.samplerate
    mels, ref = {}, 0.0
    for name, stem in zip(model.sources, out):
        mono = stem.mean(axis=0)
        mel = librosa.feature.melspectrogram(y=mono, sr=sr, hop_length=HOP, n_mels=N_MELS,
                                             fmax=min(15000, sr / 2))
        mels[name] = mel
        ref = max(ref, float(mel.max()))
    mel_db = {k: librosa.power_to_db(v, ref=ref or 1.0).astype(np.float32) for k, v in mels.items()}
    frames = next(iter(mel_db.values())).shape[1]
    times = librosa.frames_to_time(np.arange(frames), sr=sr, hop_length=HOP)
    freqs = librosa.mel_frequencies(n_mels=N_MELS, fmax=min(15000, sr / 2))
    return StemFeatures(times, freqs, mel_db)


def features_cached(song_path: str, pcm: np.ndarray, sample_rate: int,
                    cache_dir: str) -> StemFeatures:
    """separate_features() with an .npz cache keyed by file hash and version."""
    h = hashlib.sha1()
    with open(song_path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"stems-{h.hexdigest()}-{MODEL}-v{STEMS_VERSION}.npz")
    if os.path.exists(path):
        d = np.load(path)
        stems = [k[4:] for k in d.files if k.startswith("mel_")]
        return StemFeatures(d["times"], d["freqs"], {s: d[f"mel_{s}"] for s in stems})
    feats = separate_features(pcm, sample_rate)
    np.savez_compressed(path, times=feats.mel_times, freqs=feats.mel_freqs,
                        **{f"mel_{k}": v.astype(np.float16) for k, v in feats.mel_db.items()})
    return feats


def voice_spec(voice: str):
    """(stem, low Hz, high Hz, kind, label) for a voice name, including split voices
    named '<voice>@<lo>-<hi>' (e.g. 'guitar@0-420')."""
    if "@" in voice:
        base, rng = voice.split("@", 1)
        lo, hi = (float(x) for x in rng.split("-"))
        stem, _, _, kind = VOICES[base]
        part = "low" if lo == 0 else "high"
        return stem, lo, hi, kind, f"{VOICE_LABELS[base]} {part}"
    stem, lo, hi, kind = VOICES[voice]
    return stem, lo, hi, kind, VOICE_LABELS[voice]


def voice_mel(feats: StemFeatures, voice: str) -> np.ndarray:
    """The mel rows of a voice's stem inside its frequency range."""
    stem, lo, hi, _, _ = voice_spec(voice)
    rows = (feats.mel_freqs >= lo) & (feats.mel_freqs < hi)
    if not rows.any():
        rows[np.argmin(np.abs(feats.mel_freqs - lo))] = True
    return np.asarray(feats.mel_db[stem], dtype=float)[rows]


def present_voices(feats: StemFeatures, within_db: float = 24.0) -> List[str]:
    """Voices whose loud level (95th percentile) is within ``within_db`` of the loudest voice."""
    levels = {}
    for voice, (stem, *_rest) in VOICES.items():
        if stem in feats.mel_db:
            env = fast_energy_db(voice_mel(feats, voice), feats.mel_times)
            levels[voice] = float(np.percentile(env, 95))
    if not levels:
        return []
    top = max(levels.values())
    return [v for v in VOICES if v in levels and levels[v] >= top - within_db]


def split_point(feats: StemFeatures, voice: str) -> float:
    """Frequency (Hz) halving a voice's energy: where to split it into low/high."""
    mel = voice_mel(feats, voice)
    stem, lo, hi, _, _ = voice_spec(voice)
    freqs = feats.mel_freqs[(feats.mel_freqs >= lo) & (feats.mel_freqs < hi)]
    energy = (10 ** (mel / 10)).sum(axis=1)
    if freqs.size < 4 or energy.sum() <= 0:
        return 0.0
    i = int(np.searchsorted(np.cumsum(energy) / energy.sum(), 0.5))
    return float(freqs[int(np.clip(i, 1, freqs.size - 2))])


def fill_voices(feats: StemFeatures, voices: List[str], channels: int) -> List[str]:
    """
    With more channels than voices, split the loudest pitched voices into a
    low and a high part (at their energy midpoint) until every channel has one.
    """
    voices = list(voices)
    tried = set()
    while len(voices) < channels:
        tones = [v for v in voices if voice_spec(v)[3] == "tone" and "@" not in v and v not in tried]
        if not tones:
            break
        loudest = max(tones, key=lambda v: np.percentile(
            fast_energy_db(voice_mel(feats, v), feats.mel_times), 95))
        tried.add(loudest)
        f = split_point(feats, loudest)
        if f <= 0:
            continue
        i = voices.index(loudest)
        voices[i:i + 1] = [f"{loudest}@0-{f:.0f}", f"{loudest}@{f:.0f}-20000"]
    return voices


def assign_voices(feats: StemFeatures, layout: Layout,
                  order: Optional[List[str]] = None) -> Dict[int, str]:
    """channel -> voice: present voices in priority order, the loudest split to fill spare channels."""
    voices = order or fill_voices(feats, present_voices(feats), len(layout.active))
    return {c: v for c, v in zip(layout.active, voices)}


def _peaks(flux: np.ndarray, hop: float, threshold: float = 3.0, floor_frac: float = 0.1,
           min_gap_s: float = 0.25) -> np.ndarray:
    """Frame indices of onset peaks in one flux curve (adaptive median/MAD threshold)."""
    width = max(3, int(round(0.5 / hop)))
    med = _moving_median(flux, width)
    mad = _moving_median(np.abs(flux - med), width) + 1e-6
    score = (flux - med) / mad
    floor = floor_frac * float(np.percentile(flux, 99)) if flux.size else 0.0
    cand = np.flatnonzero((score > threshold) & (flux >= floor)
                          & (score >= np.roll(score, 1)) & (score >= np.roll(score, -1)))
    out, last, gap = [], -10 ** 9, max(1, int(round(min_gap_s / hop)))
    for t in cand:
        if t - last >= gap:
            out.append(t)
            last = t
    return np.array(out, dtype=int)


def render_voice(mel: np.ndarray, kind: str, mel_times: np.ndarray, times: np.ndarray,
                 beat_period: float, min_on: float, min_off: float) -> np.ndarray:
    """
    One channel (bool per CSV row) for one voice.

    hit:  on for ~half a beat at each hit.
    tone: on at each note/syllable onset, held while the voice keeps sounding
          (envelope within 12 dB of its loud level) until min_off before the next
          onset, so consecutive notes blink.
    """
    hop = float(np.median(np.diff(mel_times)))
    flux = np.maximum(0.0, np.diff(mel, axis=1, prepend=mel[:, :1])).sum(axis=0)
    env = fast_energy_db(mel, mel_times)
    loud = float(np.percentile(env, 95))
    active = env > loud - 12.0
    peaks = _peaks(flux, hop, min_gap_s=max(min_on + min_off, 0.25))
    out = np.zeros(times.size, dtype=bool)
    hit_hold = max(min_on, 0.5 * beat_period)
    for i, p in enumerate(peaks):
        if not active[p]:
            continue
        start = mel_times[p]
        if kind == "hit":
            end = start + hit_hold
        else:
            nxt = mel_times[peaks[i + 1]] - min_off if i + 1 < len(peaks) else mel_times[-1]
            stop = p
            while stop < active.size and active[stop] and mel_times[stop] < nxt:
                stop += 1
            end = max(start + min_on, mel_times[min(stop, active.size - 1)])
        out[(times >= start - 1e-9) & (times < end)] = True
    return out


def stems_show(feats: StemFeatures, mix_mel_db: np.ndarray, mix_mel_times: np.ndarray,
               beat_times, rows: int, sample_rate: int, layout: Layout, timing_ms: int = 25,
               min_on: float = 0.2, min_off: float = 0.15, drop_db: Optional[float] = 10.0,
               assignment: Optional[Dict[int, str]] = None):
    """
    Levels [rows, channels] with one voice per channel, plus the channel->voice map.
    Unassigned channels stay dark. The mix's drop cut applies to every channel.
    """
    times = row_times(rows, sample_rate, timing_ms)
    out = np.zeros((rows, layout.channels))
    assignment = assignment if assignment is not None else assign_voices(feats, layout)
    beats = np.asarray(beat_times, dtype=float)
    beat_period = float(np.median(np.diff(beats))) if beats.size > 1 else 0.5
    for channel, voice in assignment.items():
        out[:, channel] = render_voice(voice_mel(feats, voice), voice_spec(voice)[3],
                                       feats.mel_times, times, beat_period, min_on, min_off)
    out = relay_safe(out, sample_rate, timing_ms, min_on, min_off)
    if drop_db is not None and len(mix_mel_times) > 1:
        dark = dark_frames(mix_mel_db, mix_mel_times, drop_db, momentary_s=max(0.3, beat_period))
        dark_rows = np.interp(times, mix_mel_times, dark.astype(float)) > 0.5
        out = cut(out, dark_rows, seconds_to_rows(min_on, sample_rate, timing_ms))
    return out, assignment


__all__ = ["VOICES", "VOICE_LABELS", "StemFeatures", "available", "features_cached",
           "present_voices", "assign_voices", "render_voice", "stems_show", "safety"]
