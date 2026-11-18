"""
Load audio the way the Pi does, and analyse it (beats, loudness).

The Pi decodes with audioread at the file's native sample rate; CSV row timing
depends on that rate, so we decode at the native rate too: audioread first,
then soundfile (libsndfile) when audioread has no working backend (e.g. a Mac
without ffmpeg). Both handle MP3 encoder delay, but different decoders can
differ by a few ms at the start; well under one 25 ms row.

librosa is imported only inside analyze(): the CSV, timing and safety code
must work on a Pi or in CI without it.
"""

import hashlib
import os
from dataclasses import dataclass

import numpy as np

ANALYSIS_VERSION = 1


@dataclass
class Audio:
    samples: np.ndarray    # mono float32, -1..1
    sample_rate: int       # native rate
    frames: int            # frames per channel
    decoder: str

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate


def load(path: str) -> Audio:
    """Decode to mono float32 at the native sample rate."""
    try:
        import audioread
        with audioread.audio_open(path) as f:
            sr, ch = f.samplerate, f.channels
            raw = b"".join(f.read_data())
        pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        pcm = pcm[: len(pcm) - len(pcm) % ch].reshape(-1, ch)
        return Audio(pcm.mean(axis=1), int(sr), pcm.shape[0], "audioread")
    except Exception as audioread_error:
        try:
            import soundfile
        except ImportError:
            raise RuntimeError(f"Can't decode {path}: audioread failed ({audioread_error}) "
                               "and soundfile isn't installed") from audioread_error
        data, sr = soundfile.read(path, dtype="float32", always_2d=True)
        return Audio(data.mean(axis=1), int(sr), data.shape[0], "soundfile")


@dataclass
class Features:
    tempo: float               # BPM from the beat tracker
    beat_times: np.ndarray     # seconds
    energy_times: np.ndarray   # seconds
    energy_db: np.ndarray      # RMS in dB, smoothed
    onset_times: np.ndarray    # seconds (for metrics / later strategies)


def _file_key(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return f"{h.hexdigest()}-v{ANALYSIS_VERSION}"


def analyze(audio: Audio, tempo_scale: float = 1.0, smooth_s: float = 1.0) -> Features:
    """Beat grid, smoothed loudness and onsets with librosa (Mac side)."""
    import librosa

    y, sr = audio.samples, audio.sample_rate
    hop = 512
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=hop,
                                           units="time")
    tempo = float(np.atleast_1d(tempo)[0])
    beats = np.asarray(beats, dtype=float)
    if tempo_scale == 2.0 and beats.size > 1:
        beats = np.sort(np.concatenate([beats, (beats[:-1] + beats[1:]) / 2]))
        tempo *= 2
    elif tempo_scale == 0.5:
        beats = beats[::2]
        tempo /= 2
    elif tempo_scale != 1.0:
        raise ValueError("tempo_scale must be 0.5, 1 or 2")

    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    width = max(1, int(round(smooth_s * sr / hop)))
    rms = np.convolve(rms, np.ones(width) / width, mode="same")
    energy_db = 20 * np.log10(np.maximum(rms, 1e-6))
    energy_times = librosa.frames_to_time(np.arange(rms.size), sr=sr, hop_length=hop)
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=hop,
                                        units="time")
    return Features(tempo, beats, energy_times, energy_db, np.asarray(onsets, dtype=float))


def analyze_cached(path: str, audio: Audio, cache_dir: str, tempo_scale: float = 1.0) -> Features:
    """analyze() with a .npz cache keyed by file hash, analysis version and tempo scale."""
    os.makedirs(cache_dir, exist_ok=True)
    key = f"{_file_key(path)}-t{tempo_scale:g}"
    cache = os.path.join(cache_dir, f"features-{key}.npz")
    if os.path.exists(cache):
        d = np.load(cache)
        return Features(float(d["tempo"]), d["beat_times"], d["energy_times"], d["energy_db"],
                        d["onset_times"])
    feats = analyze(audio, tempo_scale=tempo_scale)
    np.savez(cache, tempo=feats.tempo, beat_times=feats.beat_times,
             energy_times=feats.energy_times, energy_db=feats.energy_db,
             onset_times=feats.onset_times)
    return feats
