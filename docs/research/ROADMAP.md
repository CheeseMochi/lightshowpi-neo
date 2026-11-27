# Light pattern research: roadmap and experiments

**Status (2026-11-27):** the composed patterns work, and Steve's favourite is *hits per band*
(`onset_bands`) and its dynamic versions. The next phase is *iterating on the live display*:
tweak a pattern, hear and see it on the lights, keep what works. This page is the plan for that:
the loop, the experiments in priority order, and how to decide each one.

Background and the survey of techniques: [ALGORITHMS.md](ALGORITHMS.md).
Tools: [research/README.md](../../research/README.md) (the pattern lab).

## The iterate loop

1. **Tweak.** In the lab's *Patterns* tab, change an option (e.g. `drop_db`, `hold_beats`,
   `threshold`), or define a *mix* (a pattern per light source or channel group). Save it under
   a new name, so the old version stays to compare against.
2. **Build** the song with the old and new patterns (*Build* tab). Use the clip around the
   biggest drop, or a `--clip-start` you choose.
3. **Compare on the Mac** (*Compare & rate*): watch the preview, read the metrics table
   (on-beat %, switches per minute, relay violations, duty), and star each variant.
4. **Audition on the lights:** *Audition* plays the song next with that variant, once. Its
   saved setting stays put. Watch from the street, not the screen.
5. **Keep:** push the ★4+ variants and set the song to `rotate:` through them. Every play is
   logged with its variant, so `GET /api/analytics/songs?by_variant=true` shows what actually
   played.
6. **Write it down:** the rating notes (`research/ratings.jsonl`) are the experiment log. When
   a tweak wins across several songs, promote it into `research/patterns.toml` with a
   one-line reason in the commit.

**Decision rule for any tweak:**
- It replaces the old version when it wins on the lights for **3 of 4 test songs** (rating,
  street view). Use Wizards in Winter, Linus and Lucy, Mele Kalikimaka and Carol of the Bells.
- It must also not raise relay-limit violations above 0.
- The metrics support the call but don't make it.

## Experiments, in priority order

Each has a hypothesis, how to measure it, and what decides it.

### 1. Tune the dynamic hits on the real display
- **Hypothesis:** the defaults (`drop_db = 10`, `hold_beats = 0.5`, `threshold = 3`, SSR
  0.05 s limits) were set on a desk. The street view will want longer holds on the big props
  (roofline, trees) and shorter ones on small ones.
- **Do:** make `onset_dynamic` copies with `hold_beats` 0.5 / 0.75 / 1.0, and `drop_db`
  6 / 10 / 14. Rate them on the 4 test songs.
- **Measure:** ratings; switches per channel per minute; how long the drop cut stays dark
  (watch the Deck the Halls 44.8 s drop and Wizards in Winter's stops).
- **Decide:** the best combination becomes the new `onset_dynamic` default.

### 2. Prop-aware layout (letters and snowflake, sweep order)
- **Hypothesis:** patterns look intentional only when they know the props:
  - Channels 1 and 2 (0-based) light the same fence boards as **letters or snowflake, either-or**.
  - Sweeps need the physical end-to-end order.
- **Do:**
  - Give the sweep order once (`--order` / the Build tab), from one end of the yard to the
    other.
  - Add a "pair" rule to the composer so letter/snowflake never light together (or alternate
    by section).
  - Still needed from Steve: what channel 6 drives.
- **Decide:** sweeps read as a sweep from the street; no frame lights both 1 and 2.

### 3. One pattern per light source (Nanoleaf vs relays)
- **Hypothesis:** the relays carry the rhythm; Nanoleaf works better as a slower colour layer.
- **Do:**
  - Turn on `virtual_channels = 6` and map `[output:nanoleaf] channels = 7,…,12`.
  - Build mixes: relays on `onset_dynamic` plus Nanoleaf on `test_alternate`, `onset_smooth`
    or `beat_chase_calm`.
- **Measure:** ratings. Also check that Nanoleaf stays under about 10 updates per second, the
  rate where its Wi-Fi stream hitched.
- **Decide:** keep the mix that rates best across the 4 songs as the default for Nanoleaf.

### 4. Vocals and a cappella (Pentatonix)
- **Hypothesis:** `stems` can put voices on lights. Demucs gives one vocals stem; the low/high
  split approximates the bass singer against the upper voices.
- **Do:** build one Pentatonix track with `stems` and a mix that puts `stems` on a few lights
  and `onset_dynamic` on the rest.
- **Measure:** on the preview, does the "low voice" light follow the bass singer? Count
  phrases where it's wrong.
- **Decide:** if more than about 70% of phrases look right, keep it as a per-song choice.
  Otherwise try a per-singer approach (pitch tracking in the vocals stem), a bigger project.

### 5. Beat grid quality (beat_this)
- **Hypothesis:** librosa's beat tracker is loose on these arrangements. About 70% of kick
  hits sit more than 60 ms off its grid. That hurts beat chases and the hit snapping.
- **Do:** add `beat_this` (MIT, code and weights; torch is already there for Demucs) as an
  optional analyser, then compare the grids on the 4 songs.
- **Measure:** the share of kick-stem hits within ±50 ms of a beat, under each tracker.
- **Decide:** switch the default when the on-grid share rises by 15 points or more.

### 6. Sections, rebuilt on dynamics
- **Hypothesis:** `sections` was hard to read because its looks (sparse, A/B, chase, pulse)
  ignore the dynamics. Built on `onset_dynamic` with a different *character* per section
  (hold length, density, sweep on or off), a repeated chorus would look like itself.
- **Do:** make `sections` choose pattern options per section label, not fixed patterns.
- **Decide:** Steve can name what changed at each section boundary in a full-song preview.

### 7. Measure the 2005 Wizards in Winter video
- **Hypothesis:** the reference show's sweeps, stops and density can become numeric targets.
- **Do:** with a local `.mp4`, track each light group's brightness per frame (opencv), then
  compute sweep speed, the time from a stop to dark, and lights per moment.
- **Decide:** tune `onset_runs` until our Wizards in Winter is within about 25% of those
  numbers.

### 8. Switching variants on the fly
- **Done already:** per-song `rotate:` and `random:`, and the one-shot audition.
- **Next:**
  - switch variant at section boundaries mid-song (needs show code)
  - triggers: time of night, a button, crowd votes
- **Decide:** only after experiments 1–3 settle the keepers. Parked until then (see the
  multi-variant idea).

### 9. Colour for Nanoleaf and LEDs
- **Hypothesis:** colour from harmony (chroma and key) adds mood without more motion.
- **Do:** a CSV v2 with RGB columns per LED zone (runtime change), or try the zero-change
  MAP1 trick first.
- **Decide:** only after Nanoleaf gets its own pattern (experiment 3).

### 10. Learn from the ratings
- **Hypothesis:** once there are about 50 ratings, simple preference tuning can propose
  option values to try next (`ratings.jsonl` holds each variant's recipe through the bundle
  manifest).
- **Decide:** worth it only if the hand tuning in experiment 1 plateaus.

### 11. Live audio (audio-in and stream-in)
- **Hypothesis:** the winning offline ideas (hits per band, drop cut) can run live in numpy on
  the Pi for radio or stream modes.
- **Decide:** after the season; these modes aren't in this year's show.

## Rules that stay fixed
- Composing stays on the Mac. The Pi only plays CSVs, and an unusable CSV falls back to the
  legacy CSV, then FFT.
- Every pattern respects the relay preset (`LSP_RELAYS=ssr` for Steve's display).
- New show-code features are off by default and switchable in config: `allow_sequence_audition`,
  `virtual_channels`.
