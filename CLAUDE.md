# CLAUDE.md

Guidance for Claude Code working in **LightShowPi Neo**, a modernized fork of LightShowPi that syncs Christmas lights to music on a Raspberry Pi 3/4/5. See [AGENTS.md](AGENTS.md) for the AI-use and attribution policy, [ROADMAP.md](ROADMAP.md) for priorities, and [FORK.md](FORK.md) for the history of the fork.

## Working agreement

The workflow from AGENTS.md applies:

- **Steve directs the work and checks it on the hardware.** Architecture, scope, config design and roadmap priorities are his decisions. Propose changes and ask; don't decide these on your own.
- **Claude implements, tests and debugs.** Write the code and its tests, then iterate on the error output Steve sends back from the Pi.
- **Only the Pi can confirm hardware behavior.** GPIO, PWM, ALSA audio, LEDs and real timing can't be tested on the Mac. When a change touches these, say so plainly and give Steve the exact command to run on the Pi.
- Run the test suite and show the verify command before suggesting a commit. Commit only when asked. Don't commit directly to `main` when there is uncommitted WIP that belongs to someone else.

## Repository layout

| Path | What it is |
|------|------------|
| `py/` | Core lightshow engine (legacy-derived Python). Its modules import each other by bare name (`import configuration_manager`). |
| `py/synchronized_lights.py` | Main show process: decodes audio, runs the FFT, drives the channels. |
| `py/hardware_controller.py` | Hardware abstraction (`Hardware`, `Channel`), the `Patterns` test patterns, and the CLI (`--state`, `--test`, `--serve`). |
| `py/gpio_adapter.py`, `py/wiring_pi.py` | lgpio backend plus the WiringPi→BCM pin translation layer. |
| `py/configuration_manager.py` | Loads `config/defaults.cfg` with `overrides.cfg` layered on top. |
| `py/buttonmanager.py` | Physical button daemon. Runs in `api`, `direct` (state file) or `auto` mode. |
| `py/led_module.py`, `led_color_maps.py`, `driver_sacn.py`, `networking*.py` | LED and sACN/E1.31 output, plus server/client networking. |
| `api/` | FastAPI backend (optional). `core/` holds config, auth (JWT + bcrypt) and SQLite; `routers/` holds the endpoints; `services/` holds the managers. |
| `api/services/lightshow_manager.py` | Runs `synchronized_lights.py` as a **subprocess**, which it starts, stops and monitors. |
| `api/services/scheduler.py` | APScheduler start/stop jobs with suspend/resume, persisted in SQLite. |
| `web/` | React 18 + Vite frontend. The dev server runs on :3000 and proxies `/api` to :8000. |
| `config/` | `defaults.cfg` (checked in), `overrides.cfg` (per-Pi, gitignored), `state.cfg` (runtime state), and `contrib/` example overrides. |
| `systemd/` | Unit files for the API and the button manager. Paths assume `/home/pi/lightshowpi` and a miniconda env. |
| `bin/` | Start/stop helper scripts (`start_api.sh`, `start_frontend.sh`, `link_lgpio.sh`, …). |
| `tests/` | pytest suite. `conftest.py` and `fixtures/` (including `test_tone.wav`). |

## Architecture notes

- **The API is optional.** The show still runs standalone from the CLI, cron and buttons. Don't make the core in `py/` depend on `api/`.
- **The API controls the show from outside.** `LightshowManager` launches `synchronized_lights.py` as a subprocess and reads its output. Show and API state are shared through `config/state.cfg`. Earlier commits fixed lost updates on that file, so keep writes to it atomic.
- **Imports:** pytest sets `pythonpath = py`, and `api/core/config.py` adds `py/` to `sys.path`. Import `py/` modules by bare name, never as `py.module`.
- **Config:** to add a setting, document the key in `config/defaults.cfg` and read it with an `APIConfig._get_*` getter that has a sane fallback. Never require users to edit `defaults.cfg`.
- **Auth:** the first API start creates `admin` with the password from `$ADMIN_PASSWORD`, falling back to `admin123`. The button manager reads its API credentials from `/etc/lightshowpi/buttons.env`.
- **Frontend serving:** `web/` currently runs only through the Vite dev server (`bin/start_frontend.sh`). FastAPI imports `StaticFiles` but does not mount a built `web/dist`.
- **Pins:** configs use BCM numbering. Legacy WiringPi numbers are translated in `wiring_pi.py`.

## Commands

All commands run from the repo root. `SYNCHRONIZED_LIGHTS_HOME` must point to the repo root.

```bash
# Dev environment (Mac)
conda activate lightshowpi-neo-dev        # created from environment-dev.yml
export SYNCHRONIZED_LIGHTS_HOME=$(pwd)

# Tests (verified on macOS: lgpio and ALSA tests skip)
pytest -q
pytest tests/test_lightshow_endpoints.py -q    # a single file
pytest -m "not gpio and not audio and not platform"
pytest --cov=py --cov=api --cov-report=term-missing

# Without activating the env
SYNCHRONIZED_LIGHTS_HOME=$(pwd) conda run -n lightshowpi-neo-dev pytest -q
```

The following commands have not been run in a Claude session. Treat them as unverified.

```bash
python -m api.main                         # API on :8000, docs at /docs
cd web && npm install && npm run dev       # frontend on :3000
cd web && npm run build                    # production bundle to web/dist
black py/ api/ && flake8 py/ api/          # formatting and lint (in requirements-dev)

# On the Pi (conda env "lightshowpi-neo", needs sudo for GPIO)
sudo $(which python) py/synchronized_lights.py
sudo $(which python) py/hardware_controller.py --state flash
./bin/start_api.sh
```

## Testing conventions

- Use pytest markers from `pytest.ini`: `unit`, `integration`, `platform`, `gpio`, `audio`, `slow`. `--strict-markers` is on, so register any new marker there first.
- Mock hardware with `unittest.mock.patch` on `gpio_adapter.lgpio` and similar. Tests that need a real Pi must `skip` cleanly elsewhere.
- API tests use FastAPI's `TestClient` and override dependencies. Follow the patterns in `tests/test_lightshow_endpoints.py` and `tests/test_button_endpoints.py`.
- Give production classes whose names start with `Test` the attribute `__test__ = False` so pytest doesn't try to collect them (see `TestModeManager`).
- Every new endpoint, service or bug fix ships with tests.
- No CI exists yet: `.github/` holds only issue and PR templates. Run the suite locally.

## Code style

**Existing conventions are not sacred.** When a pattern makes the repo harder to change, update or extend, improve it. Examples: module globals, `sys.path` hacks, duplicated logic, magic numbers, untestable code. Keep refactors in commits separate from feature work, cover them with tests, and say what changed and why. For `py/` code that runs on show nights, flag refactors that need validation on the Pi. Unless there's a reason to depart, use these defaults:

- PEP 8, 4 spaces, docstrings on public functions and classes, `logging.getLogger(__name__)` instead of `print`. The exception is CLI user output in `py/` scripts.
- `py/` is legacy-derived code that runs live on show nights. Refactor it freely when that pays off, but keep the CLI and config behaviour backward-compatible unless Steve agrees to a break.
- The frontend is plain React (JSX) with per-component CSS in `web/src/styles/`. API calls go through `web/src/services/api.js`.

## Commits

- Subjects are plain sentences in the present participle that summarize the change. Examples: "Wiring up live song status, schedule suspend/resume and health, and fixing state file lost updates", "Release v0.9.1 Beta 2". The `feat:`/`fix:` prefixes in CONTRIBUTING.md are not used in practice.
- AI-assisted commits end with a line naming the agent, not a model, e.g. `🤖 Generated with Claude Code` (or whichever agent did the work). Don't name specific models such as Opus or Sonnet.
- Update `CHANGELOG.md` and `RELEASE_NOTES.md` for user-visible changes. Releases are tagged `vX.Y.Z Beta N`.

## Gotchas

- Doc claims can be stale. "76+ tests" undercounts: the suite now has 200+. README version strings may lag the latest release commit.
- `config/overrides.cfg`, `api/data/*.db`, `music/` media and `.env` are gitignored and per-device. Never commit them.
- The systemd units hardcode `/home/pi/...` paths and the `pi` user. Check them against the target Pi before deploying.
- The API listens on `0.0.0.0` with CORS `*`. Treat it as LAN-only until auth and CORS are hardened.
