import json
import pathlib
import sys
import time

import pytest
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deckdash import config, gfx, wx_icons  # noqa: E402
from deckdash.app import App  # noqa: E402
from deckdash.canvas import Canvas  # noqa: E402
from deckdash.device import SimDeck  # noqa: E402
from deckdash.sources.base import StaticSource  # noqa: E402
from deckdash.sources.weather import parse_openmeteo  # noqa: E402
from deckdash.tiles import TILES, make_tile  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
GB = 1 << 30


@pytest.fixture
def cfg():
    return config.load(ROOT / "config.toml", local=ROOT / "tests" / "no-such-local.toml")


def weather_state():
    st = parse_openmeteo(json.loads((FIX / "openmeteo.json").read_text()))
    st["units"] = "imperial"
    st["place"] = "Testville"
    return st


@pytest.fixture
def sources():
    return {
        "weather": StaticSource(weather_state()),
        "sys": StaticSource({
            "cpu": 23.5, "per_core": [10.0 * (i % 10) for i in range(32)], "ghz": 4.9,
            "mem_used": 16 * GB, "mem_total": 32 * GB, "disk_used": 800 * GB, "disk_total": 1863 * GB,
            "down": 12.4 * (1 << 20), "up": 1.2 * (1 << 20),
            "cpu_hist": [20 + (i % 7) * 5 for i in range(60)],
            "down_hist": [(i % 13) * 1e6 for i in range(60)], "up_hist": [(i % 5) * 1e5 for i in range(60)],
            "boot": time.time() - 90000, "procs": 312,
        }),
        "gpu": StaticSource({
            "name": "NVIDIA GeForce RTX 4090", "util": 64.0, "mem_util": 40.0, "temp": 71.0,
            "mem_used": int(12.1 * GB), "mem_total": 24 * GB, "power": 312.0, "fan": 45.0, "clock": 2520,
            "util_hist": [30 + (i % 10) * 6 for i in range(60)], "temp_hist": [60 + (i % 4) for i in range(60)],
            "power_hist": [200 + i for i in range(60)],
        }),
        "ping": StaticSource({"ms": 9, "hist": [8, 9, 12, 9, -1, 10], "host": "1.1.1.1"}),
    }


@pytest.fixture
def empty_sources():
    return {k: StaticSource({}, error="offline") for k in ("weather", "sys", "gpu", "ping")}


def _assert_key(img):
    assert isinstance(img, Image.Image)
    assert img.size == (gfx.KEY, gfx.KEY)
    assert img.mode == "RGB"


@pytest.mark.parametrize("name", sorted(TILES))
def test_tile_renders_with_data(cfg, sources, name):
    tile = make_tile(name, cfg, sources)
    _assert_key(tile.render(time.time()))


@pytest.mark.parametrize("name", sorted(TILES))
def test_tile_renders_without_data(cfg, empty_sources, name):
    tile = make_tile(name, cfg, empty_sources)
    _assert_key(tile.render(time.time()))


@pytest.mark.parametrize("name", sorted(TILES))
def test_zoom_views_are_full_deck(cfg, sources, empty_sources, name):
    for srcs in (sources, empty_sources):
        tile = make_tile(name, cfg, srcs)
        if not tile.zoomable:
            continue
        images = tile.render_zoom(time.time())
        assert len(images) == 15
        for img in images:
            _assert_key(img)


def test_weather_parse_starts_after_current_hour():
    st = weather_state()
    assert st["current"]["code"] == 61
    assert st["hourly"][0]["time"] == "2026-09-06T15:00"
    assert st["hourly"][-1]["precip"] == 0  # null probability coerced
    assert len(st["daily"]) == 6
    assert st["daily"][5]["precip"] == 0


@pytest.mark.parametrize("code,cat", [(0, "clear"), (2, "partly"), (3, "overcast"), (45, "fog"), (53, "drizzle"), (63, "rain"), (81, "rain"), (73, "snow"), (86, "snow"), (95, "thunder"), (99, "thunder")])
def test_weather_categories(code, cat):
    assert wx_icons.category(code) == cat


@pytest.mark.parametrize("size", [12, 40, 48])
def test_weather_icons_render_every_category(size):
    for code in (0, 2, 3, 45, 55, 65, 75, 95):
        for is_day in (True, False):
            img = wx_icons.icon(size, code, is_day, t=1.234)
            assert img.size == (size, size) and img.mode == "RGBA"
            assert img.getbbox() is not None  # something was drawn


def test_canvas_geometry():
    c = Canvas(gap=24)
    assert (c.w, c.h) == (456, 264)
    assert c.key_box(6) == (96, 96, 168, 168)
    assert c.rows_box(1, 2) == (0, 96, 456, 264)
    assert len(c.slice()) == 15


def test_simdeck_sends_only_changed_keys(tmp_path):
    deck = SimDeck(gap=24, out_dir=tmp_path, interval=0)
    deck.open()
    a = gfx.new_key((1, 2, 3))
    deck.set_key_image(0, a)
    deck.set_key_image(1, a)
    assert deck.flush(1.0) == 2
    deck.set_key_image(0, gfx.new_key((1, 2, 3)))  # identical pixels, new object
    assert deck.flush(1.0) == 0
    deck.set_key_image(0, gfx.new_key((9, 9, 9)))
    assert deck.flush(1.0) == 1
    assert deck.sent == 3
    assert (tmp_path / "canvas.png").exists()


def test_simdeck_budget_carries_over(tmp_path):
    deck = SimDeck(gap=24, out_dir=tmp_path, interval=1e9)
    deck.open()
    for i in range(15):
        deck.set_key_image(i, gfx.new_key((i, i, i)))
    first = deck.flush(0.0)  # budget exhausted after the first send
    assert first == 1
    assert deck.flush(10.0) == 14


def test_app_board_then_zoom_then_back(cfg, sources, tmp_path):
    deck = SimDeck(gap=24, out_dir=tmp_path, interval=1e9)
    app = App(cfg, deck, sources=sources)
    app.start()
    app.tick()
    assert deck.sent == 15  # 6 tiles + 9 blanks
    app._on_press(3, True)  # gpu tile
    app.tick()
    assert app.zoom is not None and app.zoom.name == "gpu"
    app._on_press(0, True)
    app.tick()
    assert app.zoom is None
    app.stop()


def test_local_override_roundtrip(tmp_path):
    local = tmp_path / "config.local.toml"
    config.write_local({"weather": {"latitude": 40.5, "longitude": -75.25, "place": 'Some "Town", PA'}}, local=local)
    config.write_local({"deck": {"gap_px": 30}}, local=local)
    cfg = config.load(ROOT / "config.toml", local=local)
    assert cfg["weather"]["latitude"] == 40.5
    assert cfg["weather"]["place"] == 'Some "Town", PA'
    assert cfg["deck"]["gap_px"] == 30
    assert cfg["deck"]["brightness"] == 80  # untouched keys survive the merge


def test_fmt_helpers():
    assert gfx.fmt_rate(12.4 * (1 << 20)) == "12M"
    assert gfx.fmt_rate(1.2 * (1 << 20)) == "1.2M"
    assert gfx.fmt_rate(830 * 1024) == "830K"
    assert gfx.fmt_duration(90000) == "1d 1h"
    assert gfx.fit_size("12345678901234567890", 40, 20) < 20
