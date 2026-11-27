# Offline composer and test bed (research)

The Mac analyses a song and writes `song.csv` sequences that the Pi already knows how to
play. A CSV beside an audio file replaces that song's FFT show. **No show code changes**,
so it's safe to try during the freeze. Background and design:
[docs/research/ALGORITHMS.md](../docs/research/ALGORITHMS.md).

1. **Mac:** `research/bundle.py` builds one folder per song with every show variant, as the
   full song and as a 30 s clip, plus a browser preview.
2. **Copy** `research/out/` to the Pi.
3. **Pi, over ssh:** `research/testbed.py` plays the variants on the real lights through the
   normal show engine. You choose one at a time from a menu, or play them all back to back.

## Variants

| # | Name | What you'll see |
|---|---|---|
| 1 | `fft` | **Today's show**: one frequency band per channel, ON above a 0.5 threshold. It's simulated with the Pi's own `fft.py` and your `chunk_size = 2048` and channel mapping. |
| 2 | `fft_safe` | The same levels with hysteresis and minimum on/off times. The same look with far less flicker. |
| 3 | `onset_bands` | **Steve's pick.** One band per channel like today, but channels fire on *hits* (drums, note attacks), not loudness. Hits near a beat snap to it. |
| 4 | `onset_dynamic` | Hits per band, plus: how many channels may fire per beat follows loudness (sparse when quiet, full in big moments), and **everything cuts to dark the instant the music drops ≥ 10 dB and stays down**. |
| 5 | `onset_smooth` | The dynamic version with each hit held a full beat: calmer. |
| 6 | `onset_sensitive` | The dynamic version that catches softer hits and cuts on smaller (6 dB) drops, for gentle songs. |
| 7 | `onset_runs` | **Hits + scale sweeps + starts.** The dynamic version plus: fast rising or falling scale runs light the display from one end to the other with the notes (each light comes on once as the run passes it, so relays don't chatter), and when the music crashes back in after a stop, every light flashes on that hit. `--order 1,3,5,…` sets the display's physical end-to-end order. |
| 8 | `stems` | **Instrument per light.** Demucs separates the song; each light follows one instrument: kick drum, snare/hats, bass, low voice, high voice, guitar, piano, other, whichever are present. Spare lights split the loudest instrument into low/high. Drums blink on hits; pitched parts light per note while they sound. The menu and preview label each light. |
| 9 | `beat_chase` | A group of adjacent channels steps along on every beat and bounces at the ends. Louder means a bigger group; quiet passages go dark. |
| 10 | `beat_chase_calm` | The chase on every other beat with at most 3 channels lit. |
| 11 | `vu_meter` | A bar graph from channel 1 up, refreshed on each beat. |
| 12 | `beat_ab` | Odd and even channels swap on every beat. |
| 13 | `sections` | A different look per song section (sparse → A/B → chase → all-pulse, by loudness), and the same look when a section repeats. A short blackout and a flash lead into louder sections. |
| 14 | `test_alternate` | Odd/even swap every 0.5 s whatever the music does. It checks the CSV path and audio/light sync. |

**The drop cut** (variants 4–8): the cut lands on the loudness cliff itself (on Deck
the Halls at 44.8 s it switches off within one 25 ms row of the drop). The lights stay dark until
the music is back within 10 dB (6 dB for "sensitive") of where it was, or for at most 3 s. After
that they relight at the new, quieter level. Near-silence (35 dB below the song's loud level) is
always dark.

**Relay limits: `--relays mechanical|ssr`.** Every variant keeps each pulse at least
`min_on` long with at least `min_off` between pulses:
- `mechanical` (the default): 0.20 / 0.15 s, so contacts don't wear.
- `ssr` (solid-state relays): 0.05 / 0.05 s. That's fast enough for 16th notes and syncopation.

Put `export LSP_RELAYS=ssr` in your shell profile to make `ssr` your default. `--min-on` and
`--min-off` override either preset, and the setting is recorded in each bundle's
`manifest.json`.

**Close-together hits (syncopation).** Variants 4–8 place hits with a scheduler instead of
dropping a hit that arrives while its light is still on:
1. It shortens the previous pulse when both still fit the relay limits.
2. Otherwise it moves the hit to a free neighbouring band's light (hits per band only; an
   instrument never moves to another instrument's light).
3. Otherwise the stronger hit wins.

Variant 3 keeps the original "first hit wins" placement for comparison. The beat variants put
every switch-on on a beat.

## 1. Mac: build the bundles

```bash
python -m venv .venv-research
.venv-research/bin/pip install -r research/requirements-research.txt
.venv-research/bin/pip install demucs        # optional, for the 'stems' variant (pulls in torch)
# (inside a Claude Code sandbox, add --use-deprecated=legacy-certs)

.venv-research/bin/python research/bundle.py music/sample/evilwezil_carol-of-the-bells.mp3 \
    music/sample/ovenrake_deck-the-halls.mp3 --channels 7
open research/out/evilwezil_carol-of-the-bells/preview.html     # every variant in the browser
```

- `--channels` must equal the Pi's `gpio_len` (7 on `piserver-neo`). The test bed checks it and
  refuses a mismatched bundle.
- `--dead 2` keeps channels dark (1-based, like `always_off_channels`). The patterns then skip them.
- Instrument separation (`stems`) takes ~20 s per minute of audio on the Mac CPU the first time
  and is cached in `research/out/.cache` (also its ~50 MB model). `--no-stems` skips it.
- The clip defaults to 20 s before to 10 s after the song's biggest sudden drop, so you can see the
  drop cut; with no drop, it's the loudest 30 s. Use `--clip-start 62` (seconds) to choose your own, and
  `--clip-seconds` to change its length.
- To pick variants: `--variants fft,beat_chase,sections`. Use `--tempo-scale 0.5` or `2` if the
  beat tracker got a song's tempo half or double.
- The audio is decoded on the Mac and saved as WAV. The Pi plays exactly the samples the CSVs were
  timed against, so there's no decoder offset between them.

## 2. Copy to the Pi

```bash
ssh piserver-neo.local mkdir -p lightshowpi/research/out
rsync -av --exclude .cache research/out/ piserver-neo.local:lightshowpi/research/out/
```

The test bed itself is in the repo (`research/testbed.py`). Merge the `research-composer` PR
and run `make deploy HOST=piserver-neo`. Don't rsync `research/` code into the Pi's checkout:
the deploy's `git` step won't overwrite untracked files, and the next deploy would fail.

## 3. Pi: run the test bed

```bash
ssh piserver-neo.local
cd ~/lightshowpi && source ~/miniforge3/bin/activate lightshowpi-neo
python research/testbed.py
```

It asks for your sudo password once, stops the running show, and shows a menu:

```
━━ evilwezil_carol-of-the-bells  ·  7 channels  ·  123 BPM  ·  mode: 30 s clip (14-44 s)
   1. Today's show (FFT)           What plays now: one frequency band per channel...
   2. Today's show, relay-safe     ...
   ...
  number = play it   a = all back to back   l = loop all until Ctrl-C
  m = switch clip/full   i N = details of variant N   s = pick another song   q = quit
```

- **Compare the same 30 s back to back:** press `a`. Each variant's title prints as it starts.
- `l` loops all variants until **Ctrl-C**. Ctrl-C always stops the current variant and turns the
  lights off.
- **Without the menu:**
  `python research/testbed.py run evilwezil_carol-of-the-bells --variants fft,beat_chase,sections --repeat 3`
- Each variant runs `py/synchronized_lights.py --file=…` with a generated `config/testbed.cfg`.
  That file is your `overrides.cfg` with the pre-show and postshow off, and with
  `always_on`/`always_off`/`invert_channels` reset, so every relay shows every pattern.
  `--keep-overrides` keeps those three. `light_delay`, Nanoleaf (`[output:nanoleaf]`) and network
  clients behave as in the real show.
  The show's own output goes to `logs/testbed.log`.
- **When done:** the show stays stopped. Run `bin/start_music_and_lights`, or let cron start it.
  The test bed prints cron's start time when it can read the crontab.

Each variant starts a fresh show process, so expect a few seconds of dark between them. A run
that stops early (for example a column mismatch) is reported with the end of the log. Judge
`sections` in full-song mode (`m`), because a 30 s clip usually sits inside one section.

**Step 0:** play `test_alternate` (menu 14) first. It passes when odd and even channels swap steadily every
0.5 s and stay in time with the music, with no audio dropouts.

## Single-strategy tool

`research/compose.py` writes one strategy's CSV and a preview, for quick experiments. It's
superseded by `bundle.py` for testing on the lights.

## Tests

```bash
SYNCHRONIZED_LIGHTS_HOME=$(pwd) .venv-research/bin/python -m pytest -q -p no:cacheprovider tests/test_research_composer.py
```

CI and the dev env run the same file; the librosa tests skip there.
