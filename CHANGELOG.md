# Changelog

All notable changes to LightShowPi Neo will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `tools/sacn_monitor.py`: watches sACN packets arriving on a Pi (packets/s,
  sequence gaps, sender, levels) with a pass/fail mode, and
  `docs/bench/SACN_BENCH.md`, the Pi-to-Pi sACN bench procedure for the D9 go/no-go.
- **Nanoleaf output** (`py/outputs/nanoleaf.py`): streams per-LED colour to
  Nanoleaf Essentials (tested: NL73K3 Permanent Outdoor Lights, 60 LEDs) over
  UDP extControl v2. Configure with an `[output:<name>]` section
  (`type = nanoleaf`; see the example at the end of `defaults.cfg`). The strip
  is split into one segment per show channel; runs on its own thread and
  re-enables stream mode every 30 s so it recovers from power or Wi-Fi drops.
- `[output:<name>]` config sections for extra outputs (decision D5).
- **sACN (E1.31) Pi-to-Pi networking works**: `networking = sacn_server` /
  `sacn_client`. The server unicasts to every address in `sacn_address`
  (recommended on Wi-Fi), handles show frames, pre/post-show single-channel
  changes and overrides, and survives a client dropping off the network.
  Clients drop out-of-order packets (E1.31 sequence rules), ignore other
  universes, and assemble multi-universe frames. Default GPIO
  `universe_start` is now 100, clear of LED controllers on universe 1.
- **Hardware test mode**: `/api/test/*` endpoints (enter, exit, status, per-channel
  on/off with brightness, all on/off, test patterns) backed by a long-running
  `hardware_controller.py --serve` process. Test mode and the lightshow are
  exclusive: the show, the start endpoint and scheduled starts are refused while
  test mode is active. It exits automatically after
  `[api] test_mode_timeout_minutes` (default 10) without commands, turning all
  lights off.
- **GPIO chip selection**: `py/gpio_adapter.py` now picks the gpiochip that drives the
  40-pin header instead of always opening `gpiochip0`. Set `LSP_GPIOCHIP` (e.g. `4`,
  `gpiochip4` or `/dev/gpiochip4`) to force one; otherwise it auto-detects by label
  (`pinctrl-rp1` on Pi 5, `pinctrl-bcm2711` on Pi 4, `pinctrl-bcm2835` on Pi 3) and
  falls back to `gpiochip0`. Fixes Pi 5 on older kernels where the header is `gpiochip4`.
- **Hardware test bench**: `tests/test_gpio_hardware.py` is now a pytest suite of GPIO
  loopback tests (digital, `Channel` active_low/inverted, soft-PWM duty) with wiring in
  `tests/bench.yaml`. Tests marked `gpio`, `audio` or `platform` are skipped unless
  `LSP_BENCH=1`, so they can run unattended on a Pi runner (`LSP_BENCH=1 pytest -m gpio`).
- `[lightshow] light_delay_legacy_math` (default `True`). Set it to `False` to
  get `light_delay` in true seconds; see Fixed.
- `py/outputs/`: an `Output` interface (`LedOutput` for LED configs) and
  `ThreadedOutput`, which runs a slow or network output on its own thread
  (newest frame wins, fps cap, retry with back-off). Groundwork for Nanoleaf.
- LED configs take `threaded` (default `True`) and `max_fps` (default 30). LEDs
  are drawn and sent on their own thread, so a slow matrix render or an sACN
  controller dropping off Wi-Fi no longer stalls or crashes the show. Set
  `threaded = False` for the old in-loop behaviour.

- `make media HOST=... SRC=<backup> [OLD_PREFIX=<old install path>]`: copies
  `music/` and per-device configs (`overrides.cfg`, `led*.cfg`) from a local
  backup to a Pi and rewrites old absolute paths. A git-ignored
  `deploy/local/<host>/config/` overlay is applied last.
- Ansible `show_schedule` role: installs the show's cron schedule
  (`show_schedule_entries` in the inventory) in root's crontab on show hosts,
  with the conda env on PATH and output in `logs/cron.log`.
- `py/examples/random_pattern_ci.py`: random twinkle postshow.
- `lsp_enable_api` (Ansible, default true; false for the `client` group): client
  Pis no longer run the API, whose scheduler could start a second show there.
  `show_schedule` also runs on clients and accepts `special_time` (e.g. a
  client that starts at boot).

### Changed
- Test patterns in `hardware_controller.py` moved into a `Patterns` class so the
  CLI and serve mode share them.
- `py/gpio_adapter.py` no longer fails at import time when `lgpio` is missing; it
  raises a clear `RuntimeError` when GPIO is initialised instead.
- LEDs are driven through `hc.outputs`. Ambient mode, the exit blackout and
  scripts that call `hc.set_array` now light the LEDs too (they only moved GPIO
  before). With several LED configs, each one uses its own
  `led_channel_configuration`.
- The show loop re-reads `config/state.cfg` only when it changes (or once a
  second) instead of on every audio chunk.

### Security
- Removed the unused `python-jose` dependency (unfixed critical advisory; it also
  pulled in `ecdsa`, `rsa` and `pyasn1`, which had open advisories). Auth uses PyJWT.
- Raised minimum versions to releases with known advisories fixed: Pillow 13.0,
  PyJWT 2.15, certifi 2026.10.18 (removes compromised CA roots), Starlette
  0.49.1, Jinja2 3.1.6, python-multipart 0.0.18, requests 2.32.4, black 26.10.1.
- Web: Vite 6.4 (with esbuild 0.25), fixing dev-server file-access and
  cross-origin advisories; removed the unused `react-router-dom`. `npm audit`
  and an OSV scan of a fresh `requirements-dev.txt` install both report none.

### Fixed
- **The API played only one song per Start.** `LightshowManager` now loops a
  playlist start song after song (skip moves to the next song) until stop or
  pause, the scheduler stops it, test mode becomes active, or three runs in a
  row end within 5 s (state ERROR instead of spinning). Single-song and
  ambient starts are unchanged.
- **`bin/start_music_and_lights` played only one song.** `synchronized_lights.py`
  plays one song per run; the start script now loops it over the playlist
  until `bin/stop_music_and_lights`, as the original LightShowPi did.
- **`bin/stop_music_and_lights` could miss a running show**: it matched
  `$SYNCHRONIZED_LIGHTS_HOME` in the command line, which a relative
  `py/synchronized_lights.py` under the conda python lacks. It now stops the
  playlist loop first, then the show and button manager by script name.
- `bin/start_lightshow_client` warned on every start unless networking was
  `sacn_client`; it now accepts `client` too.
- `preshow_script`/`postshow_script` accept `$SYNCHRONIZED_LIGHTS_HOME` and log a
  warning when the file is missing (it was silently skipped).
- Network clients never lit their LEDs during the show (the server sends
  `BcastArray`, clients only handled raw arrays).
- Clients with fewer channels than the server crashed on pre/post-show pin
  messages (`IndexError`).
- Clients ignored ambient mode's keepalive broadcast.
- A client dropping off Wi-Fi could crash the server (`TypeError` while
  handling the socket error); a garbled packet crashed the client.
- `serverjson` failed on every show frame.
- A blank or malformed playlist line exited the show instead of being skipped.
- `audio-in` mode never updated the lights (full ALSA periods were discarded).
- `light_delay` was 4x too long for stereo files (2x for mono). The old maths
  stays the default via `light_delay_legacy_math = True` so tuned shows don't
  shift.
- Client override handling crashed (`Channel.inverted` called as a method).
- `RunningStats` used an undefined `length`; `APIConfig.is_server()` /
  `is_client()` read a missing setting; values in `defaults.cfg` with inline
  comments were parsed including the comment.

## [0.9.1] - 2026-09-23

### Added
- **Button API integration**: `api/routers/buttons.py` and `api/services/button_manager.py`
  expose skip / audio toggle / repeat / outlet controls over the REST API; the
  physical button manager (`py/buttonmanager.py`) can drive the show through the API.
- **Virtual buttons** in the web dashboard mirroring the physical buttons.
- **Ambient mode**: lights on steady with no music (`--mode ambient`), selectable
  per schedule and from the dashboard.
- **Schedule modes**: each schedule can start the show in `playlist`, `ambient`,
  `audio-in` or `stream-in` mode (new `schedule.mode` column, migrated automatically).
- **Experimental sACN networking** (`py/networking_sacn.py`) for server/client shows,
  with a JSON control plane for channel overrides.
- **Client/server scripts**: `bin/start_lightshow_server`, `bin/stop_lightshow_server`,
  `bin/start_lightshow_client`, `bin/stop_lightshow_client`, `bin/start_buttons.sh`,
  `bin/stop_buttons.sh`.
- **systemd units** for the API and button manager (`systemd/`), with setup docs in
  `systemd/README.md`. Units are not installed automatically by `install.sh`.
- **`environment-dev.yml`**: conda environment for macOS/Linux development (no GPIO/ALSA).
- **Live song status**: `/api/lightshow/status` reports the song title, elapsed and total
  time, mode, and the next scheduled event; `synchronized_lights.py` publishes
  `song_title`, `song_path`, `song_duration` and `song_started_at` to the state file.
- **Schedule suspend/resume**: stopping with `mode=stop` suspends all scheduled
  start/stop jobs (stored in the `settings` table) until the show is started again
  with `resume_schedule=true`. The dashboard has separate Pause and Stop buttons and
  shows the next event.
- `/api/health` reports real uptime, lightshow state and online client count.
- **Configurable button API credentials**: `[buttons] api_url`, `api_username`,
  `api_password`, overridable by `LIGHTSHOWPI_API_URL`, `LIGHTSHOWPI_API_USERNAME`,
  `LIGHTSHOWPI_API_PASSWORD`. The systemd unit reads `/etc/lightshowpi/buttons.env`.
  The button manager logs in again automatically after a 401 (expired token or API restart).
- API and button service tests (`tests/test_button_endpoints.py`,
  `tests/test_button_manager_service.py`).

### Changed
- Audio decoding switched from **soundfile** to **audioread**, supporting more file formats.
- `requirements.txt`, `requirements-dev.txt` and `environment.yml` trued up so the Pi
  and development installs match; docs updated accordingly.
- Configuration loading uses fallbacks for `[lightshow]` keys, so older override files
  no longer crash on missing options.
- Missing or unreadable playlist files now produce a clear error message.
- **Breaking**: the button manager no longer falls back to the hardcoded
  `admin`/`admin123` login. Configure a password or it runs in direct mode.
- The API's button service now reads `[buttons] enabled`, `audio_auto_shutoff` and
  `button_cooldown` instead of hardcoded values. Virtual buttons in the web UI require
  `[buttons] enabled = True`.
- Button endpoints moved from `/buttons/*` to `/api/buttons/*`.

### Fixed
- **State file lost updates**: `Configuration.update_state` now re-reads `state.cfg`
  under an exclusive lock before writing, so concurrent writers (show, button manager,
  API) no longer overwrite each other's keys. New `update_states()` writes several keys
  at once.
- Lightshow status reported the `now_playing` flag ("0"/"1") as the song name.
- `/api/lightshow/status` could fail with a 500 for the `starting`/`stopping` states
  (duplicate `LightshowState` enums unified; client status enum renamed `ClientState`).
- `restart` dropped the current mode (e.g. ambient).
- Button manager API mode never connected: it used port 5000 (API listens on 8000), and
  both it and the web UI called `/api/buttons/*` while the router was mounted at `/buttons`.
- `api/core/auth.py` used `sqlite3` without importing it (duplicate username crashed).
- Raspberry Pi 5 install issues (`install.sh`, `environment.yml`, `bin/link_lgpio.sh`).
- Button manager bug fixes and test fixes on macOS.
- `requests` is now installed as a dependency.

## [0.9.0] - 2025-12-14

First beta of **LightShowPi Neo**, a modernization fork of the original LightShowPi (v3.21)
for Python 3.13 and Raspberry Pi 3/4/5. See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the
full release notes.

### Added
- **FastAPI REST API** with JWT authentication, SQLite database, and lightshow
  start/stop/skip endpoints.
- **React web UI** with dashboard and schedule manager.
- **APScheduler-based scheduling** with per-day schedules and upcoming events.
- **Button manager** (`py/buttonmanager.py`) with `[buttons]` configuration: skip,
  audio toggle, repeat mode, outlet relay, cooldown and auto-shutoff timers.
- **pytest test suite** covering platform detection, GPIO adapter, configuration,
  FFT, audio decoding and the button manager, with macOS-friendly mocking.
- Documentation: README, CONTRIBUTING.md, FORK.md, LICENSE, GitHub issue/PR templates.

### Changed
- **GPIO**: lgpio via a compatibility adapter with WiringPi → BCM pin translation.
- **Audio processing**: CPU numpy FFT (GPU FFT removed); `audioop` replaced with numpy.
- **Audio decoding**: git-based decoder replaced with soundfile from PyPI
  (later replaced by audioread, see [Unreleased]).
- **Platform detection** simplified and fixed for newer Raspberry Pi OS cpuinfo formats.
- Default chunk size increased from 2048 to 4096 for Pi 3+.
- Dependencies updated for Python 3.13 (Pydantic v2, PyJWT).
- Configuration class no longer takes an SMS parameter.

### Removed
- **SMS control**: outdated, with security concerns.
- **Twitter integration**: API changes and limited usefulness.
- **FM broadcasting**: legal restrictions and liability concerns.
- **GPU FFT** and `rpi_audio_levels`: CPU FFT is sufficient on Pi 3+.
- **wiringPi**: deprecated, replaced by lgpio.
- **Git-based audio decoder**.
- **microweb** web interface: replaced by the React UI.
- **Raspberry Pi 1/2/Zero support**: insufficient performance for real-time FFT.

### Fixed
- Button manager: `Configuration(True)` SMS parameter bug, hardcoded home path,
  print statements replaced with logging.
- Configuration loader absolute path handling.
- Network cleanup race condition on shutdown.
- Playlist path environment variable expansion.
- JWT exception handling, schedule JSON parsing, frontend error display, API CORS/routing.

### Migration from original LightShowPi
- **Hardware**: Raspberry Pi 3 or newer (40-pin header) is required.
- **Installation**: run the new `install.sh`, or create the conda environment from
  `environment.yml`.
- **Configuration**: remove `[sms]`, Twitter and FM sections from `overrides.cfg`;
  add a `[buttons]` section if using the button manager and an `[api]` section for the API.
- **Verify**: run `pytest` before deploying and check the logs for deprecated features.

See [FORK.md](FORK.md) for a detailed comparison with the original LightShowPi.

---

## Original LightShowPi releases (2013-2021)

For the history of the original project this fork is based on, see the
[original LightShowPi repository](https://bitbucket.org/togiles/lightshowpi/).

[Unreleased]: https://github.com/CheeseMochi/lightshowpi-neo/compare/v0.9.1...HEAD
[0.9.1]: https://github.com/CheeseMochi/lightshowpi-neo/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/CheeseMochi/lightshowpi-neo/releases/tag/v0.9.0
