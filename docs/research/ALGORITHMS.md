# Light Algorithm Research: Survey and Recommendations

> **Status:** first draft, research only. Nothing here changes runtime code.
> **Scope:** the ROADMAP "Light Algorithm Research" initiative: show generation that reacts to more than frequency spikes.
> **Conventions:** **[UNVERIFIED]** marks a claim not confirmed against a primary source in this pass. **[ESTIMATE]** marks the author's engineering judgment. Code facts in §1 were read from this tree. Licenses, versions and repo status in §8 were fetched on 2026-11-15. librosa API names and doc links, and classic paper citations, come from memory and are marked **[UNVERIFIED]** where they matter.

---

## TL;DR

1. Today's engine measures **level per frequency band**, normalizes every band on its own and switches relays at a fixed 0.5 threshold. Shows look "random/busy" for three reasons: every channel fires about equally often whatever the music is doing; the lights follow energy rather than *events* (beats and onsets); and nothing knows about musical time (beats, bars, sections).
2. The biggest, cheapest gain is an **offline composer** that writes the **existing `song.csv` format**. Steve's Mac does the heavy analysis (beats, downbeats, sections, stems). The Pi plays the result with **zero runtime changes**.
3. Proposed first four strategies, in order: `beat_chase` → `onset_bands` → `sections` → `stems`. All of them share a **relay-safety post-pass** that enforces minimum on/off times.
4. Recommended libraries: **librosa** (ISC) for features and segmentation, **beat_this** (MIT, code *and* weights) for beats/downbeats, **Demucs** (MIT, but archived) for stems. madmom's pretrained models are **CC BY-NC-SA**, and aubio (GPL) and Essentia (AGPL) are copyleft. Keep all of these optional and out of the shipped code.
5. ML is promising but data-poor. The only public music→lighting dataset found is RPMC-L2 (Skip-BART). An **LLM "director"** that picks a strategy and palette per section is the cheapest way to try "AI composing" without training data.

---

## 1. How the current engine works (verified from code)

### 1.1 FFT path (`sequence_type == 'auto'`)

| Step | Where | Detail |
|---|---|---|
| Decode | `synchronized_lights.py` `AudioFileWrapper` | audioread → int16 interleaved bytes |
| Chunk | `[audio_processing] chunk_size = 4096` frames | ≈ 92.9 ms at 44.1 kHz ≈ **10.8 updates/s**, non-overlapping |
| Window + FFT | `fft.py` `FFT.calculate_levels` | **Left channel only**: `data_stereo[::2]` with `input_channels=2` hard-coded in `setup_audio`. Hann window, `rfft`, power = \|X\|² |
| Bands | `FFT.calculate_channel_frequency` | One log-spaced band per channel between `min_frequency=20` and `max_frequency=15000`. With 8 channels that is ≈1.2 octaves each, and the lowest band (20–~46 Hz) slices only 3 FFT bins (indices 1–3) |
| Level | `calculate_levels` | `log10(sum(power in band))` |
| Cache | `.song.mp3.sync` / `.cfg` | Rows of per-band levels, with row 0 = std and row 1 = mean of the **non-zero** values per band. Before a cache exists, mean=12.0 and std=1.5 |
| Normalize | `Lightshow.update_lights` | `b = (x − mean + SD_low·std) / (std·(SD_low+SD_high)) · (1 − attenuate_pct/100)`, then clipped to 0..1. Defaults: `SD_low=0.5`, `SD_high=0.75`, `attenuate_pct=0` |
| Decay | `update_lights` | Optional peak-hold with linear release `decay_factor` (default **0 = off**) |
| Relay out | `hardware_controller.Channel` | Non-PWM: `digitalWrite(b > 0.5)`, a **hard threshold with no hysteresis**. PWM: `softPwmWrite(int(b·pwm_max))` |
| LED out | `led_module.Led.write_full` | Per-channel scalar → `pattern_color_map` (MONO/FREQ1/MAP1/MAP2/…) × `pattern_type` (CBARS/FULL/LBARS, matrix types). `update_throttle=1` skips every other frame (≈5.4 fps under FFT, ≈20 fps under 25 ms CSV) |

Test patterns (`flash`, `fade`, `cylon`, `step`, `dance`, `random_pattern`) live on the `Patterns` class in `hardware_controller.py` (added on the `test-mode` branch, Nov 2026) and are driven by the CLI and the API's test mode. There is no Nanoleaf output code yet; `tools/nanoleaf_probe.py` (branch `nanoleaf-probe`) is the first step.

### 1.2 The CSV sequence path: the target format for offline composers

Every rule below comes from `get_song()`, `setup_audio()`, `play_song()` and `update_lights()` in `py/synchronized_lights.py`, plus `configuration_manager.py`.

| Property | Exact behavior |
|---|---|
| **Location** | Same directory and basename as the audio, with the extension replaced: `music/foo.mp3` → `music/foo.csv` (`filename.rsplit('.',1)[0] + '.csv'`). If the file exists, the song plays from the CSV and the FFT/cache is skipped. |
| **Header** | **None.** The first line is read once to count columns, then the file is reopened and the first line is played as row 0. |
| **Columns** | Must equal `hardware.gpio_len`, or the process prints an error and calls `exit()`. **MIRROR**: `len(gpio_pins)`. **LEDONLY**: `led_channel_count`. **EXTEND**: `len(gpio_pins) + led_channel_count`, with GPIO channels first and LED channels after. `configuration_manager.py` re-reads `led_channel_count` after parsing `custom_per_channel`, so the length of `custom_per_channel` does *not* change the column count. Behavior with several LED configs was not checked. |
| **Row parsing** | `list(map(float, line.strip().split(',')))`. Any float works. A **blank line or a comment line mid-file raises `ValueError`**. A trailing newline at EOF is fine. |
| **Row period** | One row per audio chunk of `N = int(sample_rate · timing / 1000)` frames. `sample_rate` is the file's **native** rate as audioread reports it; a composer that analyses at a resampled rate (librosa defaults to 22050 Hz) must still compute N from the native rate. `timing` comes from `[custom_sequences] timing` (an **integer** in ms, default 25). At 44.1 kHz, N = 1102 frames = **24.989 ms**, not 25 ms. Over a 3-minute song that is ≈80 ms of drift if a composer assumes exactly 25 ms. **Compute row `i`'s time as `i·N/sr`.** At 48 kHz, N = 1200 exactly. |
| **Value scale** | `b = clip(v / brightness_range, 0, 1)`, with `[custom_sequences] brightness_range` an integer, default 255. |
| **Relays** | A non-PWM channel is ON iff `b > 0.5`, i.e. `v > 127.5` at the default range. A PWM channel gets `int(b·pwm_max)`. |
| **Bypassed** | SD_low/SD_high, attenuate_pct and decay_factor do not apply to CSV values. |
| **Still applied** | `light_delay`. On physical GPIO channels, active-low inversion and the always_on / always_off / inverted overrides (`set_array(..., True)` → `Channel.set_action`, verified). LED channels receive the raw clipped value and still go through the color map and pattern. |
| **Length** | Rows past EOF play as all zeros. Extra rows after the audio ends are ignored. |
| **Scope** | `[custom_sequences]` is **global**. Per-song `.cfg` overrides only read `custom_lightshow` and `custom_audio_processing`. Each `synchronized_lights.py` process plays one song, so a playlist can mix CSV and FFT songs. |

**What the CSV *cannot* express:** it carries one brightness value per channel, so **no per-LED color**. Color comes from the configured `pattern_color_map`. A hack within zero runtime changes: with `MAP1`/`MAP2` the brightness value indexes a color map, so a composer could choose a value to choose a color, at the cost of brightness control **[ESTIMATE: workable for LED-only layouts]**. Real chroma/mood→color needs a runtime change (see §7c).

**Observation, not fixed here:** `chunks_per_sec = ((16·nch·sr)/8)/chunk_size` divides **bytes**/s by a **frame** count, which makes `light_delay` 4× too long for 16-bit stereo. The default `light_delay = 0` hides this.

---

## 2. Critique of the FFT approach

| Problem | Why it looks random or busy | Affects |
|---|---|---|
| **Per-band z-scoring** | Every band is scaled to its own mean/std, so every channel ends up ON for a similar share of the song whether or not that band carries anything musically salient. Quiet treble bands get amplified hiss and cymbal wash. | All |
| **Level, not events** | People judge sync by **onsets and beats** (the "hit"). Band level tracks sustained energy, so pads and reverb tails hold lights on, and dense mixes keep every channel near threshold. | All |
| **No musical time** | No beat grid, bar or phrase. Nothing repeats when the chorus repeats, and verse and chorus look alike apart from loudness. | All |
| **Hard 0.5 threshold, no hysteresis** | A band hovering near its mean toggles every frame (≈10.8 Hz), which looks like flicker and wears mechanical relays. Mechanical relays are typically rated around 10⁵ electrical operations **[UNVERIFIED: depends on part]**. SSRs don't wear the same way but still flicker. | Relays |
| **Band ≠ instrument** | One sung note spreads harmonics over 3–5 bands, so several channels fire together for one event. Kick and bass share the low bands. | All |
| **Coarse, blurred timing** | Non-overlapping 93 ms windows smear onsets by up to one frame. Only the left channel is analysed, so hard-panned parts are missed. | All |
| **LED color is static** | Color depends only on channel index or brightness, never on harmony or mood. LED updates are throttled to ~5 fps under FFT. | LEDs |
| **Cold start** | The first play has no cache and uses mean=12 / std=1.5, which can over- or under-drive. | All |

Relays and LEDs need different treatment. Relays are a **1-bit, slow, wear-limited** medium: they want few, well-timed, beat-aligned transitions and groups of channels used as *patterns* (chases, alternations). LEDs and PWM can show continuous envelopes (attack/decay), color and spatial motion, so they suit smooth energy plus accents.

---

## 3. Music information retrieval (MIR) features useful for lights

| Feature | What it gives lights | Tools (offline / live) | Notes |
|---|---|---|---|
| **Onset strength / spectral flux** | "Something just hit" curve; per-band flux gives per-channel triggers | librosa `onset_strength(_multi)` ([docs](https://librosa.org/doc/latest/generated/librosa.onset.onset_strength.html) **[UNVERIFIED: link and names not re-checked for librosa 1.0]**); numpy flux (live); aubio `onset` (live) | Positive half-wave-rectified log-spectral difference. Cheap in numpy. |
| **Beat tracking** | Grid to quantize events to | librosa `beat_track` (dynamic programming, Ellis 2007 **[UNVERIFIED citation]**); **beat_this** ([repo](https://github.com/CPJKU/beat_this), Foscarin et al. ISMIR 2024, [arXiv:2407.21658](https://arxiv.org/abs/2407.21658)); madmom DBN ([repo](https://github.com/CPJKU/madmom)); BeatNet online ([repo](https://github.com/mjhydri/BeatNet), [arXiv:2108.03576](https://arxiv.org/abs/2108.03576)) | beat_this reports state-of-the-art accuracy without DBN post-processing; "small" weights are ≈8 MB |
| **Downbeats / meter** | Bar-level accents (big flash on beat 1), phrase counting | beat_this, madmom, BeatNet, all-in-one | librosa has no downbeat tracker **[UNVERIFIED for 1.0]** |
| **Tempo** | Pattern speed (chase step = beat or half-beat), slow-song detection | from the beat grid; librosa `tempo` | Pick half or double time for relay-friendly rates |
| **Structure segmentation** | Section boundaries + labels (verse/chorus/…) → density and style changes, repeat-consistent looks | librosa Laplacian segmentation ([example](https://librosa.org/doc/latest/auto_examples/plot_segmentation.html), McFee & Ellis 2014 **[UNVERIFIED link/citation]**); **MSAF** ([repo](https://github.com/urinieto/msaf), MIT); **all-in-one** ([repo](https://github.com/mir-aidj/all-in-one), Kim & Nam WASPAA 2023, [arXiv:2307.16425](https://arxiv.org/abs/2307.16425)) gives beats, downbeats, and *functional* labels (intro/verse/chorus/…) in one model | all-in-one is MIT but pulls in madmom (git), NATTEN and Demucs, so it is fragile to install |
| **Chroma / key** | Palette from key/mode, hue shift on chord change | librosa `chroma_cqt`/`chroma_stft` + Krumhansl key profile | Needs runtime color support for LEDs (§1.2) |
| **Loudness / energy** | Intensity envelope, section energy ranking, "quiet = dark" | librosa `rms`; **pyloudnorm** (MIT, EBU R128 / BS.1770) | Use a *global* song-level scale, not per-band z-scores |
| **Source separation** | Drums / bass / vocals / other stems → channel groups | **Demucs** htdemucs ([repo](https://github.com/facebookresearch/demucs), MIT, **archived Jan 2025**, maintained fork [adefossez/demucs](https://github.com/adefossez/demucs)); Spleeter (MIT, TensorFlow, **Python < 3.12**) | Demucs CPU time is about 1.5× track length on a desktop CPU (per its README) |
| **Vocal activity** | Turn a "singer" prop on during vocals, or hold back during instrumental breaks | Demucs vocal stem RMS + hysteresis (simplest); Essentia classifiers (AGPL code, CC BY-NC-SA models) | No lightweight permissively licensed VAD was verified **[UNVERIFIED]** |

---

## 4. Composition strategies (design vocabulary)

These building blocks can be mixed per section.

- **Beat-gating:** quantize every relay transition to the beat or ½-beat grid. Arbitrary flicker becomes rhythm.
- **Section-aware density:** set how many channels are active and how often they change from section energy rank. Verse sparse, chorus full, bridge different, outro fading. Reuse the same "look" whenever a section label repeats.
- **Stem → channel groups:** drums → a relay chase group; bass → a PWM floodlight; vocals → a dedicated prop or LED strip; other (keys/guitar) → the remaining channels driven by onsets.
- **Call and response:** alternate group A and group B on alternating bars or phrases, or on detected call/response between stems.
- **Chases / sweeps:** step a "head" through an ordered channel list every beat (relays) or move a gradient along a strip with a beat-locked phase (LEDs). Bounce on downbeats.
- **Accents:** downbeats of section starts → full flash (all ON for one beat). Big onsets after silence → blackout-then-hit.
- **Energy envelope:** loudness drives PWM and LED brightness, and the number of channels lit in a "VU" pattern.
- **Color (LEDs):** key/mode → palette (major = warm/bright, minor = cool) **[ESTIMATE: aesthetic heuristic]**; chord change → hue step; section label → palette. Needs runtime color unless the MAP1 hack is used.
- **Anti-chatter (all relays):** `min_on`, `min_off` (e.g. ≥ 150–250 ms **[ESTIMATE]**), a per-channel maximum toggle rate, and hysteresis on any continuous→binary step.
- **Lookahead:** with offline analysis, effects can *anticipate*: pre-fade before a drop, ensure a blackout lands one beat before the chorus, and compensate per-output latency (Nanoleaf over Wi-Fi).

---

## 5. Prior art and AI/ML composers

### 5.1 Hobby and commercial tools

| Tool | What its "auto" does (sourced) | License |
|---|---|---|
| **xLights** | Timing tracks from Vamp plugins ("other than the first three, the remaining are from the VAMP plugins": [manual](https://manual.xlights.org/xlights/chapters/chapter-four-sequencer/timing-tracks)). The Queen Mary plugins (beat tracker, onset detector, polyphonic transcription) have been bundled since 4.1.12 ([forum](https://auschristmaslighting.com/threads/xlights-4-1-12-vamp-plugins-now-part-of-xlights-audacity-no-longer-needed.7677)). Optional AI services "generate color palettes, generate images" and help auto-map models on import ([manual](https://manual.xlights.org/xlights/chapters/chapter-five-menus/file/settings/services)). **No documented full-sequence generator.** | GPL-3.0 **[UNVERIFIED in this pass]** |
| **Vixen Lights** | "Marks for audio beats and bars can be automatically detected" (Tools → Audio → Beat/Bar detection, [docs](https://www.vixenlights.com/docs/usage/sequencer/sequencer-basics/audio-functions/)). The marks then drive effects the user places by hand. | open source **[UNVERIFIED license]** |
| **Light-O-Rama Beat Wizard** | "analyze the song … to try to determine its tempo, and can insert timings and lighting effects into the sequence based upon it" ([help](https://www.lightorama.com/help/the_beat_wizard.htm)) | proprietary |
| **LedFx** | Real-time audio-reactive effect engine over the network; depends on an aubio fork ([repo](https://github.com/LedFx/LedFx), [pyproject](https://raw.githubusercontent.com/LedFx/LedFx/main/pyproject.toml)) | GPL-3.0 |
| **WLED audio-reactive** | 16-channel GEQ from an FFT, AGC (Off/Normal/Vivid/Lazy), squelch noise floor ([docs](https://kno.wled.ge/advanced/audio-reactive/)) | EUPL-1.2 (WLED-MM) |
| **Philips Hue Sync** | Proprietary music sync; the Entertainment API docs need a login | **[UNVERIFIED]** |
| **Nanoleaf** | Open API "extControl" v2 UDP streaming on port 60222, per third-party clients ([picoleaf](https://github.com/tessro/picoleaf/blob/main/client.go)). The old docs URL redirects; current docs appear to be at [nanoleaf.atlassian.net](https://nanoleaf.atlassian.net/wiki/spaces/nlapid/overview) **[UNVERIFIED: not opened]**. Frame-rate limits and Permanent Outdoor Lights support **[UNVERIFIED]**. | — |
| **LLM attempts** | On xlightsseq.com, users report mixed results asking chat LLMs to edit `.xsq` files directly: unchanged or broken files, one success on timing tracks ([thread](https://xlightsseq.com/threads/ai-created-sequences.5680/post-7393)) | — |

The takeaway is that the community automates **timing marks** and leaves placing effects to humans. LightShowPi already has a fully automatic (FFT) mode, so the opportunity is to make the automatic mode *musical*, not to build a sequencer UI.

### 5.2 Academic work (fetched)

| Paper | Summary |
|---|---|
| Zhao, Jin, Zhou, Zhang, *Automatic Stage Lighting Control: Is it a Rule-Driven Process or Generative Task?* (2025, ICLR 2026 poster) [arXiv:2506.01482](https://arxiv.org/abs/2506.01482) | **Skip-BART**: audio → hue + intensity sequence. Releases the **RPMC-L2** dataset (699 live-house rock/punk/metal samples, labels from video), code and weights. |
| Same authors, *Stage Light is Sequence²: Multi-Light Control via Imitation Learning* (2026) [arXiv:2605.03660](https://arxiv.org/abs/2605.03660) | **SeqLight**: extends Skip-BART to distribute a palette over several fixtures. |
| Robinson et al., *Glow with the Flow: AI-Assisted Creation of Ambient Lightscapes for Music Videos* (2026) [arXiv:2602.08838](https://arxiv.org/abs/2602.08838) | Generates editable light sequences as a starting point for human refinement (semi-automatic). |
| B. Wilson, *LightShow: Abstract Representations of Music Lighting in Python* (MIT thesis, 2022) [dspace](https://dspace.mit.edu/handle/1721.1/147314) | Learning from concert video failed, so it builds a rule-based Python abstraction for audio-driven shows. |

### 5.3 Learned approaches for LightShowPi

| Idea | Data | Effort | Assessment |
|---|---|---|---|
| **LLM "director"**: give an LLM a compact JSON of sections (label, energy, tempo, key, vocals y/n) and the channel layout; it returns a per-section plan (strategy, density, palette, group assignment) that the deterministic composers render | none | S–M | **Best first ML step.** Controllable and cheap; the LLM never emits frames. Needs network and API cost, or a local model. Run offline on the Mac. |
| **Preference tuning**: Steve rates A/B renders and a bandit or Bayesian optimizer tunes strategy parameters (min_on, density curve, chase speed) | Steve's ratings | M | Small, personal, effective **[ESTIMATE]** |
| **Skip-BART-style model** for LED hue/intensity | RPMC-L2 (one genre family, stage lights) | L | Domain gap: rock stage lighting vs. Christmas pop. Possible fine-tune target later. |
| **Learn from community sequences** (xLights `.xsq`/`.fseq` + audio) | Many exist, but most are **sold** or copyrighted with their audio, and every one maps to a different prop layout | XL | Licensing and layout-normalization blockers. Not recommended near term. |
| **Mood/genre → palette** with pretrained taggers | Essentia models | S | Model license is CC BY-NC-SA, so personal use only **[verify intent]** |

---

## 6. Real-time vs. offline

| Analysis | Live on Pi 3/4/5? | Offline (Mac) | Notes |
|---|---|---|---|
| Band FFT (current) | ✅ | ✅ | |
| Spectral flux / onset (numpy) | ✅ cheap **[ESTIMATE]** | ✅ | Same cost class as the current FFT |
| aubio onset/tempo | ✅ (C library) **[UNVERIFIED on Pi 5 / Py3.13]** | ✅ | GPL-3.0+; last PyPI release 0.4.9 (2019) |
| librosa beat/segmentation | ⚠️ batch only; numba on ARM is slow to JIT **[UNVERIFIED]** | ✅ | Fine as "precompute on the Pi before first play" |
| BeatNet online | ⚠️ claims real-time; Pi untested **[UNVERIFIED]** | ✅ | Depends on madmom (broken on Py ≥ 3.10 from PyPI) |
| beat_this (small) | ⚠️ maybe offline on a Pi 5 at a few × realtime **[UNVERIFIED]** | ✅ fast | torch dependency |
| all-in-one | ❌ | ✅ (GPU helps; 10 songs in 73 s on RTX 4090 per README) | NATTEN build is fragile on macOS **[UNVERIFIED]** |
| Demucs | ❌ (≈1.5× track length on a desktop CPU) | ✅ | Archived upstream |

**Implications.**
- Playback is from files, so **offline wins**: analyze once on the Mac (or overnight on the Pi), cache the result next to the song (like `.sync`), and play back deterministically.
- Live is needed only for `audio-in` / `stream-in` modes. There a numpy flux-and-beat follower with hysteresis would already beat the level threshold.
- **Latency:** offline sequences allow per-output **time offsets**. A Nanoleaf frame can be sent `latency_ms` early, and relay attack (mechanical ≈ 5–15 ms **[UNVERIFIED]**) can be pre-compensated. Live analysis can only add delay; `light_delay` exists for the opposite case (audio output delayed relative to the lights).
- **Nanoleaf at ≤ ~10 fps:** fast flicker is useless there. Composers should give it beat-level changes with smooth transitions, which matches beat-gated design.

---

## 7. Recommendation: phased roadmap

### (a) Phase 1: offline composer → existing `song.csv` (zero runtime changes)

**Step 0: prove the CSV path on the Pi first.** No test in `tests/` covers `sequence_type == 'csv'` (grep for `csv` and `sequence_type` finds nothing), so this legacy path is unproven in Neo, and phase 1 rests entirely on it. A 25 ms row rate also makes the playback loop, including `cm.load_state()` on every row, run about 4× as often as in FFT mode. That needs Pi validation and bears on open question 7. Smoke test with a hand-made alternating CSV, setting the column count to your `gpio_len`:

```bash
# Make a 60 s, 8-column test sequence that alternates odd/even channels every 0.5 s
python3 -c "
sr=44100; N=int(sr*25/1000); rows=int(60*sr/N)
for i in range(rows):
    odd=int(i*N/sr*2)%2
    print(','.join('255' if c%2==odd else '0' for c in range(8)))
" > music/test_song.csv     # test_song.mp3 (44.1 kHz) must sit beside it
# On the Pi
sudo $(which python) py/synchronized_lights.py --file music/test_song.mp3
```

Pass: a steady 0.5 s alternation that stays locked to the audio until the end, with no audio underruns.

Proposed CLI (not built yet):

```
python research/compose.py music/song.mp3 --strategy beat_chase \
    [--config config/overrides.cfg] [--channels N] [--pwm 0,0,1,1,...] \
    [--timing-ms 25] [--min-on 0.2] [--min-off 0.15] [--out music/song.csv] [--preview song.mp4]
```

- Read the channel count, PWM flags and LED mode from the same config the Pi uses, so the column count matches `gpio_len`.
- Analysis cache: `.song.mp3.features.npz` (beats, downbeats, onset envelopes, segments, stems RMS), keyed by a file hash plus analyzer versions.
- Output: integer values `0..brightness_range`, one row per `N = int(sr·timing/1000)` frames, no header, no blank lines. Use `round(v)` for relays (0 or 255).
- `--preview`: render the CSV plus audio to an MP4 (a row of circles per channel) on the Mac, so strategies can be reviewed without the Pi.
- The Mac-side dependencies live in a separate `requirements-research.txt`. The Pi never imports them.

### (b) Evaluation method

| Metric (objective proxy) | Definition | Good direction |
|---|---|---|
| **Beat alignment** | Fraction of relay ON-transitions within ±50 ms of a beat (or an F-measure, e.g. via `mir_eval.beat` **[UNVERIFIED: not researched; a short numpy version is enough]**) | ↑ |
| **Toggle rate** | Transitions per channel per minute; max and median | ↓ (relays), within a target band |
| **Min dwell violations** | Count of ON/OFF segments shorter than `min_on`/`min_off` | = 0 |
| **Duty cycle spread** | Per-channel ON fraction; the variance across channels | Not all ≈ the same (avoid "everything equally busy") |
| **Section contrast** | Activity difference between high- and low-energy sections | ↑ |
| **Silence darkness** | Mean brightness while RMS < −40 dBFS | ≈ 0 |
| **Inter-channel correlation** | Mean pairwise correlation | Moderate; ≈1 means everything flashes together |

**Subjective:** blind A/B sessions on the real display or on the MP4 preview. Use 6–10 songs across genres (fast pop, ballad, classical, rock). Rate "in sync", "not random", "variety", "would watch again" on a 1–5 scale. The FFT baseline is always one arm. Compute the proxies for the FFT baseline too, by thresholding its brightness at 0.5, so numbers are comparable.

### (c) Phase 2: runtime integration (later, needs Steve's design decision)

A pluggable pipeline inside `py/`, still free of any `api/` dependency:

```
Analyzer.analyze(audio_path) -> Features            # cached .features.npz; offline or Pi-batch
Composer.compose(features, layout) -> Timeline     # frames x channels (+ optional per-pixel RGB)
StreamingComposer.step(chunk) -> frame             # for audio-in / stream-in
OutputAdapter(relay | led | nanoleaf).send(frame, t) # per-output fps + latency offset
```

- A **"CSV v2"** with a header row (`#lspi-seq v2 timing_ms=… range=…`) and optional RGB columns per LED zone would unlock color, explicit timing and per-song settings. Keep v1 (headerless) fully backward compatible.
- `--strategy` could become a per-song setting in the playlist or per-song `.cfg`.
- Any change here touches show-night code and **needs validation on the Pi**.

### (d) Future ML work
LLM director (§5.3), then preference tuning, then a Skip-BART-style LED color model, then (only if licensing allows) learning from community sequences.

### First four prototype strategies (ordered)

All four end with the shared `relay_safety` pass:

```python
def relay_safety(on, min_on_rows, min_off_rows):           # on: bool[T] per relay channel
    out = on.copy(); last_change = -10**9; state = out[0]
    for t in range(1, T):
        want = on[t]
        dwell = t - last_change
        if want != state and dwell >= (min_on_rows if state else min_off_rows):
            state, last_change = want, t
        out[t] = state
    return out
# PWM/LED channels: attack/release envelope instead (e.g. instant attack, exp. decay tau≈150 ms)
```

**1. `beat_chase`: beat-gated chase with downbeat flashes.** *Effort: S (1–2 days).* Gives the biggest perceived "sync" gain per hour of work.
```python
beats, downbeats = beat_this(audio)  # or librosa.beat.beat_track (no downbeats → assume 4/4)
energy = smooth(rms(audio), 1.0 s);  level = rank_quantile(energy)   # 0..1, song-global
for each beat b_i (time t_i):
    k = n_active(level(t_i))         # 1..G channels lit, more when louder
    head = i % G                     # chase position (bounce on downbeat)
    light channels [head .. head+k) mod G from t_i until t_{i+1}
    leave an off-gap of max(min_off, 0.25*beat) before t_{i+1}, so held channels still pulse when k == G
    if b_i is downbeat and level > 0.8: all channels on for 1 beat (accent)
    if level < 0.1: all off (quiet = dark)
PWM channels: pulse = exp(-(t - t_i)/tau) * level
```

**2. `onset_bands`: event-based band triggering.** *Effort: S–M (2–3 days).* The current band→channel mental model, but driven by *hits*, not levels.
```python
S = log-mel or CQT spectrogram (hop ≈ 10 ms)
for channel c with band B_c:
    flux_c = sum(max(0, S[B_c, t] - S[B_c, t-1]))          # per-band spectral flux
    thr_c  = median_filter(flux_c, 0.5 s) + delta_c         # adaptive threshold
    peaks  = peak_pick(flux_c > thr_c, min_gap=min_off)
    optional: snap peaks to nearest ½-beat if within 40 ms
    relay: on for hold = clamp(0.5*beat_period, min_on, 1 beat)
    PWM/LED: envelope(peaks, strength=flux height)
density cap: at most K channels triggering per beat (keep the K strongest)
```

**3. `sections`: section-aware styles.** *Effort: M (3–5 days).* Answers the "everything looks the same" complaint.
```python
segments = laplacian_segmentation(audio) or allin1(audio)   # [(start, end, label)]
seg_energy = mean_loudness per segment -> rank 0..1
style_for_label = {}                       # same label -> same look (repeat-consistent)
menu = [sparse_pulse, alternating_AB, beat_chase, full_onset_bands, all_flash_downbeats]
for seg in segments:
    style = style_for_label.setdefault(seg.label, menu[pick_by(seg_energy)])
    render(style, seg, intensity=seg_energy)
transitions: blackout last ½-beat before high-energy section; 1-beat all-on at its downbeat
```

**4. `stems`: instrument-to-channel groups.** *Effort: M–L (4–7 days, mostly tuning).* Distinct parts drive distinct props.
```python
drums, bass, vocals, other = demucs(audio)           # htdemucs, offline on the Mac, cached
groups = layout config, e.g. {drums:[0..3], bass:[4], vocals:[5,6], other:[7]}
drums  -> onset_bands on the drums stem (kick=low, snare/hats=high) across its group as a chase
bass   -> PWM envelope of bass-stem RMS (relay: on while RMS > hysteresis band)
vocals -> on while vocal-activity (RMS > thr, hysteresis, min 1 beat); off in instrumental breaks
other  -> onset-triggered, gated to the beat grid
fallback: if a stem is near-silent for the whole song, its group joins 'other'
```

Later candidates include call-and-response between stems, chroma-driven LED palette (needs CSV v2 or the MAP1 hack) and live `onset_follow` for audio-in mode.

---

## 8. Dependencies and licensing

The project is BSD-licensed. Keep everything below **optional**, in research or extra requirements, and never vendored.

| Package | Code license | Model/weights | Status / Python | Pi feasibility | Use? |
|---|---|---|---|---|---|
| [librosa](https://github.com/librosa/librosa) | ISC | — | 1.0.1 (Nov 2026), Py ≥ 3.12 | batch OK **[UNVERIFIED speed]** | ✅ core |
| [beat_this](https://github.com/CPJKU/beat_this) | MIT | **MIT** ("code and the published model weights are released under the MIT license") | 1.1.0 (Apr 2026), torch ≥ 2 | small model maybe **[UNVERIFIED]** | ✅ beats and downbeats |
| [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm) | MIT | — | numpy/scipy | ✅ | ✅ |
| [MSAF](https://github.com/urinieto/msaf) | MIT | — | maintenance unclear **[UNVERIFIED]** | batch | optional |
| [Demucs](https://github.com/facebookresearch/demucs) | MIT | not stated separately **[UNVERIFIED]** | **archived Jan 2025**; [fork](https://github.com/adefossez/demucs) gets critical fixes only | ❌ | ✅ Mac only |
| [all-in-one](https://github.com/mir-aidj/all-in-one) | MIT | **[UNVERIFIED]** | depends on madmom (git) + NATTEN + Demucs | ❌ | optional, try later |
| [madmom](https://github.com/CPJKU/madmom) | BSD (code) | **CC BY-NC-SA 4.0** | PyPI 0.16.1 (2018) broken on Py ≥ 3.10 / NumPy ≥ 1.24; install from git | batch | avoid as a hard dependency |
| [BeatNet](https://github.com/mjhydri/BeatNet) | CC-BY-4.0 (repo) | **[UNVERIFIED]** | needs madmom | **[UNVERIFIED]** | maybe, for live |
| [aubio](https://github.com/aubio/aubio) | **GPL-3.0+** | — | last release 2019 | ✅ | optional live only, not bundled |
| [Essentia](https://essentia.upf.edu) | **AGPL-3.0** | **CC BY-NC-SA 4.0** | no Linux aarch64 wheel | build from source | avoid |
| [Spleeter](https://github.com/deezer/spleeter) | MIT | **[UNVERIFIED]** | **Python < 3.12**, TensorFlow | ❌ | no |

If shows are ever distributed commercially, avoid NC-licensed models (madmom, Essentia). For Steve's personal display NC is fine, but generated CSVs shipped *with the repo* would be derivative output **[not legal advice; flag for review]**.

---

## Open questions for Steve

1. **Layout:** how many relay channels, which are PWM, and are they mechanical relays or SSRs? That sets `min_on`/`min_off` defaults. What `led_channel_configuration` do you run (MIRROR, EXTEND or LEDONLY)?
2. **Where to compose:** is "analyze on the Mac, copy `song.csv` to the Pi" acceptable for phase 1, or must it run on the Pi (e.g. overnight batch)?
3. **Dependencies:** OK to add a Mac-only `requirements-research.txt` with torch (beat_this, Demucs)?
4. **Licensing:** is personal-use-only (CC BY-NC-SA) model output acceptable, or should we stay strictly permissive (librosa + beat_this + Demucs only)?
5. **Aesthetic target:** calmer and more rhythmic, or busier and more spectacular? Is there a reference show you like?
6. **Evaluation:** willing to do short blind A/B rating sessions? On the real display, or are MP4 previews good enough at first?
7. **CSV timing:** keep 25 ms rows, or move to 50 ms (relay-friendly)? Note `[custom_sequences]` is global today.
8. **Color:** is the zero-runtime MAP1 color hack worth trying, or go straight to designing CSV v2 with RGB?
9. **Nanoleaf:** expected timeline for the output plugin? It decides whether the latency and lookahead design matters in phase 3.
10. **LLM director:** OK to call a hosted LLM API from the offline composer (cost, network), or do you prefer local-only?
11. **Side findings:** should the `light_delay` 4× units bug (§1.2) and the hard-coded left-channel/stereo FFT assumption be filed as separate issues?
