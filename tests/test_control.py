"""Phase 7a: the control channel and its commands, the tray icon, ``deckdash ctl``."""

import copy
import os
import pathlib
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from test_deckdash import cfg, sources  # noqa: E402,F401 - the shared fixtures

from deckdash import ctl  # noqa: E402
from deckdash.app import App  # noqa: E402
from deckdash.control import ControlServer, pipe_address, send, validate  # noqa: E402
from deckdash.device import SimDeck  # noqa: E402
from deckdash.gfx import AMBER, DIM, GREEN  # noqa: E402
from deckdash.main import main as deckdash_main  # noqa: E402
from deckdash.tray import STATE_COLORS, icon_image  # noqa: E402


def make_app(cfg, sources, tmp_path):
    cfg["deck"]["off_on_lock"] = False
    cfg["deck"]["night_start"] = cfg["deck"]["night_end"] = "00:00"  # no night window, whatever the clock says
    deck = SimDeck(gap=24, out_dir=tmp_path, interval=1e9)
    app = App(cfg, deck, sources=sources)
    app.start()
    app.tick()
    return app, deck


def test_commands_drive_the_app(cfg, sources, tmp_path):
    app, deck = make_app(cfg, sources, tmp_path)
    r = app.command("status", [])
    assert r["ok"] and r["mode"] == "board" and r["tiles"][0] == "clock" and r["sources"]["weather"]["error"] is None
    assert r["deck"] == {"type": "SimDeck"} and r["slow_ticks"] == 0 and r["brightness_override"] is None
    r = app.command("scene", ["tokyo"])
    assert r["ok"] and app.mode == "ambient" and app.scene.name == "tokyo"
    r = app.command("next", [])
    assert r["ok"] and r["scene"] == "weather"  # tokyo is last in the rotation, so next wraps around
    r = app.command("wake", [])
    assert r["ok"] and app.mode == "board" and app.scene is None
    assert app.command("scene", ["nope"])["ok"] is False
    assert app.command("bogus", [])["ok"] is False
    assert app.command("toast", ["test", "hello", "from ctl"])["ok"]
    app.tick()
    assert app.mode == "toast" and app.toast.kind == "TEST" and app.toast.title == "HELLO"
    assert app.command("wake", [])["ok"] and app.mode == "board" and not app.badges  # wake dismisses a toast like a press
    assert app.command("quit", [])["ok"] and app.stopping
    app.stop()


def test_pause_darkens_and_resume_restores(cfg, sources, tmp_path):
    app, deck = make_app(cfg, sources, tmp_path)
    app.command("scene", ["plasma"])
    app.tick()
    assert app.command("pause", [])["ok"]
    assert app.mode == "paused" and deck.brightness == 0 and app.scene is None and app.tray_state() == "off"
    sent = deck.sent
    app.scene_next = 0.0
    for tile in app.tiles:
        tile.next_due = 0.0
    app.tick()
    app.tick()
    assert deck.sent == sent  # nothing is rendered while paused
    assert app._sleep_s(0.0) == 1.0  # one tick per second
    assert app.command("status", [])["paused"] is True
    assert app.command("resume", [])["ok"]
    assert app.mode == "board" and deck.brightness == app.brightness and app.tray_state() == "on"
    app.tick()
    assert deck.sent > sent
    app.stop()


def test_brightness_override(cfg, sources, tmp_path):
    app, deck = make_app(cfg, sources, tmp_path)
    r = app.command("brightness", ["50"])
    assert r["ok"] and r["brightness"] == 50 and deck.brightness == 50
    app._apply_brightness(time.time(), force=True)
    assert deck.brightness == 50  # the schedule does not override a fixed level
    assert app.command("brightness", ["150"])["ok"] is False
    assert app.command("brightness", ["x"])["ok"] is False
    assert app.command("brightness", ["auto"])["ok"] and deck.brightness == app.brightness
    app.stop()


def test_reload_rebuilds_slots(cfg, sources, tmp_path):
    app, deck = make_app(cfg, sources, tmp_path)
    new = copy.deepcopy(cfg)  # after make_app: same no-night window, so 55 is what the deck must show
    new["layout"]["keys"] = ["gpu", "cpu"] + [""] * 13
    new["deck"]["brightness"] = 55
    new["ambient"]["scenes"] = ["life"]
    assert app.command("reload", [])["ok"] is False  # no loader
    app.config_loader = lambda: new
    r = app.command("reload", [])
    assert r["ok"] and r["tiles"] == ["gpu", "cpu"] and r["scenes"] == ["life"]
    assert app.brightness == 55 and deck.brightness == 55 and app.slots[2] is None
    app.tick()
    app.stop()


def test_pipe_round_trip(cfg, sources, tmp_path):
    app, deck = make_app(cfg, sources, tmp_path)
    name = f"deckdash-test-{os.getpid()}"
    server = ControlServer(app, pipe_address(name))
    server.start()
    assert server.ready.wait(5) and server.error is None
    result = {}
    t = threading.Thread(target=lambda: result.update(send("status", [], pipe=name, timeout=5)))
    t.start()
    deadline = time.time() + 5
    while t.is_alive() and time.time() < deadline:
        app.tick()  # the loop thread answers between ticks
        time.sleep(0.02)
    t.join(1)
    assert result.get("ok") and result["mode"] == "board" and result["pid"] == os.getpid()
    assert send("scene", [], pipe=name)["ok"] is False  # usage error, rejected client-side
    assert "usage" in send("toast", ["x"], pipe=name)["error"]
    server.stop()
    server.join(3)
    assert "not running" in send("status", [], pipe=name)["error"]
    app.stop()


def test_validate_and_ctl(capsys):
    assert validate("scene", []).startswith("usage")
    assert "unknown" in validate("nope", [])
    assert validate("toast", ["a", "b"]) is None and validate("toast", ["a", "b", "c", "d"]).startswith("usage")
    p = ctl.build_parser()
    a = p.parse_args(["--json", "scene", "tokyo"])
    assert a.command == "scene" and a.args == ["tokyo"] and a.json
    text = ctl.format_status({
        "version": "0.2.0", "pid": 7, "uptime_s": 3725, "mode": "ambient", "scene": "tokyo", "brightness": 30,
        "brightness_override": None, "ticks": 10, "slow_ticks": 0, "idle_s": 700, "badges": ["ci"],
        "ambient": {"scene": "tokyo", "fps": 13.9, "target": 14, "flush_avg_ms": 63, "flush_max_ms": 74, "slow": 0},
        "sources": {"weather": {"age_s": 12, "error": None, "failures": 0}, "ci": {"age_s": None, "error": "boom", "failures": 3}},
    })
    assert "mode ambient (tokyo)" in text and "up 1 h 02 min" in text and "badges: ci" in text
    assert "13.9 fps" in text and "failing x3: boom" in text and "12 s ago" in text
    assert ctl.main(["--pipe", "deckdash-test-none", "status"]) == 1
    assert "not running" in capsys.readouterr().err
    assert deckdash_main(["ctl", "--pipe", "deckdash-test-none", "wake"]) == 1  # dispatched before the app's own parser
    assert ctl.main(["--pipe", "deckdash-test-none", "--json", "status"]) == 1
    assert '"ok": false' in capsys.readouterr().out


def test_tray_icon_images():
    for state, color in STATE_COLORS.items():
        img = icon_image(state)
        assert img.size == (64, 64) and img.mode == "RGBA"
        assert img.getpixel((32, 32)) == tuple(color) + (255,)  # the centre key
        assert img.getpixel((0, 0))[3] == 0  # transparent corner
    assert STATE_COLORS == {"on": GREEN, "off": DIM, "badge": AMBER}
