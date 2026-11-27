"""
A small client for the show Pi's API (standard library only).

Logs in like the button manager (POST /api/auth/login, JWT), caches the token
per Pi URL in ~/.config/lightshowpi/lab-token.json (0600), and wraps the
endpoints the lab needs: songs, layout, variant upload/delete, one-shot
audition (play with a variant) and the saved sequence setting.
"""

import json
import os
import uuid
from typing import List, Optional
from urllib import error, parse, request

import sequence_select

DEFAULT_URL = os.environ.get("LSP_PI", "http://piserver-neo.local:8000")
TOKEN_FILE = os.environ.get("LSP_LAB_TOKEN_FILE",
                            os.path.expanduser("~/.config/lightshowpi/lab-token.json"))


class PiError(Exception):
    """An error response from the Pi (status code + the API's detail text)."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


class NeedLogin(PiError):
    """No token, or the Pi rejected it (expired, or the Pi's JWT key changed)."""


def pi_variant_name(variant: str) -> str:
    """The name a composer variant gets on the Pi (reserved words get a research_ prefix)."""
    return "research_" + variant if variant in sequence_select.RESERVED else variant


def _read_tokens(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class PiClient:
    """One show Pi, addressed by its API base URL."""

    def __init__(self, url: str = DEFAULT_URL, token_file: str = TOKEN_FILE, timeout: float = 30.0,
                 opener=None):
        self.url = url.rstrip("/")
        self.token_file = token_file
        self.timeout = timeout
        self._open = opener or request.urlopen

    # -- token ------------------------------------------------------------
    @property
    def token(self) -> Optional[str]:
        return _read_tokens(self.token_file).get(self.url)

    def _save_token(self, token: Optional[str]) -> None:
        tokens = _read_tokens(self.token_file)
        if token:
            tokens[self.url] = token
        else:
            tokens.pop(self.url, None)
        os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
        fd = os.open(self.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(tokens, f)

    def login(self, username: str, password: str) -> None:
        data = self._call("POST", "/api/auth/login", {"username": username, "password": password},
                          auth=False)
        self._save_token(data["access_token"])

    def logout(self) -> None:
        self._save_token(None)

    # -- transport --------------------------------------------------------
    def _call(self, method: str, path: str, body=None, auth: bool = True, raw: bytes = None,
              content_type: str = None, query: dict = None):
        url = self.url + path + ("?" + parse.urlencode(query) if query else "")
        headers = {"Accept": "application/json"}
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif content_type:
            headers["Content-Type"] = content_type
        if auth:
            token = self.token
            if not token:
                raise NeedLogin(401, f"not logged in to {self.url}: run `lab pi login`")
            headers["Authorization"] = f"Bearer {token}"
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._open(req, timeout=self.timeout) as resp:
                text = resp.read().decode() or "null"
                return json.loads(text) if text.strip() else None
        except error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except (ValueError, AttributeError):
                pass
            if e.code == 401 and auth:
                self._save_token(None)
                raise NeedLogin(401, f"{detail} (log in again: `lab pi login`)")
            raise PiError(e.code, str(detail))
        except error.URLError as e:
            raise PiError(0, f"can't reach {self.url}: {e.reason}")

    # -- endpoints ----------------------------------------------------------
    def health(self) -> dict:
        return self._call("GET", "/api/health", auth=False)

    def layout(self) -> dict:
        return self._call("GET", "/api/layout")

    def songs(self) -> List[dict]:
        return self._call("GET", "/api/songs").get("songs", [])

    def find_song(self, name: str, songs: Optional[List[dict]] = None) -> List[dict]:
        """Pi songs matching a local song: same audio file stem first, then the playlist name."""
        songs = self.songs() if songs is None else songs
        stem = os.path.splitext(os.path.basename(name))[0].lower()
        by_stem = [s for s in songs
                   if os.path.splitext(os.path.basename(s.get("path", "")))[0].lower() == stem]
        if by_stem:
            return by_stem
        return [s for s in songs if s.get("name", "").lower() == stem
                or stem in s.get("name", "").lower()]

    def variants(self, index: int) -> dict:
        return self._call("GET", f"/api/songs/{index}/variants")

    def upload(self, index: int, name: str, csv_bytes: bytes, replace: bool = False) -> dict:
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"{name}.csv\"\r\nContent-Type: text/csv\r\n\r\n").encode() \
            + csv_bytes + f"\r\n--{boundary}--\r\n".encode()
        return self._call("PUT", f"/api/songs/{index}/variants/{parse.quote(name)}", raw=body,
                          content_type=f"multipart/form-data; boundary={boundary}",
                          query={"replace": "true"} if replace else None)

    def delete_variant(self, index: int, name: str) -> dict:
        return self._call("DELETE", f"/api/songs/{index}/variants/{parse.quote(name)}")

    def play(self, index: int, variant: Optional[str] = None) -> dict:
        return self._call("POST", f"/api/songs/{index}/play",
                          {"variant": variant} if variant else None)

    def set_sequence(self, index: int, value: str) -> dict:
        return self._call("PUT", f"/api/songs/{index}/settings", {"sequence": value})

    def status(self) -> dict:
        return self._call("GET", "/api/lightshow/status")
