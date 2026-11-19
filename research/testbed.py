#!/usr/bin/env python3
"""
Test bed: play composed show variants on the real lights, one after another.

Run it on the show Pi over ssh, in the repo, with the show's conda python:

    cd ~/lightshowpi && source ~/miniforge3/bin/activate lightshowpi-neo
    python research/testbed.py                     # menu: pick a song, then variants
    python research/testbed.py run evilwezil_carol-of-the-bells --repeat 2   # no menu

Each variant plays through the normal show engine (py/synchronized_lights.py
--file), so light_delay, channel overrides, LED/Nanoleaf outputs and network
clients all behave as in the show. A generated config/testbed.cfg (your
overrides.cfg with the pre-show and postshow turned off) keeps the postshow
twinkle from running between variants.

The test bed stops the running show first. It does not restart it: run
bin/start_music_and_lights, or let cron start it at its next time.
Bundles come from research/bundle.py on the Mac (see research/README.md).
"""

import argparse
import configparser
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO, "research", "out")
TESTBED_CFG = "testbed.cfg"
LOG = os.path.join(REPO, "logs", "testbed.log")
SHOW_KEYS = ("preshow_configuration", "preshow_script", "postshow_configuration", "postshow_script")
# Per-channel overrides are reset too, so every relay shows every pattern (--keep-overrides keeps them)
OVERRIDE_KEYS = ("always_on_channels", "always_off_channels", "invert_channels")
# A run that ends before this share of the expected length failed, whatever the exit code
# (synchronized_lights exits 0 on a CSV column mismatch)
MIN_PLAYED_FRACTION = 0.5


class SetupError(Exception):
    """A problem the user must fix on the Pi (message says how)."""


# ---------------------------------------------------------------------------
# Bundles
# ---------------------------------------------------------------------------

def find_bundles(out_dir=OUT_DIR):
    """[(name, manifest)] for every research/out/<song>/manifest.json, sorted by name."""
    found = []
    if not os.path.isdir(out_dir):
        return found
    for name in sorted(os.listdir(out_dir)):
        path = os.path.join(out_dir, name, "manifest.json")
        if os.path.isfile(path):
            with open(path) as f:
                found.append((name, json.load(f)))
    return found


def variant_audio(out_dir, bundle, mode, variant):
    """Absolute path of the audio file whose CSV holds this variant."""
    return os.path.join(out_dir, bundle, mode, variant, f"{mode}.wav")


# ---------------------------------------------------------------------------
# Config checks and the test-bed config
# ---------------------------------------------------------------------------

def pi_layout(repo=REPO):
    """(gpio_len, timing_ms, brightness_range) exactly as the show will see them."""
    sys.path.insert(0, os.path.join(repo, "py"))
    os.environ.setdefault("SYNCHRONIZED_LIGHTS_HOME", repo)
    import importlib.util
    import configuration_manager
    cm = configuration_manager.Configuration()
    # Hardware only loads LED configs (which can add CSV columns) when led_module imports
    if importlib.util.find_spec("bibliopixel") is not None:
        for lc in cm.configs.led or []:
            cm.set_led(config_file=lc)
    cs = cm.custom_sequences
    return cm.hardware.gpio_len, int(cs.timing), int(cs.brightness_range)


def layout_problems(manifest, layout):
    """Why this bundle won't play correctly on this Pi (empty list = fine)."""
    gpio_len, timing_ms, brightness_range = layout
    problems = []
    if manifest["channels"] != gpio_len:
        problems.append(f"bundle has {manifest['channels']} channels, this Pi's gpio_len is "
                        f"{gpio_len}: rebuild it with --channels {gpio_len}")
    if manifest["timing_ms"] != timing_ms:
        problems.append(f"bundle rows are {manifest['timing_ms']} ms, [custom_sequences] timing "
                        f"is {timing_ms}")
    if manifest["brightness_range"] != brightness_range:
        problems.append(f"bundle brightness_range {manifest['brightness_range']}, config "
                        f"{brightness_range}")
    return problems


def write_testbed_config(repo=REPO, keep_overrides=False):
    """config/testbed.cfg = overrides.cfg with pre/postshow (and channel overrides) off."""
    src = os.path.join(repo, "config", "overrides.cfg")
    cfg = configparser.RawConfigParser(allow_no_value=True)
    cfg.optionxform = str
    if os.path.isfile(src):
        cfg.read(src)
    if not cfg.has_section("lightshow"):
        cfg.add_section("lightshow")
    for key in SHOW_KEYS:
        cfg.set("lightshow", key, "")
    if not keep_overrides:
        for key in OVERRIDE_KEYS:
            cfg.set("lightshow", key, "-1")
    path = os.path.join(repo, "config", TESTBED_CFG)
    try:
        with open(path, "w") as f:
            f.write("# Generated by research/testbed.py from overrides.cfg (pre/postshow off).\n"
                    "# Safe to delete; it is rewritten on every run.\n")
            cfg.write(f)
    except PermissionError:
        raise SetupError(f"Can't write {path} (owned by root?). Fix: sudo chown $USER {path}")
    return TESTBED_CFG


def cron_show_times():
    """'HH:MM' times root's crontab starts the show (best effort, may be empty)."""
    try:
        text = subprocess.run(["sudo", "-n", "crontab", "-l"], capture_output=True, text=True,
                              timeout=5).stdout
    except Exception:
        return []
    times = []
    for line in text.splitlines():
        parts = line.split()
        if line.strip().startswith("#") or len(parts) < 6 or "start_music_and_lights" not in line:
            continue
        if parts[0].isdigit() and parts[1].isdigit():
            times.append(f"{int(parts[1]):02d}:{int(parts[0]):02d}")
    return times


# ---------------------------------------------------------------------------
# Playing
# ---------------------------------------------------------------------------

def show_command(audio_path, python=sys.executable, repo=REPO, config=TESTBED_CFG, sudo=None):
    """The command that plays one file through the show engine."""
    if sudo is None:
        sudo = os.geteuid() != 0
    cmd = [python, os.path.join(repo, "py", "synchronized_lights.py"),
           f"--file={audio_path}", f"--config={config}"]
    return (["sudo", "-E"] + cmd) if sudo else cmd


def _stop(proc):
    """Stop a show child that ignored Ctrl-C (it runs as root under sudo)."""
    try:
        proc.terminate()
    except PermissionError:
        subprocess.run(["sudo", "kill", str(proc.pid)], check=False)
    try:
        proc.wait(timeout=10)
    except Exception:
        subprocess.run(["sudo", "kill", "-9", str(proc.pid)], check=False)


def open_log(log_path):
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        return open(log_path, "a")
    except PermissionError:
        raise SetupError(f"Can't write {log_path} (logs/ owned by root?). "
                         f"Fix: sudo chown -R $USER {os.path.dirname(log_path)}")


def play(audio_path, title, runner=subprocess.Popen, log_path=LOG, expected_s=None,
         clock=time.monotonic):
    """
    Play one variant; Ctrl-C stops it. Returns "done", "stopped" or "failed".
    The show's own output goes to logs/testbed.log; on a failure its tail is shown.
    A run much shorter than ``expected_s`` counts as failed even with exit code 0.
    """
    print(f"\n  ▶ {title}   (Ctrl-C stops)", flush=True)
    started = clock()
    with open_log(log_path) as log:
        log.write(f"\n===== {time.strftime('%F %T')} {title}: {audio_path}\n")
        log.flush()
        start = log.tell()
        env = dict(os.environ, SYNCHRONIZED_LIGHTS_HOME=REPO)
        proc = runner(show_command(audio_path), stdout=log, stderr=subprocess.STDOUT, env=env)
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            # The show got the same Ctrl-C (same process group) and turns the lights off
            try:
                proc.wait(timeout=10)
            except (KeyboardInterrupt, Exception):
                _stop(proc)
            print("  ■ stopped")
            return "stopped"
    elapsed = clock() - started
    too_short = expected_s is not None and elapsed < MIN_PLAYED_FRACTION * expected_s
    if code != 0 or too_short:
        with open(log_path) as f:
            f.seek(start)
            tail = f.read().strip().splitlines()[-15:]
        why = f"exited with {code}" if code != 0 else \
            f"ended after {elapsed:.1f} s of an expected {expected_s:.0f} s"
        print(f"  ✗ the show {why}. Last lines of {log_path}"
              " (and logs/music_and_lights.play.dbg):")
        print("\n".join("    " + line for line in tail) or "    (no output)")
        return "failed"
    return "done"


def expected_seconds(manifest, mode):
    """How long a variant should play in this mode, if the manifest says."""
    return manifest.get("clip_seconds") if mode == "clip" else manifest.get("duration_s")


def play_sequence(out_dir, bundle, manifest, mode, names, repeat=1, gap=2.0, play_fn=play):
    """Play variants back to back; Ctrl-C (a "stopped" result) ends the whole run."""
    titles = {v["name"]: v["title"] for v in manifest["variants"]}
    for round_ in range(repeat):
        for i, name in enumerate(names, 1):
            label = f"[{i}/{len(names)}{'' if repeat == 1 else f', round {round_ + 1}'}] {titles[name]}"
            result = play_fn(variant_audio(out_dir, bundle, mode, name), label,
                             expected_seconds(manifest, mode))
            if result in ("stopped", "failed"):
                return result
            try:
                time.sleep(gap)
            except KeyboardInterrupt:
                print("  ■ stopped")
                return "stopped"
    return "done"


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

def print_menu(bundle, manifest, mode):
    clip = f"{manifest['clip_start_s']:.0f}-{manifest['clip_start_s'] + manifest['clip_seconds']:.0f} s"
    print(f"\n━━ {bundle}  ·  {manifest['channels']} channels  ·  {manifest.get('tempo_bpm', 0):.0f} BPM"
          f"  ·  mode: {'30 s clip (' + clip + ')' if mode == 'clip' else 'full song'}")
    for i, v in enumerate(manifest["variants"], 1):
        print(f"  {i:2d}. {v['title']:28s} {v['description'][:80]}")
    print("\n  number = play it   a = all back to back   l = loop all until Ctrl-C\n"
          "  m = switch clip/full   i N = details of variant N   s = pick another song   q = quit")


def choose_bundle(bundles, input_fn=input):
    if len(bundles) == 1:
        return bundles[0]
    print("\nSongs:")
    for i, (name, man) in enumerate(bundles, 1):
        print(f"  {i}. {name}  ({len(man['variants'])} variants)")
    while True:
        answer = input_fn("song number> ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(bundles):
            return bundles[int(answer) - 1]
        if answer in ("q", "quit"):
            return None


def menu(out_dir, bundles, layout, input_fn=input, play_fn=play):
    picked = choose_bundle(bundles, input_fn)
    mode = "clip"
    while picked:
        bundle, manifest = picked
        problems = layout_problems(manifest, layout)
        if problems:
            print(f"\n{bundle} can't play on this Pi:")
            for problem in problems:
                print(f"  ! {problem}")
            others = [b for b in bundles if b[0] != bundle]
            picked = choose_bundle(others, input_fn) if others else None
            continue
        print_menu(bundle, manifest, mode)
        names = [v["name"] for v in manifest["variants"]]
        try:
            answer = input_fn("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if answer in ("q", "quit", "exit"):
            return
        if answer == "m":
            mode = "full" if mode == "clip" else "clip"
        elif answer == "s":
            picked = choose_bundle(bundles, input_fn)
        elif answer == "a":
            play_sequence(out_dir, bundle, manifest, mode, names, play_fn=play_fn)
        elif answer == "l":
            play_sequence(out_dir, bundle, manifest, mode, names, repeat=10 ** 6, play_fn=play_fn)
        elif answer.startswith("i ") and answer[2:].strip().isdigit():
            n = int(answer[2:])
            if 1 <= n <= len(names):
                v = manifest["variants"][n - 1]
                m = v.get("metrics", {})
                print(f"\n  {v['title']} ({v['name']})\n  {v['description']}")
                if v.get("labels"):
                    print("  " + "   ".join(f"ch{i + 1}: {x}" for i, x in enumerate(v["labels"])))
                if m:
                    print(f"  on beat {m.get('on_beat_fraction', 0):.0%}, "
                          f"{m.get('toggles_per_channel_min_median', 0):.0f} toggles/channel/min, "
                          f"duty {m.get('duty_mean', 0):.0%}")
        elif answer.isdigit() and 1 <= int(answer) <= len(names):
            v = manifest["variants"][int(answer) - 1]
            if v.get("labels"):
                print("  " + "   ".join(f"ch{i + 1}: {x}" for i, x in enumerate(v["labels"])))
            play_fn(variant_audio(out_dir, bundle, mode, v["name"]), v["title"],
                    expected_seconds(manifest, mode))
        else:
            print("  ?")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def prepare(skip_stop=False, keep_overrides=False):
    """sudo, stop the show, write the test-bed config, warn about cron."""
    if os.geteuid() != 0:
        subprocess.run(["sudo", "-v"], check=True)
    open_log(LOG).close()
    if not skip_stop:
        print("Stopping the running show (bin/stop_music_and_lights)...")
        # Our PID is the script's "don't kill" argument: it kills anything mentioning the repo
        subprocess.run([os.path.join(REPO, "bin", "stop_music_and_lights"), str(os.getpid())],
                       env=dict(os.environ, SYNCHRONIZED_LIGHTS_HOME=REPO),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    write_testbed_config(keep_overrides=keep_overrides)
    if not keep_overrides:
        print("Channel overrides (always_on/always_off/invert) are off for the test bed: "
              "every relay shows every pattern.")
    times = cron_show_times()
    if times:
        print(f"Note: cron starts the show at {', '.join(times)}; stop testing before then.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out-dir", default=OUT_DIR)
    p.add_argument("--no-stop", action="store_true", help="don't stop the running show first")
    p.add_argument("--keep-overrides", action="store_true",
                   help="keep always_on/always_off/invert_channels from overrides.cfg")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list", help="list bundles and variants")
    r = sub.add_parser("run", help="play variants back to back, no menu")
    r.add_argument("bundle")
    r.add_argument("--mode", choices=("clip", "full"), default="clip")
    r.add_argument("--variants", default="all", help="comma-separated names (default all)")
    r.add_argument("--repeat", type=int, default=1)
    args = p.parse_args(argv)

    bundles = find_bundles(args.out_dir)
    if not bundles:
        print(f"No bundles in {args.out_dir}. Build them on the Mac with research/bundle.py "
              "and rsync research/out/ here (research/README.md).")
        return 1

    if args.cmd == "list":
        for name, man in bundles:
            print(f"{name}: " + ", ".join(v["name"] for v in man["variants"]))
        return 0

    layout = pi_layout()
    if args.cmd == "run":
        matches = [b for b in bundles if b[0] == args.bundle]
        if not matches:
            print(f"No bundle {args.bundle!r}; have: {', '.join(b[0] for b in bundles)}")
            return 1
        bundle, manifest = matches[0]
        problems = layout_problems(manifest, layout)
        if problems:
            print("\n".join("! " + x for x in problems))
            return 1
        names = [v["name"] for v in manifest["variants"]] if args.variants == "all" else \
            [x.strip() for x in args.variants.split(",")]
        try:
            prepare(args.no_stop, args.keep_overrides)
            result = play_sequence(args.out_dir, bundle, manifest, args.mode, names, args.repeat)
        except SetupError as e:
            print(f"! {e}")
            return 1
        return 0 if result == "done" else 1

    try:
        prepare(args.no_stop, args.keep_overrides)
        menu(args.out_dir, bundles, layout)
    except SetupError as e:
        print(f"! {e}")
        return 1
    print("\nThe show is still stopped: bin/start_music_and_lights to restart it (or wait for cron).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
