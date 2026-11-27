#!/usr/bin/env python3
"""
Compose a LightShowPi sequence (song.csv) for a song, offline, on the Mac.

    conda activate lightshowpi-neo-lab        # environment-lab.yml (make lab-env)
    python research/compose.py music/song.mp3 --channels 7 --dead 3
    python research/compose.py song.mp3 --channels 7 --strategy alternate

Writes research/out/<song>-<strategy>/ with a copy of the song, <song>.csv beside it (so the
Pi plays the CSV instead of the FFT when given that file), preview.html and
metrics.json. Nothing is written next to your playlist songs: a CSV there would
silently replace that song's FFT show, and a wrong column count stops the song.

--channels must equal the Pi's gpio_len (print it on the Pi with the command in
research/README.md). --dead takes 1-based channel numbers like always_off_channels;
those columns stay dark and the chase skips them.
"""

import argparse
import json
import os
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lspcompose import audio as audio_mod  # noqa: E402
from lspcompose import baseline, csvio, metrics, preview, strategies  # noqa: E402
from lspcompose.timing import row_count  # noqa: E402

STRATEGIES = ("beat_chase", "alternate")


def parse_channels(text: str):
    """'3' or '3,5' (1-based) -> [2] / [2, 4] (0-based); '' or -1 -> []."""
    out = []
    for part in (text or "").split(","):
        part = part.strip()
        if part and int(part) > 0:
            out.append(int(part) - 1)
    return out


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("songs", nargs="+", help="audio files (mp3, wav, ...)")
    p.add_argument("--channels", type=int, required=True, help="CSV columns = the Pi's gpio_len")
    p.add_argument("--dead", default="", help="1-based channels to keep dark, e.g. 3 (always_off)")
    p.add_argument("--strategy", choices=STRATEGIES, default="beat_chase")
    p.add_argument("--timing-ms", type=int, default=25, help="[custom_sequences] timing (default 25)")
    p.add_argument("--brightness-range", type=int, default=255)
    p.add_argument("--min-on", type=float, default=0.20, help="seconds (mechanical relays)")
    p.add_argument("--min-off", type=float, default=0.15, help="seconds")
    p.add_argument("--min-step", type=float, default=0.40,
                   help="shortest chase step; faster beats are skipped (>= min-on + min-off)")
    p.add_argument("--max-lit", type=int, default=None, help="most channels lit at once")
    p.add_argument("--quiet-db", type=float, default=35.0, help="dark this far below the loud level")
    p.add_argument("--tempo-scale", type=float, default=1.0, choices=(0.5, 1.0, 2.0),
                   help="fix a half/double tempo from the beat tracker")
    p.add_argument("--beats-per-bar", type=int, default=0,
                   help="enable full flashes on loud downbeats (e.g. 4; 3 for waltzes)")
    p.add_argument("--no-bounce", action="store_true", help="wrap the chase instead of bouncing")
    p.add_argument("--fft-chunk", type=int, default=2048,
                   help="baseline: the Pi's [audio_processing] chunk_size (production 2048)")
    p.add_argument("--fft-mapping", default="1,2,2,3,4,5,4",
                   help="baseline: custom_channel_mapping ('' for none; production 1,2,2,3,4,5,4)")
    p.add_argument("--no-baseline", action="store_true", help="skip the FFT-show comparison")
    p.add_argument("--out-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
    return p


def compose_one(song: str, args) -> dict:
    """Compose one song; returns a summary dict."""
    stem, ext = os.path.splitext(os.path.basename(song))
    out_dir = os.path.join(args.out_dir, f"{stem}-{args.strategy}")
    os.makedirs(out_dir, exist_ok=True)

    audio = audio_mod.load(song)
    rows = row_count(audio.frames, audio.sample_rate, args.timing_ms)
    layout = strategies.Layout(args.channels, parse_channels(args.dead))
    if any(d >= args.channels for d in layout.dead):
        raise SystemExit(f"--dead {args.dead} is outside --channels {args.channels}")

    if args.strategy == "alternate":
        levels = strategies.alternate(rows, audio.sample_rate, layout, timing_ms=args.timing_ms)
        beats = np.array([])
        energy_t, energy_db, tempo = np.array([0.0]), np.array([0.0]), 0.0
    else:
        feats = audio_mod.analyze_cached(song, audio, os.path.join(args.out_dir, ".cache"),
                                         tempo_scale=args.tempo_scale)
        opts = strategies.ChaseOptions(
            min_on=args.min_on, min_off=args.min_off, min_step=args.min_step,
            quiet_db=args.quiet_db, max_lit=args.max_lit, beats_per_bar=args.beats_per_bar,
            bounce=not args.no_bounce)
        levels = strategies.beat_chase(feats.beat_times, feats.energy_times, feats.energy_db,
                                       rows, audio.sample_rate, layout, args.timing_ms, opts)
        beats, energy_t, energy_db, tempo = (feats.beat_times, feats.energy_times,
                                             feats.energy_db, feats.tempo)

    song_copy = os.path.join(out_dir, stem + ext)
    if os.path.abspath(song_copy) != os.path.abspath(song):
        shutil.copyfile(song, song_copy)
    csv_path = os.path.join(out_dir, stem + ".csv")
    csvio.write_csv(levels, csv_path, args.brightness_range)
    csvio.read_csv(csv_path, args.brightness_range, channels=args.channels)  # Pi-style check

    stats = metrics.summarize(levels, audio.sample_rate, beats, energy_t, energy_db,
                              args.timing_ms, args.min_on, args.min_off)
    if not args.no_baseline and beats.size:
        mapping = [int(x) for x in args.fft_mapping.split(",") if x.strip()] or None
        if mapping and len(mapping) != args.channels:
            mapping = None
        fft_levels, _ = baseline.fft_levels(song, args.channels, args.fft_chunk, mapping)
        fft_on = baseline.on_csv_grid(fft_levels, audio.sample_rate, args.fft_chunk, rows,
                                      args.timing_ms, layout.dead)
        fft_stats = metrics.summarize(fft_on, audio.sample_rate, beats, energy_t, energy_db,
                                      args.timing_ms, args.min_on, args.min_off)
        stats.update({f"fft_{k}": v for k, v in fft_stats.items()})
    stats.update({"tempo_bpm": round(tempo, 1), "rows": rows, "channels": args.channels,
                  "sample_rate": audio.sample_rate, "decoder": audio.decoder,
                  "strategy": args.strategy})
    with open(os.path.join(out_dir, "metrics.json"), "w") as f:
        json.dump(stats, f, indent=2, default=float)
    subtitle = (f"{args.strategy} · {args.channels} channels · {tempo:.0f} BPM · "
                f"{audio.duration:.0f} s · light_delay not applied")
    preview.write_preview(os.path.join(out_dir, "preview.html"), levels, audio.sample_rate,
                          os.path.basename(song_copy), stem, subtitle, beats,
                          {k: v for k, v in stats.items() if isinstance(v, float)},
                          layout.dead, args.timing_ms)
    return {"song": stem, "dir": out_dir, **stats}


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.strategy == "beat_chase" and args.min_step < args.min_on + args.min_off:
        raise SystemExit("--min-step must be at least --min-on + --min-off")
    for song in args.songs:
        s = compose_one(song, args)
        print(f"\n{s['song']}: {s['strategy']}, {s['tempo_bpm']} BPM, {s['rows']} rows x "
              f"{s['channels']} channels ({s['decoder']}, {s['sample_rate']} Hz)")
        print(f"  {'metric':32s} {'composed':>9s} {'fft today':>10s}")
        for key in ("on_beat_fraction", "toggles_per_channel_min_median", "short_on_violations",
                    "short_off_violations", "duty_mean", "silence_lit_fraction",
                    "mean_channel_correlation"):
            fft = s.get(f"fft_{key}")
            print(f"  {key:32s} {s[key]:9.3f} {fft if fft is None else format(fft, '10.3f')}")
        print(f"  preview: open '{os.path.join(s['dir'], 'preview.html')}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
