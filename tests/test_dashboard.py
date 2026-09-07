"""Phase 7b: the localhost dashboard - its four endpoints, the settings validator, the wiring."""

import copy
import io
import json
import os
import pathlib
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from test_control import make_app  # noqa: E402,F401 - the SimDeck app builder
from test_deckdash import cfg, sources  # noqa: E402,F401 - the shared fixtures

from deckdash import config as dd_config  # noqa: E402
from deckdash import dashboard  # noqa: E402
from deckdash import main as main_mod  # noqa: E402
from deckdash.app import build_slots  # noqa: E402
from deckdash.control import send  # noqa: E402
from deckdash.device import SimDeck  # noqa: E402
from deckdash.dashboard import Dashboard, effective, validate_settings  # noqa: E402
from deckdash.tiles import TILES  # noqa: E402


def serve(app, port=0):
    """Port 0 = let the OS pick. Hard-coded ports collided when two runs overlapped, and because
    `ready` is set on the refused path too, the loser silently drove the winner's App."""
    board = Dashboard(app, port)
    board.start()
    assert board.ready.wait(5), "the dashboard thread never became ready"
    assert board.error is None, f"the dashboard did not bind: {board.error}"
    return board


def raw(board, request: bytes) -> bytes:
    """One request written byte for byte, so a test can shape headers a browser would send."""
    chunks = []
    with socket.create_connection(("127.0.0.1", board.port), timeout=5) as s:
        s.sendall(request)
        try:
            while True:  # every request here sends Connection: close, so read to EOF
                b = s.recv(4096)
                if not b:
                    break
                chunks.append(b)
        except (TimeoutError, OSError):
            pass
    return b"".join(chunks)


def drive(app, work, timeout=5):
    """Run ``work`` on another thread while this one turns the render loop, as a client does:
    anything that reaches App.submit is answered between ticks, so nothing here may block on it."""
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", work()))
    t.start()
    deadline = time.time() + timeout
    while t.is_alive() and time.time() < deadline:
        app.tick()
        time.sleep(0.01)
    t.join(2)
    assert "r" in out, "the call never came back"
    return out["r"]


def fetch(board, path, body=None, timeout=5):
    url = board.url.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as exc:  # 404 and 400 carry a JSON body worth reading
        return exc.headers.get("Content-Type"), exc.read()


def call(app, board, path, body=None):
    ctype, raw = drive(app, lambda: fetch(board, path, body))
    return json.loads(raw) if "json" in (ctype or "") else raw


# --- the endpoints ----------------------------------------------------------------------------

def test_status_frame_and_page(cfg, sources, tmp_path):
    app, deck = make_app(cfg, sources, tmp_path)
    board = serve(app)
    try:
        page = call(app, board, "/")
        assert b"<title>deck-dash</title>" in page and b"/api/frame.png" in page

        s = call(app, board, "/api/status")
        assert s["ok"] and s["mode"] == "board" and s["tray_state"] == "on"
        assert s["dashboard_url"] == board.url and s["sources"]["weather"]["error"] is None

        png = call(app, board, "/api/frame.png")
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        from PIL import Image
        img = Image.open(io.BytesIO(png))
        assert img.size == (5 * 72 + 4 * app.gap, 3 * 72 + 2 * app.gap)

        c = call(app, board, "/api/config")
        assert c["ok"] and c["config"]["tiles"] == list(TILES)
        assert "latitude" not in json.dumps(c) and "ssh_host" not in json.dumps(c)  # local-only keys
        assert call(app, board, "/api/nope")["error"] == "not found"
    finally:
        board.stop()
        app.stop()


def test_frame_is_cached_and_survives_an_unwritten_key(cfg, sources, tmp_path):
    app, deck = make_app(cfg, sources, tmp_path)
    board = serve(app)
    try:
        first = board.frame_png()
        assert board.frame_png() is first  # inside the 200 ms window: one compose, not one per client
        deck._shown[3] = None  # a key never written yet must not break the composer
        board._frame = None
        assert board.frame_png()[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        board.stop()
        app.stop()


def test_cmd_endpoint_drives_the_app(cfg, sources, tmp_path):
    app, deck = make_app(cfg, sources, tmp_path)
    board = serve(app)
    try:
        r = call(app, board, "/api/cmd", {"cmd": "scene", "args": ["tokyo"]})
        assert r["ok"] and app.scene.name == "tokyo"
        r = call(app, board, "/api/cmd", {"cmd": "brightness", "args": [50]})  # JSON number, not a string
        assert r["ok"] and deck.brightness == 50
        assert call(app, board, "/api/cmd", {"cmd": "pause", "args": []})["ok"] and app.paused
        assert call(app, board, "/api/cmd", {"cmd": "resume", "args": []})["ok"] and not app.paused
        assert "unknown command" in call(app, board, "/api/cmd", {"cmd": "rm -rf", "args": []})["error"]
        assert "usage" in call(app, board, "/api/cmd", {"cmd": "scene", "args": []})["error"]
    finally:
        board.stop()
        app.stop()


def test_open_is_intercepted_before_validate(cfg, sources, tmp_path, monkeypatch):
    """restart and open are client-side helpers; control.validate would reject them as unknown."""
    app, deck = make_app(cfg, sources, tmp_path)
    board = serve(app)
    opened = []
    monkeypatch.setattr(dashboard, "open_window", lambda url: opened.append(url) or "edge")
    try:
        r = board.command("open", [])
        assert r["ok"] and r["opened"] == "edge" and opened == [board.url]
    finally:
        board.stop()
        app.stop()


def test_a_busy_port_is_a_warning_not_a_failure(cfg, sources, tmp_path):
    """A second copy or a stale window must not stop the deck from lighting up."""
    app, deck = make_app(cfg, sources, tmp_path)
    first = serve(app)  # then collide with whatever the OS actually gave us
    second = Dashboard(app, first.port)
    second.start()
    try:
        assert second.ready.wait(5) and second.error is not None
        assert first.error is None
    finally:
        first.stop()
        second.stop()
        app.stop()


# --- the settings form ------------------------------------------------------------------------

def test_save_writes_reloads_and_reports_a_restart(cfg, sources, tmp_path, monkeypatch):
    app, deck = make_app(cfg, sources, tmp_path)
    local = tmp_path / "config.local.toml"
    dd_config.write_local({"weather": {"latitude": 12.34, "place": "somewhere"}}, local)
    monkeypatch.setattr(dd_config, "LOCAL_CONFIG", local)
    app.config_loader = lambda: dd_config._merge(copy.deepcopy(cfg), dd_config.load(ROOT / "config.toml", local))
    board = serve(app)
    try:
        r = drive(app, lambda: board.save_settings({"deck": {"brightness": 44}, "ambient": {"scene_minutes": 3}}))
        assert r["ok"], r
        assert r["restart_required"] is False
        assert app.brightness == 44 and app.scene_s == 180

        written = dd_config.load(ROOT / "config.toml", local)
        assert written["deck"]["brightness"] == 44
        assert written["weather"]["place"] == "somewhere"  # untouched keys survive the rewrite

        r = drive(app, lambda: board.save_settings({"ci": {"repos": [{"repo": "a/b", "label": "b"}]}}))
        assert r["ok"] and r["restart_required"] is True  # GhPoller reads repos once, at start
    finally:
        board.stop()
        app.stop()


def test_a_rejected_save_never_reaches_the_file(cfg, sources, tmp_path, monkeypatch):
    app, deck = make_app(cfg, sources, tmp_path)
    local = tmp_path / "config.local.toml"
    monkeypatch.setattr(dd_config, "LOCAL_CONFIG", local)
    board = serve(app)
    try:
        for bad, why in [
            ({"deck": {"brightness": 900}}, "0-100"),
            ({"deck": {"night_start": "2200"}}, "HH:MM"),
            ({"deck": {"idle_minutes": float("nan")}}, "not a number"),
            ({"ambient": {"scenes": ["nope"]}}, "unknown scene"),
            ({"layout": {"keys": ["clock"]}}, "15 keys"),
            ({"layout": {"keys": ["nosuchtile"] + [""] * 14}}, "unknown tile"),
            ({"news": {"feeds": [{"name": "a\nb", "url": "https://x/"}]}}, "control characters"),
            ({"news": {"feeds": [{"name": "a", "url": "javascript:x"}]}}, "http"),
            ({"ci": {"repos": [{"repo": "nope"}]}}, "owner/name"),
        ]:
            r = board.save_settings(bad)
            assert r["ok"] is False and why in r["error"], f"{bad} -> {r}"
        assert not local.exists()  # nothing was written at all
    finally:
        board.stop()
        app.stop()


def test_write_local_refuses_an_unparsable_file(tmp_path):
    """config.local.toml is read at every start: a bad write would leave the deck dark for good."""
    local = tmp_path / "config.local.toml"
    dd_config.write_local({"deck": {"brightness": 60}}, local)
    good = local.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="unparsable"):
        dd_config.write_local({"news": {"feeds": [{"name": "a\nb = 1", "url": "x"}]}}, local)
    assert local.read_text(encoding="utf-8") == good  # the old file is still there
    assert not (tmp_path / "config.local.tmp.toml").exists()


def test_a_blank_overlay_switches_one_off(cfg, sources, tmp_path):
    """config.local.toml merges key by key into config.toml, so blanking is the only way off -
    and it used to raise KeyError here and then break the next startup too."""
    assert validate_settings({"layout": {"overlays": {"net": ""}}}) == {"layout": {"overlays": {"net": ""}}}
    cfg["layout"]["overlays"] = {"net": ""}
    slots = build_slots(cfg["layout"]["keys"], 15, cfg, sources)
    net = [t for t in slots if t is not None and t.name == "net"]
    assert net and net[0].name == "net"  # plain, not "claude|net"


def test_effective_hides_the_machine_local_keys(cfg, sources):
    # Stand-ins, deliberately not the real ones: this repo is public and the test exists
    # precisely because these keys must never leave the machine.
    cfg["weather"] = {"latitude": 12.34, "longitude": -56.78, "place": "Placeville"}
    cfg["bitaxe"] = {"host": "192.0.2.7"}
    cfg["vps"] = {"ssh_host": "example-vps"}
    out = json.dumps(effective(cfg))
    assert "12.34" not in out and "192.0.2.7" not in out and "example-vps" not in out
    assert "brightness" in out and "scenes" in out


# --- who is allowed to ask ----------------------------------------------------------------------

def test_a_cross_site_form_post_cannot_drive_the_deck(cfg, sources, tmp_path):
    """text/plain, form and multipart bodies are CORS-safelisted, so a plain HTML form on any site
    reaches loopback with no preflight - and the side effect lands even though the reply is
    unreadable. The form trick puts the '=' inside an ignored key, so the body is valid JSON."""
    app, deck = make_app(cfg, sources, tmp_path)
    board = serve(app)
    try:
        for ctype in (b"text/plain;charset=UTF-8", b"application/x-www-form-urlencoded",
                      b"multipart/form-data; boundary=----x"):
            body = b'{"cmd":"quit","args":[],"x":"="}\r\n'
            req = (b"POST /api/cmd HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % board.port
                   + b"Origin: https://evil.example\r\nContent-Type: " + ctype + b"\r\n"
                   + b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
            reply = drive(app, lambda r=req: raw(board, r))
            assert b"403" in reply.split(b"\r\n", 1)[0], ctype
            assert b"application/json" in reply
        assert not app.stopping and not app.paused  # nothing got through

        # A same-origin JSON post from the page itself still works.
        assert call(app, board, "/api/cmd", {"cmd": "pause", "args": []})["ok"] and app.paused
    finally:
        board.stop()
        app.stop()


def test_a_foreign_origin_is_refused_even_with_json(cfg, sources, tmp_path):
    app, deck = make_app(cfg, sources, tmp_path)
    board = serve(app)
    try:
        body = b'{"cmd":"quit","args":[]}'
        req = (b"POST /api/cmd HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % board.port
               + b"Origin: https://evil.example\r\nContent-Type: application/json\r\n"
               + b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
        reply = drive(app, lambda: raw(board, req))
        assert b"403" in reply.split(b"\r\n", 1)[0] and b"cross-origin" in reply
        assert not app.stopping

        own = f"http://127.0.0.1:{board.port}".encode()
        req = req.replace(b"Origin: https://evil.example", b"Origin: " + own)
        reply = drive(app, lambda: raw(board, req))
        assert b"200" in reply.split(b"\r\n", 1)[0] and app.stopping  # our own page may
    finally:
        board.stop()
        app.stop()


def test_a_rebound_host_is_refused_on_reads_too(cfg, sources, tmp_path):
    """Loopback binding does not stop a page whose DNS was re-pointed at 127.0.0.1: the browser
    treats it as same-origin, so it could READ the deck image and the status. Only Host tells them
    apart."""
    app, deck = make_app(cfg, sources, tmp_path)
    board = serve(app)
    try:
        for host in (b"deck.attacker.example", b"deck.attacker.example:1234"):
            req = (b"GET /api/status HTTP/1.1\r\nHost: " + host + b"\r\nConnection: close\r\n\r\n")
            reply = drive(app, lambda r=req: raw(board, r))
            assert b"403" in reply.split(b"\r\n", 1)[0] and b"bad host" in reply, host
        for host in (b"127.0.0.1:%d" % board.port, b"localhost:%d" % board.port, b"[::1]:%d" % board.port):
            req = (b"GET /api/status HTTP/1.1\r\nHost: " + host + b"\r\nConnection: close\r\n\r\n")
            reply = drive(app, lambda r=req: raw(board, r))
            assert b"200" in reply.split(b"\r\n", 1)[0], host
    finally:
        board.stop()
        app.stop()


def test_write_local_leaves_no_temp_copy_behind(tmp_path, monkeypatch):
    """The temp file holds every personal value in config.local.toml. It is gitignored, and it has
    to be gone on every path out - os.replace raises PermissionError on Windows whenever anything
    holds the destination open, which the tray's Edit config does."""
    local = tmp_path / "config.local.toml"
    dd_config.write_local({"weather": {"place": "Placeville"}}, local)
    tmp = local.with_suffix(".tmp.toml")
    assert not tmp.exists()

    real = dd_config.os.replace

    def boom(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(dd_config.os, "replace", boom)
    with pytest.raises(PermissionError):
        dd_config.write_local({"deck": {"brightness": 42}}, local)
    assert not tmp.exists(), "a copy of the personal config was left in the tree"
    monkeypatch.setattr(dd_config.os, "replace", real)
    assert dd_config.load(ROOT / "config.toml", local)["weather"]["place"] == "Placeville"
def test_the_faces_are_up_before_the_deck(cfg, tmp_path, monkeypatch):
    """main() used to open the deck first, and open_with_retry waits for ever, so a missing
    hidapi.dll or a deck another process already held gave no tray, no dashboard, no pipe and no
    crash - total silence. The three faces start first now, and every one of them answers while the
    open is still blocked. Against the old order the pipe below is simply not running yet."""
    order: list = []
    reached, release = threading.Event(), threading.Event()

    class _LateDeck(SimDeck):
        """A deck that does not arrive until the test says so, the way an unplugged one behaves."""

        def open(self):
            order.append("deck")
            reached.set()
            assert release.wait(10), "the test never released the open"
            super().open()

    class _RecordingTray:  # pystray for real would put an icon in Wes's tray during the test run
        def __init__(self, app, root, url):
            self.url = url

        def start(self):
            order.append("tray")

        def stop(self):
            order.append("tray stopped")

    conf = copy.deepcopy(cfg)
    pipe = f"deckdash-test-faces-{os.getpid()}"  # its own pipe and an OS-picked port: the live app keeps its own
    conf["ui"] = {"tray": True, "pipe": pipe, "dashboard_port": 0}
    conf_path = tmp_path / "config.toml"
    dd_config.write_local(conf, conf_path)  # the whole dict written back out as TOML

    boards: list = []
    real_dashboard = main_mod.Dashboard

    def _dashboard(app, port):
        boards.append(real_dashboard(app, port))
        return boards[-1]

    monkeypatch.setattr(dd_config, "LOCAL_CONFIG", tmp_path / "no-such-local.toml")
    monkeypatch.setattr(main_mod, "setup_logging", lambda verbose: None)  # no handler on the repo's log
    monkeypatch.setattr(main_mod, "normal_priority", lambda: "priority test")  # leave pytest's own process alone
    monkeypatch.setattr(main_mod, "job_summary", lambda: "none")
    monkeypatch.setattr(main_mod, "single_instance", lambda *a, **k: True)  # the live task holds the real mutex
    monkeypatch.setattr(main_mod, "RealDeck", lambda **kw: _LateDeck(gap=24, out_dir=tmp_path, interval=1e9))
    monkeypatch.setattr(main_mod, "Tray", _RecordingTray)
    monkeypatch.setattr(main_mod, "Dashboard", _dashboard)
    monkeypatch.setattr(main_mod.App, "run", lambda self, max_seconds=None: order.append("run"))  # no source ever starts

    t = threading.Thread(target=lambda: order.append(f"exit {main_mod.main(['--config', str(conf_path)])}"))
    t.start()
    try:
        assert reached.wait(10), "main never got as far as opening the deck"
        r = send("status", [], pipe=pipe, timeout=5)
        assert r["ok"] and r["mode"] == "waiting" and r["deck"]["open"] is False
        assert "not open yet" in send("pause", [], pipe=pipe, timeout=5)["error"]
        board = boards[0]
        s = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{board.port}/api/status", timeout=5).read())
        assert s["ok"] and s["mode"] == "waiting" and s["tray_state"] == "off" and s["deck"]["open"] is False
        page = urllib.request.urlopen(f"http://127.0.0.1:{board.port}/", timeout=5)
        assert page.status == 200 and b"deck-dash" in page.read()
        assert order == ["tray", "deck"]  # the tray first, and nothing past the open
    finally:
        release.set()
        t.join(15)
    assert not t.is_alive()
    assert order == ["tray", "deck", "run", "tray stopped", "exit 0"]
