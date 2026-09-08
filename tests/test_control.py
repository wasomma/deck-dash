"""Phase 7a: the control channel and its commands, the tray icon, ``deckdash ctl``."""

import copy
import os
import pathlib
import subprocess
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from test_deckdash import cfg, sources  # noqa: E402,F401 - the shared fixtures

from deckdash import control, ctl  # noqa: E402
from deckdash.app import App, Request  # noqa: E402
from deckdash.control import (ControlServer, calibrate_script, pipe_address,  # noqa: E402
                              restart_script, run_powershell, send, validate)
from deckdash.device import SimDeck  # noqa: E402
from deckdash.gfx import AMBER, DIM, GREEN  # noqa: E402
from deckdash.main import main as deckdash_main  # noqa: E402
from deckdash.tray import STATE_COLORS, Tray, icon_image  # noqa: E402


class _FakeIcon:
    """Stands in for pystray.Icon: counts what the watcher pushes, opens no window."""

    def __init__(self):
        self.icon = self.title = self._menu = None
        self.updates = self.menus = 0

    @property
    def menu(self):
        return self._menu

    @menu.setter
    def menu(self, value):
        self._menu = value
        self.menus += 1

    def update_menu(self):
        self.updates += 1


class _FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items):
        self.items = items


class _FakeMenuItem:
    def __init__(self, text, action, checked=None, radio=False, default=False):
        self.text, self.action, self.checked = text, action, checked


class _FakePystray:
    Menu, MenuItem = _FakeMenu, _FakeMenuItem


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
    assert r["deck"] == {"type": "SimDeck", "open": True} and r["slow_ticks"] == 0 and r["brightness_override"] is None
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
        "sources": {"weather": {"age_s": 12, "error": None, "failures": 0}, "ci": {"age_s": None, "error": "boom", "failures": 3},
                    "gpu": {"age_s": None, "error": "disabled", "failures": 0}},
    })
    assert "mode ambient (tokyo)" in text and "up 1 h 02 min" in text and "badges: ci" in text
    assert "13.9 fps" in text and "failing x3: boom" in text and "12 s ago" in text
    # An error with no failures behind it is a source that was switched off, not one that broke.
    assert "no data yet; disabled" in text and "failing x0" not in text
    assert ctl.main(["--pipe", "deckdash-test-none", "status"]) == 1
    assert "not running" in capsys.readouterr().err
    assert deckdash_main(["ctl", "--pipe", "deckdash-test-none", "wake"]) == 1  # dispatched before the app's own parser
    assert ctl.main(["--pipe", "deckdash-test-none", "--json", "status"]) == 1
    assert '"ok": false' in capsys.readouterr().out


def test_the_faces_before_the_deck_is_open(cfg, sources, tmp_path):
    """main() starts the tray, the pipe and the dashboard before it waits for the deck, so each of
    them has to say so rather than look healthy. Until the loop turns nothing drains the command
    queue, so ``submit`` answers ``status`` itself - a read of attributes nothing is mutating yet,
    and the one command a client needs while it is trying to find out what is wrong."""
    cfg["deck"]["off_on_lock"] = False
    deck = SimDeck(gap=24, out_dir=tmp_path, interval=1e9)  # constructed, deliberately not opened
    app = App(cfg, deck, sources=sources)
    assert not deck.opened and app.mode == "waiting" and app.tray_state() == "off"
    r = app.submit("status", [])
    assert r["ok"] and r["mode"] == "waiting" and r["deck"] == {"type": "SimDeck", "open": False}
    r = app.submit("next", [])
    assert not r["ok"] and "not open yet" in r["error"] and app.scene is None
    assert app.commands.empty()  # neither call left a request behind for a loop that is not running
    assert app.submit("quit", [])["ok"] and app.stopping  # the tray's Quit is the way out of the wait
    app._stop = False
    app.start()
    assert deck.opened and app.mode == "board" and app.tray_state() == "on"
    assert app.command("status", [])["deck"] == {"type": "SimDeck", "open": True}


def test_tray_icon_images():
    for state, color in STATE_COLORS.items():
        img = icon_image(state)
        assert img.size == (64, 64) and img.mode == "RGBA"
        assert img.getpixel((32, 32)) == tuple(color) + (255,)  # the centre key
        assert img.getpixel((0, 0))[3] == 0  # transparent corner
    assert STATE_COLORS == {"on": GREEN, "off": DIM, "badge": AMBER}


def test_reload_is_atomic_when_the_layout_is_bad(cfg, sources, tmp_path):
    """A reload that reports an error must change nothing: build_slots raises on an unknown tile
    name, and the tunables used to be applied before it ran, so a typo in config.local.toml left
    brightness, idle_minutes and the scene list from the rejected file live behind the old layout."""
    app, deck = make_app(cfg, sources, tmp_path)
    before = (app.brightness, app.idle_s, list(app.scene_names), [t.name for t in app.tiles], app.cfg)
    bad = copy.deepcopy(cfg)
    bad["layout"]["keys"] = ["nosuchtile"] + [""] * 14
    bad["deck"]["brightness"] = 11
    bad["deck"]["idle_minutes"] = 99
    bad["ambient"]["scenes"] = ["life"]
    app.config_loader = lambda: bad
    req = Request("reload", [])
    app.commands.put(req)
    app.tick()  # the loop drains the queue and turns the KeyError into an error reply, as ctl sees it
    assert req.done.is_set() and req.reply["ok"] is False and "nosuchtile" in req.reply["error"]
    assert (app.brightness, app.idle_s, list(app.scene_names), [t.name for t in app.tiles], app.cfg) == before
    app.tick()
    assert deck.brightness == app.brightness  # the periodic re-apply cannot push a rejected level
    app.stop()


def test_a_silent_client_does_not_own_the_accept_loop(cfg, sources, tmp_path, monkeypatch):
    """The pipe's default ACL lets any local user open it read-only and recv() waits forever, so a
    peer that connects and never speaks used to park the only server thread: ctl and the dashboard
    went dead until a restart while the tray still worked, so the app looked healthy."""
    from multiprocessing.connection import Client

    from deckdash import control

    monkeypatch.setattr(control, "RECV_TIMEOUT_S", 0.3)
    app, deck = make_app(cfg, sources, tmp_path)
    name = f"deckdash-test-silent-{os.getpid()}"
    server = ControlServer(app, pipe_address(name))
    server.start()
    assert server.ready.wait(5) and server.error is None

    squatter = Client(pipe_address(name), family=control._family())  # connects, never sends
    try:
        result = {}
        t = threading.Thread(target=lambda: result.update(send("status", [], pipe=name, timeout=5)))
        t.start()
        deadline = time.time() + 10
        while t.is_alive() and time.time() < deadline:
            app.tick()
            time.sleep(0.01)
        t.join(5)
        assert result.get("ok") is True and result["mode"] == "board"  # served despite the squatter
    finally:
        squatter.close()
        server.stop()
        app.stop()


def test_tray_pushes_outside_changes_to_the_menu(cfg, sources, tmp_path):
    """pystray's win32 backend snapshots the menu into a native HMENU and only rebuilds it on
    update_menu(), so a pause from ctl or the dashboard left the item reading "Pause" - clicking it
    resumed instead. The watcher has to push icon, tooltip and menu."""
    app, deck = make_app(cfg, sources, tmp_path)
    tray = Tray(app, ROOT)
    tray.icon = _FakeIcon()
    tray._pystray = _FakePystray()
    tray._sig = tray._signature()
    assert tray._sync() is False  # nothing changed yet

    app.command("pause", [])
    assert tray._sync() is True
    assert tray.icon.updates == 1 and tray.icon.icon is not None  # menu rebuilt, icon greyed
    app.command("resume", [])
    app.command("brightness", ["30"])
    assert tray._sync() is True and tray.icon.updates == 2  # an override the tray never set
    menus = tray.icon.menus
    app.scene_names = ["life"]
    assert tray._sync() is True and tray.icon.menus == menus + 1  # reload can change the scene list
    app.stop()


def test_task_scripts_restart_even_when_abandoned():
    """install_task.ps1 -Stop kills the app and takes the tray with it, so every script that stops
    it has to start it again from a finally: an aborted calibration must not leave the deck dark."""
    root = pathlib.Path(r"C:\deck-dash")
    restart = restart_script(root)
    assert restart.startswith("try {") and "finally {" in restart
    assert restart.index("-Stop") < restart.index("finally") < restart.index("-Start")
    cal = calibrate_script(root)
    assert "try {" in cal and "finally {" in cal
    assert cal.index("calibrate.py") < cal.index("finally")
    tail = cal[cal.index("finally"):]  # Ctrl+C or a crashing tool must still reach the -Start
    assert "-Start" in tail and "Read-Host" in tail
    assert "closed with the X" in cal[:cal.index("try {")]  # the one path a finally cannot cover

@pytest.mark.skipif(sys.platform != "win32", reason="windows process flags")
def test_run_powershell_gives_the_child_a_console_and_a_log(monkeypatch, tmp_path):
    """DETACHED_PROCESS leaves PowerShell with no console: it exits 0 without running -Command
    at all, so the tray, the dashboard and ``ctl restart`` all reported a pid and did nothing.
    Output must not go to DEVNULL either - that is what kept the failure silent."""
    seen = {}

    class _Fake:
        pid = 4242

        def __init__(self, argv, **kw):
            seen.update(kw, argv=argv)

    monkeypatch.setattr(control.subprocess, "Popen", _Fake)
    monkeypatch.setattr(control, "PS_LOG", tmp_path / "logs" / "powershell.log")
    assert run_powershell("Write-Host hi") == 4242

    flags = seen["creationflags"]
    assert not flags & subprocess.DETACHED_PROCESS
    assert flags & subprocess.CREATE_NO_WINDOW
    assert seen["stdout"] is not subprocess.DEVNULL and seen["stderr"] is not subprocess.DEVNULL
    assert "Write-Host hi" in (tmp_path / "logs" / "powershell.log").read_text(encoding="utf-8")

    # a console run keeps its window and writes to it, not to the log
    run_powershell("Write-Host hi", console=True)
    assert seen["creationflags"] & subprocess.CREATE_NEW_CONSOLE
    assert seen["stdout"] is None
