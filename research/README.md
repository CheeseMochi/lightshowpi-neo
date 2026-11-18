# Offline composer (research)

`research/compose.py` analyses a song on the Mac and writes a `song.csv` that the Pi
already knows how to play. A CSV beside an mp3 replaces that song's FFT show. **No show
code changes**, so it's safe to try during the freeze. Background and design:
[docs/research/ALGORITHMS.md](../docs/research/ALGORITHMS.md).

| Strategy | What it does |
|---|---|
| `beat_chase` | Each beat, a group of adjacent channels lights and the group moves one place along (bouncing at the ends). Louder passages light more channels, and quiet passages go dark. Beats faster than `--min-step` (0.4 s) skip every other beat. ON edges land on beats, and every pulse respects `--min-on` and `--min-off` (0.20 s and 0.15 s, for mechanical relays). |
| `alternate` | Step-0 test pattern: odd and even channels swap every 0.5 s. It proves the CSV path and A/V sync on the Pi. |

Every run also scores the FFT show you have today on the same song, using the Pi's own
`py/fft.py` with production `chunk_size = 2048` and `custom_channel_mapping`, so you can
compare them:

| Sample song | | On beat (±50 ms) | Toggles per channel per min | Relay min-time violations |
|---|---|---|---|---|
| Carol of the Bells | `beat_chase` | 100% | 144 | 0 |
| | FFT today | 34% | 315 | 4,212 |
| Deck the Halls | `beat_chase` | 100% | 160 | 0 |
| | FFT today | 38% | 301 | 1,892 |

These numbers say it's relay-safe and on the beat. They don't say it looks good. Judge
that from the preview and the lights.

## On the Mac

```bash
python -m venv .venv-research
.venv-research/bin/pip install -r research/requirements-research.txt
# (inside a Claude Code sandbox, add --use-deprecated=legacy-certs)

.venv-research/bin/python research/compose.py music/sample/evilwezil_carol-of-the-bells.mp3 \
    --channels 7 --dead 2
open research/out/evilwezil_carol-of-the-bells-beat_chase/preview.html
```

- `--channels` **must equal the Pi's `gpio_len`**, or that song stops with "Number of
  channels in sequence does not match". Print it on the Pi (below).
- `--dead 2` keeps channel 2 dark, with 1-based numbers like `always_off_channels`. The chase
  then skips it.
- Useful knobs:
  - `--tempo-scale 0.5` or `2`: fix a half- or double-tempo beat track. It also calms a busy song.
  - `--max-lit 3`
  - `--beats-per-bar 4` (`3` for waltzes): full flashes on loud downbeats.
  - `--no-bounce`
- Output goes to `research/out/<song>-<strategy>/`. That folder holds a copy of the song, the CSV
  beside it, `preview.html` and `metrics.json`. Nothing is written next to your playlist songs.
  `research/out/` is git-ignored.

## On the Pi (piserver-neo, before the 17:30 cron start)

```bash
# 1. Mac: copy the composed folders over
ssh piserver-neo.local mkdir -p lightshowpi/research/out
rsync -av --exclude .cache research/out/ piserver-neo.local:lightshowpi/research/out/

# 2. Pi: set up the shell, and check the column count (must match --channels)
ssh piserver-neo.local
cd ~/lightshowpi && source ~/miniforge3/bin/activate lightshowpi-neo
export SYNCHRONIZED_LIGHTS_HOME=$PWD
python -c "
import sys, importlib.util as u; sys.path.insert(0, 'py'); import configuration_manager as c
m = c.Configuration(); led = u.find_spec('bibliopixel') is not None
for lc in (m.configs.led or []) if led else []: m.set_led(config_file=lc)
print('gpio_len =', m.hardware.gpio_len, '| LED configs loaded:', led)"

# 3. Stop the show, then play one file at a time (each returns to the prompt;
#    wait for the song to end or run bin/stop_music_and_lights)
sudo -v                          # cache sudo first: the script runs sudo in the background
bin/stop_music_and_lights
bin/start_music_and_lights --file=$PWD/research/out/evilwezil_carol-of-the-bells-alternate/evilwezil_carol-of-the-bells.mp3
bin/start_music_and_lights --file=$PWD/music/sample/evilwezil_carol-of-the-bells.mp3            # FFT today
bin/start_music_and_lights --file=$PWD/research/out/evilwezil_carol-of-the-bells-beat_chase/evilwezil_carol-of-the-bells.mp3
```

After each song, your `postshow_script` (the random twinkle, up to 30 min) starts. That's
expected: the next `start_music_and_lights` stops it, and `bin/stop_music_and_lights` ends
it when you're done.

**Step 0 (`alternate`) passes** when odd and even channels swap steadily every 0.5 s and stay
in time with the music to the end. The music must also play without dropouts, since a CSV song
reads a row every 25 ms. Then compare the FFT and `beat_chase` runs by eye.

`light_delay` still applies to CSV songs: 150 ms with your settings, against 139 ms for FFT
songs. The preview doesn't apply it. If `beat_chase` looks consistently early or late on the
Pi while `alternate` looks fine, suspect the decoders: the Mac decodes with soundfile, the Pi
with audioread/ffmpeg. Tell Claude, and the CSV can be shifted.

## Tests

```bash
SYNCHRONIZED_LIGHTS_HOME=$(pwd) .venv-research/bin/python -m pytest -q -p no:cacheprovider tests/test_research_composer.py
```

The CI and dev env run the same file. The librosa test skips there.
