from __future__ import annotations

from PIL import ImageDraw

from ..canvas import Canvas
from ..gfx import AMBER, DIM, FG, GREEN, RED, TRACK, fit_size, fmt_duration, new_key, sparkline, text
from .base import Tile

STATE_COLOR = {"ok": GREEN, "degraded": AMBER, "down": RED, "unknown": TRACK}


class VpsTile(Tile):
    name = "vps"
    zoomable = True
    refresh = 1.0
    zoom_refresh = 1.0

    def __init__(self, cfg, sources):
        super().__init__(cfg, sources)
        v = cfg.get("vps", {})
        self.labels = {s["name"]: s.get("label", s["name"]) for s in v.get("services", [])}

    def render(self, now):
        src = self.sources["vps"]
        s = src.state
        if not s:
            return self.placeholder("VPS", src.error or "loading")
        img = new_key()
        d = ImageDraw.Draw(img)
        text(d, (5, 8), "VPS", 8, DIM, anchor="lm")
        if not s.get("ssh_ok"):
            text(d, (67, 8), "ssh?", 8, AMBER, anchor="rm")
        elif s.get("load"):
            text(d, (67, 8), f"load {s['load'][0]:.2f}", 8, DIM, anchor="rm")
        rows = s["services"][:3]
        for i, r in enumerate(rows):
            y = 24 + i * 18
            col = STATE_COLOR.get(r["state"], TRACK)
            d.ellipse([6, y - 5, 16, y + 5], fill=col)
            label = self.labels.get(r["name"], r["name"])
            text(d, (20, y), label, fit_size(label, 24, 10, weight="semibold"), FG, weight="semibold", anchor="lm")
            ms = r.get("ms")
            text(d, (67, y), f"{ms}ms" if ms is not None else "-", 8, DIM if r["state"] == "ok" else col, weight="semibold", anchor="rm")
        return img

    def render_zoom(self, now):
        """One row per service: name and port, state, edge latency, local check, 30 min latency line."""
        src = self.sources["vps"]
        s = src.state
        c = Canvas(self.gap)
        d = c.draw
        if not s:
            c.key_text(7, "no data", 16, DIM)
            c.key_text(12, (src.error or "")[:14], 9, DIM, weight="semibold")
            return c.slice()
        rows = s["services"][:3]
        for i, r in enumerate(rows):
            k = i * 5
            col = STATE_COLOR.get(r["state"], TRACK)
            label = self.labels.get(r["name"], r["name"])
            c.key_text(k, label, 16, FG, where="t", pad=10)
            c.key_text(k, f":{r['port']}" if r.get("port") else "", 11, DIM, where="b", pad=10, weight="semibold")
            x0, y0, x1, y1 = c.key_box(k + 1)
            d.rounded_rectangle((x0 + 6, y0 + 8, x1 - 6, y0 + 34), radius=6, fill=col)
            text(d, ((x0 + x1) / 2, y0 + 21), r["state"], 13, (10, 12, 16) if r["state"] != "unknown" else DIM)
            unit = r.get("active") or ("no ssh" if not s.get("ssh_ok") else "")
            c.key_text(k + 1, unit, 11, DIM, where="b", pad=8, weight="semibold")
            pm = r.get("public_ms")
            c.key_text(k + 2, f"{pm}ms" if pm is not None else "-", 20, FG if pm is not None else DIM, where="t", pad=10)
            code = r.get("public_code")
            c.key_text(k + 2, f"edge {code}" if code else "edge", 10, DIM, where="b", pad=8, weight="semibold")
            lx0, ly0, lx1, ly1 = c.span_box(k + 3, k + 4)
            d.rectangle((lx0, ly0, lx1, ly1), fill=(12, 14, 20))
            hist = [h for h in (r.get("hist") or []) if h >= 0]
            sparkline(d, (lx0 + 4, ly0 + 22, lx1 - 4, ly1 - 4), [h if h >= 0 else 0 for h in (r.get("hist") or [])], col, vmax=max(100, max(hist or [0])), width=2)
            lm = r.get("local_ms")
            lc = r.get("local_code")
            local = f"local {lm}ms" + (f" · {lc}" if lc and lc != "-" else "") if lm is not None else "local -"
            c.key_text(k + 3, local, 10, DIM, where="tl", pad=5, weight="semibold")
            c.key_text(k + 4, "30 min", 10, DIM, where="tr", pad=5, weight="semibold")
        for i in range(len(rows), 3):
            k = i * 5
            if i == len(rows):
                info = []
                if s.get("uptime"):
                    info.append(f"box up {fmt_duration(s['uptime'])}")
                if s.get("ssh_error"):
                    info.append(str(s["ssh_error"])[:20])
                for j, line in enumerate(info[:2]):
                    c.key_text(k + j, line, 10, DIM, weight="semibold")
        return c.slice()
