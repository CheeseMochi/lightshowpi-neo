"""
`python -m research.lab ...`: the pattern lab's command line (the GUI uses the same code).

    lab serve [--port 8765] [--music music]     the browser GUI on this Mac
    lab build SONG... [bundle options]          = research/bundle.py (patterns, mixes, clips)
    lab patterns                                list patterns and mixes (built-in and yours)
    lab rate SONG VARIANT STARS [--note TEXT]   remember what you liked (1-5, 0 clears)
    lab ratings [SONG]
    lab pi login [--user admin]                 log in to the show Pi (token cached)
    lab pi layout [--save]                      the Pi's channel layout (saved for builds)
    lab pi songs                                the Pi's playlist and its variants
    lab pi push SONG [--variants a,b] [--as NAME] [--replace]
    lab pi play SONG --variant X                play it next with that variant, once
    lab pi set SONG --sequence "rotate:a,b"     the song's saved sequence on the Pi

The Pi URL comes from --pi or $LSP_PI (default http://piserver-neo.local:8000).
"""

import argparse
import getpass
import json
import os
import sys

from . import store
from .pi import DEFAULT_URL, NeedLogin, PiClient, PiError, pi_variant_name


def _client(args) -> PiClient:
    return PiClient(args.pi)


def cmd_build(args, rest):
    import bundle
    return bundle.main(rest)


def cmd_patterns(args, rest):
    from lspcompose import variants
    lib = variants.load_library()
    for name, spec in lib.patterns.items():
        opts = {k: v for k, v in spec.items() if k not in variants.META_KEYS}
        print(f"{name:22s} {lib.sources[name]:7s} {spec['base']:12s} {spec.get('title', '')}"
              + (f"  {opts}" if opts else ""))
    for name, spec in lib.mixes.items():
        groups = "; ".join(f"{g['channels']}: {g['pattern']}" for g in spec["groups"])
        print(f"{name:22s} {lib.sources[name]:7s} {'mix':12s} {spec.get('title', '')}  [{groups}]")
    return 0


def cmd_rate(args, rest):
    store.rate(args.song, args.variant, args.stars, args.note or "")
    print(f"{args.song} / {args.variant}: {args.stars} stars")
    return 0


def cmd_ratings(args, rest):
    for song, variants in store.ratings(args.song).items():
        print(song)
        for name, r in sorted(variants.items(), key=lambda kv: -kv[1]["stars"]):
            print(f"  {'★' * r['stars']:5s} {name}" + (f"  {r['note']}" if r.get("note") else ""))
    return 0


def cmd_pi(args, rest):
    client = _client(args)
    if args.pi_cmd == "login":
        password = os.environ.get("LSP_PI_PASSWORD") or getpass.getpass(f"{args.user}@{client.url} password: ")
        client.login(args.user, password)
        print(f"Logged in to {client.url}")
        return 0
    if args.pi_cmd == "layout":
        data = client.layout()
        print(json.dumps(data, indent=2))
        if args.save:
            with open(store.LAYOUT_FILE, "w") as f:
                json.dump(data, f, indent=2)
            print(f"Saved to {store.LAYOUT_FILE}: builds use it with --layout {store.LAYOUT_FILE}")
        return 0
    if args.pi_cmd == "songs":
        for s in client.songs():
            print(f"{s['index']:3d}  {s['name']:40s} sequence={s.get('sequence') or 'default'}  "
                  f"variants: {', '.join(s.get('variants') or []) or '-'}")
        return 0
    if args.pi_cmd == "push":
        variants = [v.strip() for v in args.variants.split(",")] if args.variants else None
        result = store.push(client, args.song, variants, args.as_song, args.replace)
        print(f"→ {result['song']['name']} (Pi song {result['song']['index']})")
        failed = 0
        for r in result["results"]:
            if r["ok"]:
                print(f"  ✓ {r['variant']}" + (f" (as {r['pi_name']})" if r["pi_name"] != r["variant"] else ""))
            else:
                failed += 1
                print(f"  ✗ {r['variant']}: {r.get('error')}"
                      + (" (add --replace to overwrite)" if r.get("status") == 409 else ""))
        return 1 if failed else 0
    target = store.resolve_pi_song(client, args.song, args.as_song)
    if args.pi_cmd == "play":
        client.play(target["index"], pi_variant_name(args.variant))
        print(f"Playing {target['name']} next with {args.variant} (once)")
        return 0
    if args.pi_cmd == "set":
        client.set_sequence(target["index"], args.sequence)
        print(f"{target['name']}: sequence = {args.sequence or '(default)'}")
        return 0
    return 2


def cmd_serve(args, rest):
    import uvicorn
    from .server import create_app
    app = create_app(music_dir=args.music, pi_url=args.pi)
    print(f"Pattern lab on http://127.0.0.1:{args.port}  (Ctrl-C stops)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="python -m research.lab", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pi", default=DEFAULT_URL, help="the show Pi's API URL")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="open the browser GUI")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--music", default=os.path.join(os.getcwd(), "music"),
                   help="folder with your songs (default ./music)")

    sub.add_parser("build", help="build bundles (all research/bundle.py options)", add_help=False)
    sub.add_parser("patterns", help="list patterns and mixes")

    r = sub.add_parser("rate", help="rate a variant 1-5 (0 clears)")
    r.add_argument("song")
    r.add_argument("variant")
    r.add_argument("stars", type=int)
    r.add_argument("--note")
    rs = sub.add_parser("ratings", help="show ratings")
    rs.add_argument("song", nargs="?")

    pi = sub.add_parser("pi", help="talk to the show Pi")
    pis = pi.add_subparsers(dest="pi_cmd", required=True)
    login = pis.add_parser("login")
    login.add_argument("--user", default="admin")
    lay = pis.add_parser("layout")
    lay.add_argument("--save", action="store_true", help=f"save to {store.LAYOUT_FILE}")
    pis.add_parser("songs")
    push = pis.add_parser("push")
    push.add_argument("song", help="bundle name (research/out/<song>)")
    push.add_argument("--variants", help="comma-separated (default: every variant in the bundle)")
    push.add_argument("--as", dest="as_song", help="the Pi's song name, if it differs")
    push.add_argument("--replace", action="store_true", help="overwrite variants already there")
    play = pis.add_parser("play")
    play.add_argument("song")
    play.add_argument("--variant", required=True)
    play.add_argument("--as", dest="as_song")
    st = pis.add_parser("set")
    st.add_argument("song")
    st.add_argument("--sequence", required=True, help="variant | rotate:a,b | random:a,b | fft | ''")
    st.add_argument("--as", dest="as_song")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    # `build` passes everything after it straight to research/bundle.py
    if "build" in argv[:3]:
        i = argv.index("build")
        args, rest = parser.parse_args(argv[:i + 1]), argv[i + 1:]
    else:
        args, rest = parser.parse_args(argv), []
    handlers = {"build": cmd_build, "patterns": cmd_patterns, "rate": cmd_rate,
                "ratings": cmd_ratings, "pi": cmd_pi, "serve": cmd_serve}
    try:
        return handlers[args.cmd](args, rest)
    except NeedLogin as e:
        print(f"! {e.detail}", file=sys.stderr)
        return 2
    except (PiError, FileNotFoundError, ValueError) as e:
        print(f"! {e}", file=sys.stderr)
        return 1
