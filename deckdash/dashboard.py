"""Dashboard: a page on 127.0.0.1 that shows the deck and drives it, opened as its own window.

The server is a stdlib ``ThreadingHTTPServer`` on a daemon thread, bound to loopback only - any
local process can reach it and there is no auth, so every command still goes through
``control.validate`` and the settings form writes only an allow-list of tunables. Commands reach
the render loop the same way ``ctl`` and the tray do, through ``App.submit``, so deck writes stay
on the one thread. The preview composes ``deck._shown`` on the request thread behind a short cache;
doing that on the loop thread would show up in the flush budget.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config
from .ambient import SCENES
from .canvas import Canvas
from .control import open_window, restart_script, run_powershell, validate
from .tiles import TILES

log = logging.getLogger(__name__)

DEFAULT_PORT = 8770
FRAME_CACHE_S = 0.2  # the page asks at 5 fps; one compose+encode per refresh, not per client
SUBMIT_TIMEOUT_S = 3.0
MAX_BODY = 256 * 1024
UI_DIR = Path(__file__).resolve().parent / "ui"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Config paths the settings form owns. Everything else in config.local.toml - the weather
# coordinates, the Bitaxe address, the VPS block, the calibrated gap - is never read out to the
# page and never written back, so a save cannot leak or lose it.
TUNABLES = {
    "deck": ["idle_minutes", "brightness", "night_brightness", "night_start", "night_end", "slow_step_ms"],
    "ambient": ["scenes", "scene_minutes", "fps"],
    "layout": ["keys", "overlays"],
    "news": ["feeds"],
    "ci": ["repos"],
}
# Sources snapshot these in __init__ and App.reload deliberately leaves the sources running, so a
# change here does nothing until the process restarts.
RESTART_KEYS = [("news", "feeds"), ("ci", "repos")]


def url_for(cfg: dict) -> str:
    return f"http://127.0.0.1:{port_for(cfg)}/"


def port_for(cfg: dict) -> int:
    return int(cfg.get("ui", {}).get("dashboard_port", DEFAULT_PORT))


# --- validation -------------------------------------------------------------------------------

def _clean_str(value, field: str) -> str:
    """A one-line string. Control characters would be emitted raw and make the file unparsable."""
    if not isinstance(value, str):
        raise ValueError(f"{field}: expected text")
    if any(c < " " or c == "\x7f" for c in value):
        raise ValueError(f"{field}: no line breaks or control characters")
    return value.strip()


def _clean_int(value, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field}: expected a number")
    if value != value or value in (float("inf"), float("-inf")):  # NaN/inf survive a TOML round trip
        raise ValueError(f"{field}: not a number")
    n = int(value)
    if not low <= n <= high:
        raise ValueError(f"{field}: must be {low}-{high}")
    return n


def _clean_hhmm(value, field: str) -> str:
    """``_minutes`` splits on ':' and int()s both halves, and it runs during reload."""
    text = _clean_str(value, field)
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"{field}: must be HH:MM")
    if not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
        raise ValueError(f"{field}: must be a real time of day")
    return f"{int(parts[0]):02d}:{int(parts[1]):02d}"


def validate_settings(posted: dict) -> dict:
    """Turn the form's JSON into an update for ``config.write_local``, or raise ValueError.

    Everything is checked here rather than left to the reload: a value that reaches
    config.local.toml is read again at every start, so a bad one outlives the process."""
    if not isinstance(posted, dict):
        raise ValueError("expected an object")
    update: dict = {}

    deck = posted.get("deck") or {}
    if deck:
        out = {}
        if "idle_minutes" in deck:
            out["idle_minutes"] = _clean_int(deck["idle_minutes"], "idle minutes", 0, 1440)
        if "brightness" in deck:
            out["brightness"] = _clean_int(deck["brightness"], "brightness", 0, 100)
        if "night_brightness" in deck:
            out["night_brightness"] = _clean_int(deck["night_brightness"], "night brightness", 0, 100)
        if "night_start" in deck:
            out["night_start"] = _clean_hhmm(deck["night_start"], "night start")
        if "night_end" in deck:
            out["night_end"] = _clean_hhmm(deck["night_end"], "night end")
        if "slow_step_ms" in deck:
            out["slow_step_ms"] = _clean_int(deck["slow_step_ms"], "slow step ms", 1, 10000)
        update["deck"] = out

    amb = posted.get("ambient") or {}
    if amb:
        out = {}
        if "scenes" in amb:
            scenes = amb["scenes"]
            if not isinstance(scenes, list):
                raise ValueError("scenes: expected a list")
            for s in scenes:
                if s not in SCENES:
                    raise ValueError(f"unknown scene '{s}' (known: {', '.join(SCENES)})")
            if len(set(scenes)) != len(scenes):
                raise ValueError("scenes: no duplicates")
            out["scenes"] = list(scenes)
        if "scene_minutes" in amb:
            out["scene_minutes"] = _clean_int(amb["scene_minutes"], "scene minutes", 0, 1440)
        if "fps" in amb:
            out["fps"] = _clean_int(amb["fps"], "ambient fps", 1, 30)
        update["ambient"] = out

    layout = posted.get("layout") or {}
    if layout:
        out = {}
        if "keys" in layout:
            keys = layout["keys"]
            if not isinstance(keys, list) or len(keys) != 15:
                raise ValueError("layout: expected 15 keys")
            for name in keys:
                if name != "" and name not in TILES:
                    raise ValueError(f"unknown tile '{name}' (known: {', '.join(TILES)})")
            out["keys"] = [str(k) for k in keys]
        if "overlays" in layout:
            overlays = layout["overlays"]
            if not isinstance(overlays, dict):
                raise ValueError("overlays: expected an object")
            for base, top in overlays.items():
                if base not in TILES:
                    raise ValueError(f"overlay base '{base}' is not a tile")
                if top != "" and top not in TILES:
                    raise ValueError(f"overlay '{base}': unknown tile '{top}'")
            out["overlays"] = {str(b): str(t) for b, t in overlays.items()}
        update["layout"] = out

    if "feeds" in (posted.get("news") or {}):
        feeds = posted["news"]["feeds"]
        if not isinstance(feeds, list):
            raise ValueError("feeds: expected a list")
        clean = []
        for i, feed in enumerate(feeds):
            if not isinstance(feed, dict):
                raise ValueError(f"feed {i + 1}: expected an object")
            url = _clean_str(feed.get("url", ""), f"feed {i + 1} url")
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"feed {i + 1}: url must start with http:// or https://")
            clean.append({"name": _clean_str(feed.get("name", ""), f"feed {i + 1} name") or "news",
                          "url": url,
                          "color": _clean_str(feed.get("color", "#888888"), f"feed {i + 1} colour")})
        update["news"] = {"feeds": clean}

    if "repos" in (posted.get("ci") or {}):
        repos = posted["ci"]["repos"]
        if not isinstance(repos, list):
            raise ValueError("repos: expected a list")
        clean = []
        for i, repo in enumerate(repos):
            if not isinstance(repo, dict):
                raise ValueError(f"repo {i + 1}: expected an object")
            name = _clean_str(repo.get("repo", ""), f"repo {i + 1}")
            if name.count("/") != 1 or not all(name.split("/")):
                raise ValueError(f"repo {i + 1}: must be owner/name")
            clean.append({"repo": name,
                          "label": _clean_str(repo.get("label", ""), f"repo {i + 1} label") or name.split("/")[1]})
        update["ci"] = {"repos": clean}

    return {k: v for k, v in update.items() if v}


def effective(cfg: dict) -> dict:
    """Just the tunables the form owns, read from the config the app is actually running."""
    out: dict = {}
    for section, keys in TUNABLES.items():
        have = cfg.get(section, {})
        out[section] = {k: have[k] for k in keys if k in have}
    out["tiles"] = list(TILES)
    out["all_scenes"] = list(SCENES)
    return out


def _restart_required(before: dict, after: dict) -> bool:
    for section, key in RESTART_KEYS:
        if before.get(section, {}).get(key) != after.get(section, {}).get(key):
            return True
    return False


# --- server -----------------------------------------------------------------------------------

class _Server(ThreadingHTTPServer):
    # HTTPServer sets allow_reuse_address, and on Windows that lets a second process bind a port
    # another one is already serving: two copies would then split the requests between them.
    allow_reuse_address = False
    daemon_threads = True


class Dashboard(threading.Thread):
    """Serves the page and its four endpoints; ``ready`` is set once the port is bound or refused."""

    def __init__(self, app, port: int = DEFAULT_PORT):
        super().__init__(name="dashboard", daemon=True)
        self.app = app
        self.port = port
        self._set_port(port)
        self.ready = threading.Event()
        self.error: str | None = None
        self._server: ThreadingHTTPServer | None = None
        self._frame: tuple[float, bytes] | None = None
        self._frame_lock = threading.Lock()
        self._write_lock = threading.Lock()  # config.write_local is read-modify-write

    def _set_port(self, port: int) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}/"
        self.origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def run(self) -> None:
        handler = _handler_for(self)
        try:
            self._server = _Server(("127.0.0.1", self.port), handler)
        except OSError as exc:  # a second copy, a stale window: the deck must still come up
            self.error = str(exc)
            log.warning("dashboard port %d unavailable: %s", self.port, exc)
            self.ready.set()
            return
        self._set_port(self._server.server_address[1])  # port 0 asks the OS to pick one (tests)
        log.info("dashboard %s", self.url)
        self.ready.set()
        try:
            self.frame_png()  # load PIL's PNG writer now: doing it on the first request cost the
        except Exception as exc:  # noqa: BLE001 - a warm-up must never stop the server
            log.debug("frame warm-up failed: %s", exc)  # render loop a few slow ticks
        try:
            self._server.serve_forever(poll_interval=0.2)
        finally:
            self._server.server_close()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()

    # --- the four endpoints ---------------------------------------------------------

    def status(self) -> dict:
        reply = self.app.submit("status", [], timeout=SUBMIT_TIMEOUT_S)
        if reply.get("ok"):
            reply["tray_state"] = self.app.tray_state()
            reply["dashboard_url"] = self.url
        return reply

    def frame_png(self) -> bytes:
        """The deck's current face as a PNG, at most one compose+encode per FRAME_CACHE_S."""
        with self._frame_lock:
            now = time.monotonic()
            if self._frame is not None and now - self._frame[0] < FRAME_CACHE_S:
                return self._frame[1]
            keys = list(self.app.deck._shown)  # a snapshot can mix frames, never tear one key
            board = Canvas.compose(keys, self.app.gap)
            buf = io.BytesIO()
            board.save(buf, format="PNG")
            self._frame = (now, buf.getvalue())
            return self._frame[1]

    def command(self, cmd: str, args: list[str]) -> dict:
        # restart and open are client-side helpers, not pipe commands; validate() rejects them.
        if cmd == "restart":
            pid = run_powershell(restart_script(config.ROOT))
            return {"ok": bool(pid), "pid": pid} if pid else {"ok": False, "error": "not on Windows"}
        if cmd == "open":
            return {"ok": True, "opened": open_window(self.url)}
        err = validate(cmd, args)
        if err:
            return {"ok": False, "error": err}
        return self.app.submit(cmd, args, timeout=SUBMIT_TIMEOUT_S)

    def save_settings(self, posted: dict) -> dict:
        try:
            update = validate_settings(posted)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not update:
            return {"ok": False, "error": "nothing to save"}
        before = self.app.cfg
        with self._write_lock:
            try:
                config.write_local(update)
            except (OSError, TypeError, ValueError) as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            reply = self.app.submit("reload", [], timeout=SUBMIT_TIMEOUT_S)
        if not reply.get("ok"):
            # The file on disk now holds a value the running app rejected, so say so plainly:
            # the app kept its old config but the next restart would read the new one.
            return {"ok": False, "saved": True, "error": f"saved, but the reload failed: {reply.get('error')}"}
        return {"ok": True, "restart_required": _restart_required(before, self.app.cfg),
                "tiles": reply.get("tiles"), "scenes": reply.get("scenes")}


def _handler_for(dash: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # the preview polls at 5 fps; a connect per frame is waste
        server_version = "deck-dash"

        def log_message(self, fmt, *args):  # noqa: A003 - BaseHTTPRequestHandler's own name
            # The default writes to sys.stderr, which is None under pythonw: every request would
            # raise inside the handler thread and the page would look dead.
            log.debug("dashboard %s - %s", self.address_string(), fmt % args)

        def _send(self, code: int, body: bytes, ctype: str, cache: bool = False) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if not cache:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict, code: int = 200) -> None:
            self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                raise ValueError("bad request body")
            return json.loads(self.rfile.read(length).decode("utf-8"))

        # --- who is allowed to ask -----------------------------------------------------
        # Binding to loopback keeps the LAN out; it does not keep a web page out, because the
        # browser making the request IS a local process. Two guards close that, and both cost the
        # real page nothing - it is always loaded from this exact origin.

        def _host_ok(self) -> bool:
            """Reject a request addressed to any name but loopback: a page on an attacker domain
            whose DNS is re-pointed at 127.0.0.1 is same-origin with us, so it could read every
            reply. Comparing Host is the only thing that tells the two apart."""
            raw = (self.headers.get("Host") or "").strip().lower()
            name = raw[1:raw.index("]")] if raw.startswith("[") and "]" in raw else raw.split(":", 1)[0]
            return name in LOCAL_HOSTS

        def _write_allowed(self) -> str | None:
            """None when a state-changing request may proceed, else why not.

            ``text/plain``, form and multipart bodies are CORS-safelisted, so a plain HTML form on
            any site can POST here with no preflight and no Origin check by the browser - and the
            side effect lands even though the reply is unreadable. Requiring JSON means a
            cross-origin caller must preflight, which we never answer."""
            ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if ctype != "application/json":
                return "expected Content-Type: application/json"
            origin = self.headers.get("Origin")
            if origin is not None and origin.strip().lower() not in dash.origins:
                return "cross-origin request refused"
            return None

        def do_GET(self) -> None:  # noqa: N802 - the stdlib's naming
            path = self.path.split("?", 1)[0]
            if not self._host_ok():
                self._json({"ok": False, "error": "bad host"}, 403)
                return
            try:
                if path in ("/", "/index.html"):
                    page = (UI_DIR / "index.html").read_bytes()
                    self._send(200, page, "text/html; charset=utf-8")
                elif path == "/api/status":
                    self._json(dash.status())
                elif path == "/api/config":
                    self._json({"ok": True, "config": effective(dash.app.cfg)})
                elif path == "/api/frame.png":
                    self._send(200, dash.frame_png(), "image/png")
                else:
                    self._json({"ok": False, "error": "not found"}, 404)
            except Exception as exc:  # noqa: BLE001 - one bad request must not kill the server
                log.warning("dashboard GET %s failed: %s", path, exc)
                self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

        def do_POST(self) -> None:  # noqa: N802 - the stdlib's naming
            path = self.path.split("?", 1)[0]
            if not self._host_ok():
                self._json({"ok": False, "error": "bad host"}, 403)
                return
            refused = self._write_allowed()
            if refused:
                log.warning("dashboard refused a POST to %s: %s (origin %r)", path, refused,
                            self.headers.get("Origin"))
                self._json({"ok": False, "error": refused}, 403)
                return
            try:
                body = self._body()
                if path == "/api/cmd":
                    cmd = str(body.get("cmd", ""))
                    args = [str(a) for a in (body.get("args") or [])]  # brightness 50 arrives as a number
                    self._json(dash.command(cmd, args))
                elif path == "/api/config":
                    self._json(dash.save_settings(body))
                else:
                    self._json({"ok": False, "error": "not found"}, 404)
            except (ValueError, UnicodeDecodeError) as exc:
                self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 400)
            except Exception as exc:  # noqa: BLE001 - one bad request must not kill the server
                log.warning("dashboard POST %s failed: %s", path, exc)
                self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    return Handler
