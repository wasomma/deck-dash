"""Phase 7b: the localhost dashboard - its four endpoints, the settings validator, the wiring."""

import copy
import io
import json
import pathlib
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
from deckdash.app import build_slots  # noqa: E402
from deckdash.dashboard import Dashboard, effective, validate_settings  # noqa: E402
from deckdash.tiles import TILES  # noqa: E402


def serve(app, port):
    board = Dashboard(app, port)
    board.start()
    assert board.ready.wait(5), "the dashboard thread never became ready"
    return board


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
    board = serve(app, 8791)
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
    board = serve(app, 8792)
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
    board = serve(app, 8793)
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
    board = serve(app, 8794)
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
    first = serve(app, 8795)
    second = Dashboard(app, 8795)
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
    dd_config.write_local({"weather": {"latitude": 38.36, "place": "somewhere"}}, local)
    monkeypatch.setattr(dd_config, "LOCAL_CONFIG", local)
    app.config_loader = lambda: dd_config._merge(copy.deepcopy(cfg), dd_config.load(ROOT / "config.toml", local))
    board = serve(app, 8796)
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
    board = serve(app, 8797)
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
    cfg["weather"] = {"latitude": 38.36, "longitude": -75.59, "place": "home"}
    cfg["bitaxe"] = {"host": "10.0.0.191"}
    cfg["vps"] = {"ssh_host": "guild-vps"}
    out = json.dumps(effective(cfg))
    assert "38.36" not in out and "10.0.0.191" not in out and "guild-vps" not in out
    assert "brightness" in out and "scenes" in out
