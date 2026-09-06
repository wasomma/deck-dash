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
from deckdash.sources.bitaxe import fmt_diff, fmt_hash, parse_axeos  # noqa: E402
from deckdash.sources.bsod import merge_crashes, parse_events, summarize  # noqa: E402
from deckdash.sources.ci import normalize_repos, summarize_repo  # noqa: E402
from deckdash.sources.news import interleave, parse_feed  # noqa: E402
from deckdash.sources.vps import parse_probe, service_state  # noqa: E402
from deckdash.sources.weather import parse_openmeteo  # noqa: E402
from deckdash.tiles import TILES, make_tile  # noqa: E402
from deckdash.tiles.ci import wrap_text  # noqa: E402
from deckdash.tiles.news import Strip  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
GB = 1 << 30
SOURCE_NAMES = ("weather", "sys", "gpu", "ping", "bitaxe", "ci", "vps", "bsod", "news")


@pytest.fixture
def cfg():
    c = config.load(ROOT / "config.toml", local=ROOT / "tests" / "no-such-local.toml")
    c["vps"]["services"] = [
        {"name": "fpv-sim-mcp", "label": "mcp", "unit": "fpv-sim-mcp", "port": 8080},
        {"name": "sales-live", "label": "sales", "unit": "sales-live", "port": 8141},
        {"name": "guild", "label": "guild", "unit": "guild", "port": 8787},
    ]
    return c


def weather_state():
    st = parse_openmeteo(json.loads((FIX / "openmeteo.json").read_text()))
    st["units"] = "imperial"
    st["place"] = "Testville"
    return st


def bitaxe_state():
    st = parse_axeos(json.loads((FIX / "axeos.json").read_text()))
    st["hist"] = [1000 + (i % 9) * 12 for i in range(60)]
    st["temp_hist"] = [58 + (i % 4) for i in range(60)]
    st["new_best"] = False
    st["host"] = "bitaxe"
    return st


def ci_state(now):
    runs = json.loads((FIX / "gh_runs.json").read_text())
    pulls = json.loads((FIX / "gh_pulls.json").read_text())
    repos = [
        summarize_repo("o/app", "app", runs, pulls),
        summarize_repo("o/sim", "sim", {"workflow_runs": [dict(runs["workflow_runs"][1], status="in_progress", conclusion=None)]}, []),
        summarize_repo("o/mcp", "mcp", runs["workflow_runs"][1:], []),
        summarize_repo("o/range", "range", [], []),
        summarize_repo("o/duet", "duet", [], []),
        summarize_repo("o/guild", "guild", runs["workflow_runs"][1:], []),
    ]
    prs = sorted((p for r in repos for p in r["prs"]), key=lambda p: -p["at"])
    return {"repos": repos, "prs": prs, "running": True, "failed": 1, "checked": now}


def vps_state():
    rows = []
    for name, port, state in (("fpv-sim-mcp", 8080, "ok"), ("sales-live", 8141, "degraded"), ("guild", 8787, "down")):
        rows.append({
            "name": name, "port": port, "state": state, "active": "active" if state != "down" else "inactive",
            "local_code": "200", "local_ms": 2, "public_code": 200, "public_ms": 150, "ms": 150,
            "hist": [140 + (i % 5) * 7 for i in range(30)],
        })
    return {"services": rows, "ssh_ok": True, "ssh_error": None, "ssh_ms": 900, "load": [0.1, 0.2, 0.3], "uptime": 1_700_000, "all_ok": False, "checked": time.time()}


def bsod_state(now):
    crashes = merge_crashes(parse_events((FIX / "events.xml").read_text()))
    # Shift the fixture so it is always "recent" relative to now.
    shift = now - 2 * 86400 - crashes[0]["time"]
    for c in crashes:
        c["time"] += shift
    return summarize(crashes, now, now - 90000, 30)


def news_state(now):
    items = parse_feed("HN", (FIX / "rss.xml").read_bytes()) + parse_feed("BBC", (FIX / "rss.xml").read_bytes())
    for it in items:
        it["at"] = now - 3600
    return {"items": items, "errors": [], "fetched": now}


@pytest.fixture
def sources():
    now = time.time()
    return {
        "weather": StaticSource(weather_state()),
        "sys": StaticSource({
            "cpu": 23.5, "per_core": [10.0 * (i % 10) for i in range(32)], "ghz": 4.9,
            "mem_used": 16 * GB, "mem_total": 32 * GB, "disk_used": 800 * GB, "disk_total": 1863 * GB,
            "down": 12.4 * (1 << 20), "up": 1.2 * (1 << 20),
            "cpu_hist": [20 + (i % 7) * 5 for i in range(60)],
            "down_hist": [(i % 13) * 1e6 for i in range(60)], "up_hist": [(i % 5) * 1e5 for i in range(60)],
            "boot": now - 90000, "procs": 312,
        }),
        "gpu": StaticSource({
            "name": "NVIDIA GeForce RTX 4090", "util": 64.0, "mem_util": 40.0, "temp": 71.0,
            "mem_used": int(12.1 * GB), "mem_total": 24 * GB, "power": 312.0, "fan": 45.0, "clock": 2520,
            "util_hist": [30 + (i % 10) * 6 for i in range(60)], "temp_hist": [60 + (i % 4) for i in range(60)],
            "power_hist": [200 + i for i in range(60)],
        }),
        "ping": StaticSource({"ms": 9, "hist": [8, 9, 12, 9, -1, 10], "host": "1.1.1.1"}),
        "bitaxe": StaticSource(bitaxe_state()),
        "ci": StaticSource(ci_state(now)),
        "vps": StaticSource(vps_state()),
        "bsod": StaticSource(bsod_state(now)),
        "news": StaticSource(news_state(now)),
    }


@pytest.fixture
def empty_sources():
    return {k: StaticSource({}, error="offline") for k in SOURCE_NAMES}


def _assert_key(img):
    assert isinstance(img, Image.Image)
    assert img.size == (gfx.KEY, gfx.KEY)
    assert img.mode == "RGB"


@pytest.mark.parametrize("name", sorted(TILES))
def test_tile_renders_with_data(cfg, sources, name):
    tile = make_tile(name, cfg, sources)
    _assert_key(tile.render(time.time()))
    span = tile.render_span(time.time())
    assert len(span) == tile.width
    for img in span:
        _assert_key(img)


@pytest.mark.parametrize("name", sorted(TILES))
def test_tile_renders_without_data(cfg, empty_sources, name):
    tile = make_tile(name, cfg, empty_sources)
    _assert_key(tile.render(time.time()))
    for img in tile.render_span(time.time()):
        _assert_key(img)


@pytest.mark.parametrize("name", sorted(TILES))
def test_zoom_views_are_full_deck(cfg, sources, empty_sources, name):
    for srcs in (sources, empty_sources):
        tile = make_tile(name, cfg, srcs)
        if not tile.zoomable:
            continue
        tile.render(time.time())
        tile.on_press()
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


# --- Phase 2 parsers -----------------------------------------------------------------

def test_axeos_parse_and_formats():
    st = bitaxe_state()
    assert st["hash_1m"] == pytest.approx(1074.61)
    assert st["pool"] == "public-pool.io"
    assert st["accepted"] == 6446 and st["rejected"] == 9
    assert fmt_hash(1074.61) == "1.07T"
    assert fmt_hash(812.0) == "812G"
    assert fmt_hash(12345.0) == "12.3T"
    assert fmt_diff(57040659706) == "57.0G"
    assert fmt_diff(53029720) == "53.0M"
    assert fmt_diff(812) == "812"


def test_ci_summary_statuses():
    runs = json.loads((FIX / "gh_runs.json").read_text())
    pulls = json.loads((FIX / "gh_pulls.json").read_text())
    r = summarize_repo("o/app", "app", runs, pulls)
    assert r["status"] == "fail" and r["workflow"] == "ci" and r["branch"] == "main"
    assert [p["number"] for p in r["prs"]] == [42, 41] and r["prs"][1]["draft"]
    assert r["at"] > 1.7e9
    assert summarize_repo("o/x", "x", [], [])["status"] == "none"
    running = summarize_repo("o/x", "x", [{"status": "in_progress"}], [])
    assert running["status"] == "running"
    assert summarize_repo("o/x", "x", [{"status": "completed", "conclusion": "cancelled"}], [])["status"] == "cancelled"
    assert normalize_repos(["a/b", {"repo": "c/d", "label": "dee"}, {"repo": "e/f"}]) == [
        {"repo": "a/b", "label": "b"}, {"repo": "c/d", "label": "dee"}, {"repo": "e/f", "label": "f"},
    ]


def test_bsod_events_merge_into_crashes():
    events = parse_events((FIX / "events.xml").read_text())
    assert len(events) == 5
    crashes = merge_crashes(events)
    assert [c["code"] for c in crashes] == [0xBE, 0, 0x3B]
    assert [c["kind"] for c in crashes] == ["bsod", "power", "bsod"]
    assert crashes[0]["name"] == "WR_RO_MEM" and crashes[2]["name"] == "SYSSVC"
    now = crashes[0]["time"] + 86400 * 3
    s = summarize(crashes, now, now - 1000, 30)
    assert s["in_window"] == 3 and s["bsod_in_window"] == 2
    assert s["since_last"] == pytest.approx(86400 * 3)
    assert s["last_bsod"]["code"] == 0xBE
    power_only = summarize([dict(crashes[1])], now, now - 1000, 30)
    assert power_only["last_bsod"] is None and power_only["since_bsod"] is None
    assert parse_events("") == []


def test_vps_probe_parse_and_states():
    out = parse_probe("fpv-sim-mcp active 200 0.000900\nsales-live inactive 000 0\nguild active - 0\n_load 0.10 0.20 0.30\n_up 12345.6\n")
    assert out["services"]["fpv-sim-mcp"] == {"active": "active", "local_code": "200", "local_ms": 0}
    assert out["load"] == [0.1, 0.2, 0.3] and out["uptime"] == 12345.6
    assert service_state("active", "200", None) == "ok"
    assert service_state("active", "-", None) == "ok"
    assert service_state("active", "502", 200) == "degraded"
    assert service_state("inactive", "200", 200) == "down"
    assert service_state(None, None, 302) == "ok"
    assert service_state(None, None, None) == "unknown"
    assert service_state(None, None, 503) == "down"


def test_news_parse_and_interleave():
    items = parse_feed("HN", (FIX / "rss.xml").read_bytes())
    assert [i["title"] for i in items] == ["First headline & some markup", "Second headline with odd spacing", "Third"]
    assert items[0]["at"] > 1.7e9 and items[2]["at"] == 0
    mixed = interleave([[1, 2, 3], ["a"], ["x", "y"]])
    assert mixed == [1, "a", "x", 2, "y", 3]


def test_news_strip_geometry_and_lookup():
    items = [
        {"source": "HN", "title": "alpha beta gamma delta epsilon zeta eta theta iota", "url": "u1", "at": 0},
        {"source": "BBC", "title": "kappa lambda mu nu xi omicron pi rho sigma tau", "url": "u2", "at": 0},
    ]
    strip = Strip(items, {"HN": (255, 102, 0)}, view_w=456)
    assert strip.ranges[0][1] > 456  # long enough that the window centre starts inside headline 0
    assert strip.img.size == (strip.content_w + 456, gfx.KEY)
    assert strip.cycle == strip.content_w
    assert strip.frame(0).size == (456, gfx.KEY)
    assert strip.frame(strip.cycle * 7 + 3).size == (456, gfx.KEY)
    # The window wraps seamlessly: the frame just past the end equals the frame at the start.
    assert strip.frame(strip.cycle).tobytes() == strip.frame(0).tobytes()
    assert strip.index_at(0) == 0
    assert strip.index_at(strip.ranges[0][1] - 228 + 1) == 1  # window centre just past headline 0
    assert strip.index_at(strip.cycle * 3) == 0


def test_wrap_text_fits():
    lines = wrap_text("Make the range build reproducible on a fresh worktree", 64, 10, max_lines=3)
    assert 1 < len(lines) <= 3
    assert all(gfx.text_width(line, 10, "semibold") <= 64 for line in lines)
    assert lines[-1].endswith("…")
    assert wrap_text("Supercalifragilisticexpialidocious", 40, 10, max_lines=5)[0].endswith("-")


# --- device, canvas, app -------------------------------------------------------------

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
    assert deck.sent == 15  # 10 single tiles + the 5-key ticker
    assert len(app.tiles) == 11
    app._on_press(3, True)  # gpu tile
    app.tick()
    assert app.zoom is not None and app.zoom.name == "gpu"
    app._on_press(0, True)
    app.tick()
    assert app.zoom is None
    app.stop()


def test_wide_tile_spans_and_opens_headline(cfg, sources, tmp_path, monkeypatch):
    deck = SimDeck(gap=24, out_dir=tmp_path, interval=1e9)
    app = App(cfg, deck, sources=sources)
    news = app.slots[10]
    assert news is app.slots[14] and news.slot == 10 and news.span == 5
    opened = []
    monkeypatch.setattr("deckdash.tiles.news.webbrowser.open", lambda url: opened.append(url))
    app.start()
    app.tick()
    app._on_press(13, True)  # any ticker key zooms the ticker
    app.tick()
    assert app.zoom is news and len(news.zoom_items) == 5
    app._on_press(7, True)  # column 2 -> third headline in the zoom
    app.tick()
    assert app.zoom is None
    assert opened == [news.zoom_items[2]["url"]]
    app.stop()


def test_layout_with_partial_span(sources, tmp_path):
    cfg = config.load(ROOT / "config.toml", local=ROOT / "tests" / "no-such-local.toml")
    cfg["layout"]["keys"] = ["news", "news", "clock", "", "news"]
    deck = SimDeck(gap=24, out_dir=tmp_path, interval=1e9)
    app = App(cfg, deck, sources=sources)
    assert app.slots[0] is app.slots[1] and app.slots[0].span == 2
    assert app.slots[2].name == "clock" and app.slots[3] is None
    assert app.slots[4].name == "news" and app.slots[4] is not app.slots[0] and app.slots[4].span == 1
    app.start()
    app.tick()
    assert deck.sent == 15
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


def test_local_override_keeps_arrays_of_tables(tmp_path):
    local = tmp_path / "config.local.toml"
    local.write_text('[vps]\nssh_host = "box"\n\n[[vps.services]]\nname = "a"\nport = 1\n\n[[vps.services]]\nname = "b"\nport = 2\n', encoding="utf-8")
    config.write_local({"weather": {"latitude": 1.5, "longitude": 2.5, "place": "X"}}, local=local)
    cfg = config.load(ROOT / "config.toml", local=local)
    assert cfg["vps"]["ssh_host"] == "box"
    assert [s["name"] for s in cfg["vps"]["services"]] == ["a", "b"]
    assert cfg["weather"]["place"] == "X"


def test_fmt_helpers():
    assert gfx.fmt_rate(12.4 * (1 << 20)) == "12M"
    assert gfx.fmt_rate(1.2 * (1 << 20)) == "1.2M"
    assert gfx.fmt_rate(830 * 1024) == "830K"
    assert gfx.fmt_duration(90000) == "1d 1h"
    assert gfx.fit_size("12345678901234567890", 40, 20) < 20
