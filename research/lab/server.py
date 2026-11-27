"""
The pattern lab's local web GUI (FastAPI). Run with `python -m research.lab serve`.

Binds 127.0.0.1 only. Requests must name localhost in the Host header (so a
web page can't reach it through DNS rebinding) and writes must be JSON (so a
cross-site form can't post to it). Builds run one at a time in a background
thread; the page polls /api/jobs/<id>.
"""

import contextlib
import io
import os
import threading
import time
import traceback
import uuid
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import store
from .pi import NeedLogin, PiClient, PiError, pi_variant_name

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
BUNDLE_KEYS = ("song", "channels", "variants", "built_at", "relays", "clip_start_s",
               "clip_seconds", "tempo_bpm")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}


class BuildRequest(BaseModel):
    songs: List[str]                       # paths relative to the music folder
    variants: List[str] = []               # empty = all
    relays: str = "ssr"
    clip_start: Optional[float] = None
    clip_seconds: float = 30.0
    channels: Optional[int] = None         # default: saved layout, else 7
    order: str = ""
    no_stems: bool = False


class PatternBody(BaseModel):
    base: str
    title: str = ""
    description: str = ""
    options: Dict[str, object] = {}


class MixBody(BaseModel):
    title: str = ""
    description: str = ""
    groups: List[Dict[str, object]]


class MusicDirBody(BaseModel):
    path: str


class RateBody(BaseModel):
    song: str
    variant: str
    stars: int
    note: str = ""


class LoginBody(BaseModel):
    url: Optional[str] = None
    username: str = "admin"
    password: str


class PushBody(BaseModel):
    song: str
    variants: List[str] = []
    as_song: Optional[str] = None
    replace: bool = False


class PlayBody(BaseModel):
    song: str
    variant: Optional[str] = None
    as_song: Optional[str] = None


class SetBody(BaseModel):
    song: str
    sequence: str
    as_song: Optional[str] = None


class Jobs:
    """At most one build at a time; output captured for the page."""

    def __init__(self):
        self.jobs: Dict[str, dict] = {}
        self.lock = threading.Lock()

    def busy(self) -> bool:
        return any(j["state"] == "running" for j in self.jobs.values())

    def start(self, argv: List[str], runner) -> str:
        with self.lock:
            if self.busy():
                raise HTTPException(409, "a build is already running")
            job_id = uuid.uuid4().hex[:8]
            job = {"id": job_id, "state": "running", "log": "", "argv": argv,
                   "started": time.time(), "code": None}
            self.jobs[job_id] = job

        def run():
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    code = runner(argv)
                job["code"] = code
                job["state"] = "done" if code == 0 else "failed"
            except SystemExit as e:
                job["code"] = e.code
                job["state"] = "failed" if e.code else "done"
            except Exception:
                buf.write(traceback.format_exc())
                job["state"] = "failed"
            finally:
                job["log"] = buf.getvalue()
                job["finished"] = time.time()

        threading.Thread(target=run, daemon=True).start()
        return job_id


def _bundle_main(argv):
    import bundle
    return bundle.main(argv)


def create_app(music_dir: str, pi_url: str, out_dir: str = store.OUT_DIR,
               local_patterns: Optional[str] = None, ratings_file: str = store.RATINGS_FILE,
               layout_file: str = store.LAYOUT_FILE, token_file: Optional[str] = None,
               build_runner=_bundle_main, pi_opener=None,
               settings_file: str = store.SETTINGS_FILE) -> FastAPI:
    from lspcompose import strategies, variants

    local_patterns = local_patterns or variants.LOCAL_FILE
    app = FastAPI(title="Pattern lab", docs_url=None, redoc_url=None)
    jobs = Jobs()
    state = {"pi_url": pi_url, "music_dir": os.path.abspath(music_dir)}

    def client() -> PiClient:
        kw = {"opener": pi_opener} if pi_opener else {}
        if token_file:
            kw["token_file"] = token_file
        return PiClient(state["pi_url"], **kw)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "the lab only answers on localhost"}, 403)
        if request.method in ("POST", "PUT", "DELETE") and \
                "application/json" not in request.headers.get("content-type", ""):
            if request.method != "DELETE":
                return JSONResponse({"detail": "send JSON"}, 415)
        return await call_next(request)

    def pi_errors(fn):
        try:
            return fn()
        except NeedLogin as e:
            raise HTTPException(401, e.detail)
        except PiError as e:
            raise HTTPException(502 if e.status in (0, 500) else e.status, e.detail)

    # -- state ------------------------------------------------------------
    def library():
        try:
            return variants.load_library(variants.BUILTIN_FILE, local_patterns)
        except variants.PatternError as e:
            raise HTTPException(422, str(e))

    def layout_info():
        from lspcompose import layout as layout_mod
        if os.path.exists(layout_file):
            try:
                return layout_mod.load(layout_file).to_dict()
            except (ValueError, OSError) as e:
                return {"error": str(e)}
        return None

    @app.get("/api/state")
    def get_state():
        lib = library()
        bases = {b: {"types": {k: str(t) for k, t in variants.option_types(b).items()},
                     "defaults": variants.option_defaults(b), "needs_stems": variants.BASES[b][3]}
                 for b in variants.BASES}
        music = state["music_dir"]
        songs, truncated = store.scan_music(music) if os.path.isdir(music) else ([], False)
        settings = store.load_settings(settings_file)
        return {
            "music_dir": music,
            "music_dir_ok": os.path.isdir(music),
            "recent_music_dirs": settings.get("recent_music_dirs", []),
            "songs": songs,
            "songs_truncated": truncated,
            "songs_limit": store.MAX_SONGS,
            "bundles": {k: {kk: v[kk] for kk in BUNDLE_KEYS if kk in v}
                        for k, v in store.bundles(out_dir).items()},
            "patterns": [{"name": n, "source": lib.sources[n], **s} for n, s in lib.patterns.items()],
            "mixes": [{"name": n, "source": lib.sources[n], **s} for n, s in lib.mixes.items()],
            "bases": bases,
            "relay_presets": {k: list(v) for k, v in strategies.RELAY_PRESETS.items()},
            "chase_patterns": sorted(strategies.PATTERNS),
            "layout": layout_info(),
            "ratings": store.ratings(path=ratings_file),
            "pi": {"url": state["pi_url"], "logged_in": bool(client().token)},
            "busy": jobs.busy(),
        }

    # -- music folder -------------------------------------------------------
    @app.post("/api/music-dir")
    def post_music_dir(body: MusicDirBody):
        try:
            path = store.resolve_music_dir(body.path)
        except ValueError as e:
            raise HTTPException(404, str(e))
        state["music_dir"] = path
        store.remember_music_dir(path, settings_file)
        songs, truncated = store.scan_music(path)
        return {"music_dir": path, "songs": len(songs), "truncated": truncated}

    # -- builds -----------------------------------------------------------
    @app.post("/api/build")
    def post_build(body: BuildRequest):
        if not body.songs:
            raise HTTPException(422, "pick at least one song")
        root = state["music_dir"]
        argv = [os.path.normpath(os.path.join(root, s)) for s in body.songs]
        for path in argv:
            if os.path.commonpath([root, path]) != root:
                raise HTTPException(422, f"{path} is outside the music folder")
        for path in argv:
            if not os.path.isfile(path):
                raise HTTPException(404, f"no such song: {path}")
        if body.channels:
            argv += ["--channels", str(body.channels)]
        elif os.path.exists(layout_file):
            argv += ["--layout", layout_file]
        else:
            argv += ["--channels", "7"]
        if body.variants:
            argv += ["--variants", ",".join(body.variants)]
        argv += ["--relays", body.relays, "--clip-seconds", str(body.clip_seconds),
                 "--out-dir", out_dir, "--local-patterns", local_patterns]
        if body.clip_start is not None:
            argv += ["--clip-start", str(body.clip_start)]
        if body.order:
            argv += ["--order", body.order]
        if body.no_stems:
            argv += ["--no-stems"]
        return {"job": jobs.start(argv, build_runner)}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        if job_id not in jobs.jobs:
            raise HTTPException(404, "no such job")
        return jobs.jobs[job_id]

    # -- patterns and mixes -------------------------------------------------
    def _write(patterns, mixes):
        try:
            store.save_local(None, patterns, mixes, local_patterns)
        except variants.PatternError as e:
            raise HTTPException(422, str(e))

    @app.put("/api/patterns/{name}")
    def put_pattern(name: str, body: PatternBody):
        patterns, mixes = store.local_entries(local_patterns)
        spec = {"title": body.title or name, "description": body.description, "base": body.base}
        spec.update(body.options)
        if name in mixes:
            raise HTTPException(409, f"{name} is already a mix")
        patterns[name] = spec
        _write(patterns, mixes)
        return {"saved": name}

    @app.delete("/api/patterns/{name}")
    def delete_pattern(name: str):
        patterns, mixes = store.local_entries(local_patterns)
        if name not in patterns:
            raise HTTPException(404, f"{name} isn't one of your patterns (built-ins can't be deleted)")
        del patterns[name]
        _write(patterns, mixes)
        return {"deleted": name}

    @app.put("/api/mixes/{name}")
    def put_mix(name: str, body: MixBody):
        patterns, mixes = store.local_entries(local_patterns)
        if name in patterns or name in variants.load_library(variants.BUILTIN_FILE, None).patterns:
            raise HTTPException(409, f"{name} is already a pattern")
        spec = {"title": body.title or name}
        if body.description:
            spec["description"] = body.description
        spec["groups"] = [{k: v for k, v in g.items() if v not in (None, "")} for g in body.groups]
        mixes[name] = spec
        _write(patterns, mixes)
        return {"saved": name}

    @app.delete("/api/mixes/{name}")
    def delete_mix(name: str):
        patterns, mixes = store.local_entries(local_patterns)
        if name not in mixes:
            raise HTTPException(404, f"no mix {name}")
        del mixes[name]
        _write(patterns, mixes)
        return {"deleted": name}

    # -- ratings ----------------------------------------------------------
    @app.post("/api/rate")
    def post_rate(body: RateBody):
        try:
            return store.rate(body.song, body.variant, body.stars, body.note, path=ratings_file)
        except ValueError as e:
            raise HTTPException(422, str(e))

    # -- Pi -----------------------------------------------------------------
    @app.post("/api/pi/login")
    def pi_login(body: LoginBody):
        if body.url:
            state["pi_url"] = body.url.rstrip("/")
        pi_errors(lambda: client().login(body.username, body.password))
        return {"url": state["pi_url"], "logged_in": True}

    @app.post("/api/pi/logout")
    def pi_logout():
        client().logout()
        return {"logged_in": False}

    @app.get("/api/pi/songs")
    def pi_songs():
        return pi_errors(lambda: client().songs())

    @app.post("/api/pi/layout")
    def pi_layout():
        import json
        data = pi_errors(lambda: client().layout())
        with open(layout_file, "w") as f:
            json.dump(data, f, indent=2)
        return {"saved": layout_file, "layout": layout_info()}

    @app.post("/api/pi/push")
    def pi_push(body: PushBody):
        try:
            return pi_errors(lambda: store.push(client(), body.song, body.variants or None,
                                                body.as_song, body.replace, out_dir=out_dir))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/pi/play")
    def pi_play(body: PlayBody):
        def go():
            c = client()
            target = store.resolve_pi_song(c, body.song, body.as_song)
            c.play(target["index"], pi_variant_name(body.variant) if body.variant else None)
            return {"playing": target["name"], "variant": body.variant}
        return pi_errors(go)

    @app.post("/api/pi/set")
    def pi_set(body: SetBody):
        def go():
            c = client()
            target = store.resolve_pi_song(c, body.song, body.as_song)
            c.set_sequence(target["index"], body.sequence)
            return {"song": target["name"], "sequence": body.sequence}
        return pi_errors(go)

    # -- files ------------------------------------------------------------
    os.makedirs(out_dir, exist_ok=True)
    app.mount("/bundles", StaticFiles(directory=out_dir, follow_symlink=True), name="bundles")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    return app
