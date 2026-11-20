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

ANALYSIS_VERSION = 3


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
    mel_times: np.ndarray = None   # seconds, one per spectrogram frame
    mel_db: np.ndarray = None      # [n_mels, frames] log-mel spectrogram, low to high
    seg_times: np.ndarray = None   # section start times (first is 0)
    seg_labels: np.ndarray = None  # repeat label per section (same label = similar music)
    note_times: np.ndarray = None  # onsets of the pitched (non-drum) part, seconds
    note_pitch: np.ndarray = None  # strongest pitch at each of those onsets, MIDI (nan if unclear)


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

    mel = librosa.feature.melspectrogram(y=y, sr=sr, hop_length=hop, n_mels=64, fmax=min(15000, sr / 2))
    mel_db = librosa.power_to_db(mel, ref=np.max).astype(np.float32)
    mel_times = librosa.frames_to_time(np.arange(mel_db.shape[1]), sr=sr, hop_length=hop)

    seg_times, seg_labels = _sections(y, sr, hop, beats, audio.duration)
    note_times, note_pitch = _notes(y, sr, hop)
    return Features(tempo, beats, energy_times, energy_db, np.asarray(onsets, dtype=float),
                    mel_times, mel_db, seg_times, seg_labels, note_times, note_pitch)


def _notes(y, sr, hop):
    """
    Note onsets of the pitched part and the strongest pitch at each.

    HPSS removes most drums, then a CQT (C2..C7, semitone bins) gives the
    loudest pitch per frame; each onset takes the median pitch over the next
    ~60 ms. Unclear frames (peak not clearly above the frame's median) are nan.
    Good enough to see a scale run's direction; not a melody transcriber.
    """
    import librosa

    harmonic = librosa.effects.harmonic(y, margin=3.0)
    fmin = librosa.note_to_hz("C2")
    cqt = np.abs(librosa.cqt(harmonic, sr=sr, hop_length=hop, fmin=fmin, n_bins=60,
                             bins_per_octave=12))
    cqt_db = librosa.amplitude_to_db(cqt, ref=np.max)
    best = cqt_db.argmax(axis=0)
    clear = cqt_db.max(axis=0) - np.median(cqt_db, axis=0) > 20.0
    pitch = np.where(clear, librosa.hz_to_midi(fmin) + best, np.nan)
    env = librosa.onset.onset_strength(y=harmonic, sr=sr, hop_length=hop)
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=hop, units="frames")
    span = max(1, int(round(0.06 * sr / hop)))
    notes = []
    for f in frames:
        window = pitch[f:f + span]
        notes.append(float(np.nanmedian(window)) if np.isfinite(window).any() else np.nan)
    times = librosa.frames_to_time(frames, sr=sr, hop_length=hop)
    return np.asarray(times, dtype=float), np.asarray(notes, dtype=float)


def _sections(y, sr, hop, beats, duration):
    """
    Section boundaries and repeat labels.

    Agglomerative segmentation of beat-synchronous chroma + MFCC (librosa),
    about one section per 15-20 s, then sections are grouped by k-means on
    their mean features so a repeated chorus gets the same label.
    """
    import librosa
    from scipy.cluster.vq import kmeans2

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=hop, n_mfcc=13)
    beat_frames = librosa.time_to_frames(beats, sr=sr, hop_length=hop)
    if beat_frames.size < 16:
        return np.array([0.0]), np.array([0])
    feats = np.vstack([librosa.util.normalize(chroma, axis=0),
                       (mfcc - mfcc.mean(axis=1, keepdims=True)) / (mfcc.std(axis=1, keepdims=True) + 1e-6)])
    sync = librosa.util.sync(feats, beat_frames, aggregate=np.median)
    k = int(np.clip(round(duration / 18), 3, 12))
    k = min(k, sync.shape[1] - 1)
    bounds = librosa.segment.agglomerative(sync, k)          # beat-sync column indices
    starts = np.concatenate([[0.0], beats[np.clip(bounds[1:] - 1, 0, beats.size - 1)]])
    starts = np.unique(np.round(starts, 3))
    seg_feats = []
    edges = list(bounds) + [sync.shape[1]]
    for a, b in zip(edges[:-1], edges[1:]):
        seg_feats.append(sync[:, a:max(b, a + 1)].mean(axis=1))
    seg_feats = np.array(seg_feats)[: starts.size]
    n_labels = int(min(max(2, round(starts.size / 2)), starts.size))
    rng = np.random.default_rng(0)
    _, labels = kmeans2(seg_feats, n_labels, minit="++", seed=rng)
    return starts, labels.astype(int)


def analyze_cached(path: str, audio: Audio, cache_dir: str, tempo_scale: float = 1.0) -> Features:
    """analyze() with a .npz cache keyed by file hash, analysis version and tempo scale."""
    os.makedirs(cache_dir, exist_ok=True)
    key = f"{_file_key(path)}-t{tempo_scale:g}"
    cache = os.path.join(cache_dir, f"features-{key}.npz")
    if os.path.exists(cache):
        d = np.load(cache)
        return Features(float(d["tempo"]), d["beat_times"], d["energy_times"], d["energy_db"],
                        d["onset_times"], d["mel_times"], d["mel_db"], d["seg_times"],
                        d["seg_labels"], d["note_times"], d["note_pitch"])
    feats = analyze(audio, tempo_scale=tempo_scale)
    np.savez(cache, tempo=feats.tempo, beat_times=feats.beat_times,
             energy_times=feats.energy_times, energy_db=feats.energy_db,
             onset_times=feats.onset_times, mel_times=feats.mel_times, mel_db=feats.mel_db,
             seg_times=feats.seg_times, seg_labels=feats.seg_labels,
             note_times=feats.note_times, note_pitch=feats.note_pitch)
    return feats
