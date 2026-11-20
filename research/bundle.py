#!/usr/bin/env python3
"""
Build a test-bed bundle for a song: every show variant, full length and as a
30-second clip, ready to copy to the Pi and compare with research/testbed.py.

    .venv-research/bin/python research/bundle.py music/song.mp3 --channels 7 --dead 2
    .venv-research/bin/python research/bundle.py a.mp3 b.mp3 --channels 7 --dead 2 --clip-start 62

Writes research/out/<song>/:
    audio/full.wav, audio/clip.wav       the audio, decoded here (the Pi plays the same PCM)
    full/<variant>/full.wav + full.csv   full.wav is a symlink to ../../audio/full.wav
    clip/<variant>/clip.wav + clip.csv
    manifest.json                        variants, descriptions, layout, metrics
    preview.html                         every variant in the browser (open it on the Mac)

The clip starts on a CSV row boundary, so its rows line up exactly with the
full song's. By default it is 20 s before to 10 s after the song's biggest
sudden drop in loudness (to show the drop cut); with no drop, the loudest 30 s.
"""

import argparse
import json
import os
import shutil
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lspcompose import audio as audio_mod  # noqa: E402
from lspcompose import baseline, csvio, metrics, preview, stems, strategies  # noqa: E402
from lspcompose.preview import spans_by_channel  # noqa: E402
from lspcompose.timing import frames_per_row, row_count, row_period  # noqa: E402
from lspcompose.variants import BY_NAME, VARIANTS, Context  # noqa: E402

MANIFEST_VERSION = 1


def parse_channels(text):
    """'2' or '2,5' (1-based) -> 0-based list; '' or -1 -> []."""
    return [int(p) - 1 for p in (text or "").split(",") if p.strip() and int(p) > 0]


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("songs", nargs="+")
    p.add_argument("--channels", type=int, required=True, help="CSV columns = the Pi's gpio_len")
    p.add_argument("--dead", default="", help="1-based channels kept dark (always_off), e.g. 2")
    p.add_argument("--variants", default="all",
                   help="comma-separated subset of: " + ", ".join(v.name for v in VARIANTS))
    p.add_argument("--clip-start", type=float, default=None, help="seconds (default: loudest 30 s)")
    p.add_argument("--clip-seconds", type=float, default=30.0)
    p.add_argument("--timing-ms", type=int, default=25)
    p.add_argument("--brightness-range", type=int, default=255)
    p.add_argument("--relays", choices=sorted(strategies.RELAY_PRESETS),
                   default=os.environ.get("LSP_RELAYS", "mechanical"),
                   help="what switches the lights: 'mechanical' (min on/off 0.20/0.15 s, the "
                        "default) or 'ssr' (solid-state, 0.05/0.05 s). Set LSP_RELAYS=ssr to "
                        "make it your default.")
    p.add_argument("--min-on", type=float, default=None, help="override the preset (seconds)")
    p.add_argument("--min-off", type=float, default=None, help="override the preset (seconds)")
    p.add_argument("--tempo-scale", type=float, default=1.0, choices=(0.5, 1.0, 2.0))
    p.add_argument("--fft-chunk", type=int, default=2048, help="the Pi's chunk_size (production 2048)")
    p.add_argument("--fft-mapping", default="1,2,2,3,4,5,4",
                   help="the Pi's custom_channel_mapping ('' for none)")
    p.add_argument("--order", default="",
                   help="1-based channels from one end of the display to the other, for scale "
                        "sweeps (default 1..N)")
    p.add_argument("--no-stems", action="store_true",
                   help="skip Demucs instrument separation (the 'stems' variant)")
    p.add_argument("--out-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
    return p


def loudest_window(energy_times, energy_db, seconds, duration):
    """Start time of the loudest ``seconds``-long window (not running off the end)."""
    if duration <= seconds:
        return 0.0
    starts = np.arange(0.0, duration - seconds, 1.0)
    power = 10 ** (np.asarray(energy_db) / 10)
    means = [power[(energy_times >= s) & (energy_times < s + seconds)].mean() for s in starts]
    return float(starts[int(np.argmax(means))])


def biggest_drop(feats, duration, margin=5.0):
    """Start time (s) of the longest sustained 10 dB drop away from the song's ends, or None."""
    from lspcompose.safety import runs
    beat_period = float(np.median(np.diff(feats.beat_times))) if len(feats.beat_times) > 1 else 0.5
    dark = strategies.dark_frames(feats.mel_db, feats.mel_times, 10.0,
                                  momentary_s=max(0.3, beat_period))
    t = feats.mel_times
    spans = [(t[a], t[min(b, len(t) - 1)] - t[a]) for a, b in runs(dark)
             if margin < t[a] < duration - margin]
    return max(spans, key=lambda x: x[1])[0] if spans else None


def write_wav(path, pcm, sample_rate, fade_s=0.01):
    """int16 [frames, channels] -> WAV, with short fades so clips don't click."""
    pcm = np.array(pcm, dtype=np.int16, copy=True)
    n = min(int(fade_s * sample_rate), pcm.shape[0] // 2)
    if n:
        ramp = np.linspace(0, 1, n)[:, None]
        pcm[:n] = (pcm[:n] * ramp).astype(np.int16)
        pcm[-n:] = (pcm[-n:] * ramp[::-1]).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.astype("<i2").tobytes())


def link(target_rel, link_path):
    if os.path.lexists(link_path):
        os.remove(link_path)
    os.symlink(target_rel, link_path)


def bundle_one(song, args):
    stem = os.path.splitext(os.path.basename(song))[0]
    out = os.path.join(args.out_dir, stem)
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "audio"))

    pcm, sr, nch = baseline._pcm16(song)
    pcm = pcm[: len(pcm) - len(pcm) % nch].reshape(-1, nch)
    audio = audio_mod.Audio(pcm.astype(np.float32).mean(axis=1) / 32768.0, int(sr), pcm.shape[0], "pcm16")
    feats = audio_mod.analyze_cached(song, audio, os.path.join(args.out_dir, ".cache"), args.tempo_scale)

    layout = strategies.Layout(args.channels, parse_channels(args.dead))
    if any(d >= args.channels for d in layout.dead):
        raise SystemExit(f"--dead {args.dead} is outside --channels {args.channels}")
    rows = row_count(audio.frames, sr, args.timing_ms)
    n = frames_per_row(sr, args.timing_ms)

    mapping = [int(x) for x in args.fft_mapping.split(",") if x.strip()] or None
    if mapping and len(mapping) != args.channels:
        mapping = None
    fft_chunks, _ = baseline.fft_levels(song, args.channels, args.fft_chunk, mapping)
    fft_grid = baseline.levels_on_csv_grid(fft_chunks, sr, args.fft_chunk, rows, args.timing_ms,
                                           layout.dead)
    stem_feats = None
    if not args.no_stems and stems.available():
        print(f"  separating instruments with Demucs ({stems.MODEL}, cached after the first run)...",
              flush=True)
        try:
            stem_feats = stems.features_cached(song, pcm, sr, os.path.join(args.out_dir, ".cache"))
        except Exception as e:   # model download or torch failure: skip stems, keep the rest
            print(f"  ! instrument separation failed ({type(e).__name__}: {e}); skipping 'stems'")
    ctx = Context(feats, rows, sr, layout, args.timing_ms, args.min_on, args.min_off, fft_grid,
                  stem_feats, order=parse_channels(args.order) or None)

    # Clip: row-aligned, so clip row i == full row (k + i)
    # Default clip: around the biggest drop (to show the drop cut), else the loudest part
    if args.clip_start is not None:
        clip_start = args.clip_start
    else:
        drop = biggest_drop(feats, audio.duration)
        if drop is not None:
            clip_start = float(np.clip(drop - 2 / 3 * args.clip_seconds, 0,
                                       max(0.0, audio.duration - args.clip_seconds)))
        else:
            clip_start = loudest_window(feats.energy_times, feats.energy_db, args.clip_seconds,
                                        audio.duration)
    k = int(round(clip_start / row_period(sr, args.timing_ms)))
    clip_rows = min(int(round(args.clip_seconds / row_period(sr, args.timing_ms))), rows - k)
    write_wav(os.path.join(out, "audio", "full.wav"), pcm, sr, fade_s=0)
    write_wav(os.path.join(out, "audio", "clip.wav"), pcm[k * n:(k + clip_rows) * n], sr)

    chosen = VARIANTS if args.variants == "all" else [BY_NAME[v.strip()] for v in args.variants.split(",")]
    skipped = [v.name for v in chosen if v.needs_stems and stem_feats is None]
    if skipped:
        print(f"  skipping {', '.join(skipped)}: install demucs in .venv-research "
              "(see research/README.md) or drop --no-stems")
    summary = []
    for v in chosen:
        if v.name in skipped:
            continue
        levels = v.build(ctx)
        parts = {"full": levels, "clip": levels[k:k + clip_rows]}
        for part, lv in parts.items():
            d = os.path.join(out, part, v.name)
            os.makedirs(d)
            link(os.path.join("..", "..", "audio", f"{part}.wav"), os.path.join(d, f"{part}.wav"))
            csvio.write_csv(lv, os.path.join(d, f"{part}.csv"), args.brightness_range)
        m = metrics.summarize(levels, sr, feats.beat_times, feats.energy_times, feats.energy_db,
                              args.timing_ms, args.min_on, args.min_off)
        m.update(ctx.extra.get(v.name, {}))
        summary.append({"name": v.name, "title": v.title, "description": v.description,
                        "labels": ctx.labels.get(v.name), "metrics": {"full": m},
                        "spans": {p: spans_by_channel(lv, sr, args.timing_ms) for p, lv in parts.items()}})

    manifest = {
        "version": MANIFEST_VERSION, "song": os.path.basename(song), "sample_rate": sr,
        "channels": args.channels, "dead_1based": [d + 1 for d in layout.dead],
        "timing_ms": args.timing_ms, "brightness_range": args.brightness_range,
        "clip_start_s": round(k * n / sr, 3), "clip_seconds": round(clip_rows * n / sr, 3),
        "relays": args.relays, "min_on_s": args.min_on, "min_off_s": args.min_off,
        "tempo_bpm": round(feats.tempo, 1), "duration_s": round(rows * n / sr, 3),
        "variants": [{k2: s[k2] for k2 in ("name", "title", "description", "labels")} |
                     {"metrics": s["metrics"]["full"]} for s in summary],
    }
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, default=float)

    preview.write_bundle_preview(
        os.path.join(out, "preview.html"), stem,
        f"{len(summary)} variants · {args.channels} channels · {feats.tempo:.0f} BPM · "
        f"clip {manifest['clip_start_s']:.0f}-{manifest['clip_start_s'] + manifest['clip_seconds']:.0f} s"
        " · light_delay not applied",
        {"clip": "audio/clip.wav", "full": "audio/full.wav"},
        [c in layout.dead for c in range(args.channels)], summary)
    return out, manifest


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.relays not in strategies.RELAY_PRESETS:
        parser.error(f"--relays / LSP_RELAYS must be one of {sorted(strategies.RELAY_PRESETS)}")
    preset_on, preset_off = strategies.RELAY_PRESETS[args.relays]
    args.min_on = preset_on if args.min_on is None else args.min_on
    args.min_off = preset_off if args.min_off is None else args.min_off
    print(f"Relays: {args.relays} (min on {args.min_on:.2f} s, min off {args.min_off:.2f} s)")
    for song in args.songs:
        out, man = bundle_one(song, args)
        print(f"\n{man['song']}: {len(man['variants'])} variants, {man['channels']} channels, "
              f"{man['tempo_bpm']} BPM, clip {man['clip_start_s']:.1f}-"
              f"{man['clip_start_s'] + man['clip_seconds']:.1f} s")
        print(f"  {'variant':18s} {'on beat':>8s} {'tog/min':>8s} {'violations':>10s} {'duty':>6s}")
        for v in man["variants"]:
            m = v["metrics"]
            if "scale_runs" in m:
                print(f"  ({v['name']}: {m['scale_runs']:.0f} scale runs found)")
            print(f"  {v['name']:18s} {m['on_beat_fraction']:8.2f} "
                  f"{m['toggles_per_channel_min_median']:8.0f} "
                  f"{m['short_on_violations'] + m['short_off_violations']:10.0f} {m['duty_mean']:6.2f}")
        print(f"  preview: open '{os.path.join(out, 'preview.html')}'")
    print("\nCopy to the Pi:  ssh piserver-neo.local mkdir -p lightshowpi/research/out && "
          "rsync -av --exclude .cache research/out/ piserver-neo.local:lightshowpi/research/out/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
