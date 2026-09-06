from __future__ import annotations

import math

from PIL import ImageDraw

from ..canvas import Canvas
from ..gfx import AMBER, BLUE, DIM, FG, GREEN, PURPLE, RED, TRACK, fit_size, fmt_duration, lerp_color, new_key, text
from .base import Tile

STATUS_COLOR = {
    "ok": GREEN,
    "fail": RED,
    "running": AMBER,
    "cancelled": DIM,
    "none": TRACK,
    "error": PURPLE,
}
STATUS_WORD = {
    "ok": "passed",
    "fail": "FAILED",
    "running": "running",
    "cancelled": "cancelled",
    "none": "no runs",
    "error": "gh error",
}


def _pulse(t: float) -> float:
    return 0.5 + 0.5 * math.sin(t * 4.0)


def _age(now: float, at: float) -> str:
    if not at:
        return ""
    return fmt_duration(max(0.0, now - at)).split(" ")[0]


def _spotlight(repos: list[dict]) -> dict | None:
    """The repo worth a closer look: a failure, else a running job, else the most recent run."""
    if not repos:
        return None
    for status in ("fail", "running"):
        hits = [r for r in repos if r["status"] == status]
        if hits:
            return max(hits, key=lambda r: r["at"])
    return max(repos, key=lambda r: r["at"])


def wrap_text(s: str, max_w: int, size: int, weight: str = "semibold", max_lines: int = 2) -> list[str]:
    from ..gfx import text_width

    words = s.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if text_width(trial, size, weight) <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = w
        while text_width(cur, size, weight) > max_w and len(cur) > 1:
            head = cur
            while text_width(head + "-", size, weight) > max_w and len(head) > 1:
                head = head[:-1]
            lines.append(head + "-")
            cur = cur[len(head):]
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while text_width(last + "…", size, weight) > max_w and len(last) > 1:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


class CiTile(Tile):
    name = "ci"
    zoomable = True
    zoom_refresh = 0.5

    def __init__(self, cfg, sources):
        super().__init__(cfg, sources)
        self.anim = 1.0 / float(cfg.get("deck", {}).get("anim_fps", 8))

    def _dot_color(self, status: str, t: float):
        col = STATUS_COLOR.get(status, TRACK)
        if status == "running":
            return lerp_color((90, 64, 10), AMBER, _pulse(t))
        return col

    def render(self, now):
        src = self.sources["ci"]
        s = src.state
        if not s:
            return self.placeholder("CI", src.error or "loading")
        repos = s["repos"]
        self.refresh = self.anim if s.get("running") else 1.0
        img = new_key()
        d = ImageDraw.Draw(img)
        text(d, (5, 8), "CI", 8, DIM, anchor="lm")
        n_pr = len(s.get("prs") or [])
        if n_pr:
            text(d, (67, 8), f"{n_pr} PR", 8, BLUE, anchor="rm")
        elif s.get("failed"):
            text(d, (67, 8), f"{s['failed']} fail", 8, RED, anchor="rm")
        xs = (13, 36, 59)
        ys = (26, 52)
        for i, r in enumerate(repos[:6]):
            cx, cy = xs[i % 3], ys[i // 3]
            col = self._dot_color(r["status"], now)
            rad = 6 if r["status"] != "running" else 5 + _pulse(now) * 2
            d.ellipse([cx - rad, cy - 5 - rad, cx + rad, cy - 5 + rad], fill=col)
            if r["status"] == "fail":
                d.ellipse([cx - rad - 2, cy - 7 - rad, cx + rad + 2, cy - 3 + rad], outline=RED, width=1)
            text(d, (cx, cy + 9), r["label"], 8, DIM if r["status"] != "fail" else RED, weight="semibold")
        return img

    def render_zoom(self, now):
        """Keys 0-2/5-7: one card per repo. Keys 3-4/8-9: the run worth looking at. Row 2: open PRs."""
        src = self.sources["ci"]
        s = src.state
        c = Canvas(self.gap)
        d = c.draw
        if not s:
            c.key_text(7, "no data", 16, DIM)
            c.key_text(12, (src.error or "")[:14], 9, DIM, weight="semibold")
            return c.slice()
        repos = s["repos"]
        cards = (0, 1, 2, 5, 6, 7)
        for key, r in zip(cards, repos[:6]):
            x0, y0, x1, y1 = c.key_box(key)
            cx, cy = (x0 + x1) / 2, y0 + 38
            col = self._dot_color(r["status"], now)
            c.key_text(key, r["label"], 12, FG, where="t", pad=4, weight="semibold")
            rad = 13 if r["status"] != "running" else 11 + _pulse(now) * 3
            d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=col)
            if r["status"] == "fail":
                d.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], outline=RED, width=2)
            foot = _age(now, r["at"]) if r["status"] not in ("none", "error") else STATUS_WORD[r["status"]]
            if r["prs"]:
                foot = f"{foot} · {len(r['prs'])}PR" if foot else f"{len(r['prs'])} PR"
            c.key_text(key, foot, 10, DIM if r["status"] != "fail" else RED, where="b", pad=4, weight="semibold")
        sp = _spotlight(repos)
        bx0, by0, bx1, by1 = c.span_box(3, 9)
        if sp is not None:
            col = STATUS_COLOR.get(sp["status"], TRACK)
            d.rounded_rectangle((bx0, by0, bx1 - 1, by1 - 1), radius=8, outline=col, width=2)
            c.key_text(3, "LATEST", 10, DIM, where="t", pad=6)
            c.key_text(3, sp["label"], 20, FG, dy=8)
            c.key_text(4, STATUS_WORD.get(sp["status"], sp["status"]), 15, col, where="t", pad=10)
            c.key_text(4, _age(now, sp["at"]) + (" ago" if sp["at"] else ""), 11, DIM, where="b", pad=8, weight="semibold")
            wf = sp["workflow"] or sp["title"] or "-"
            lines = wrap_text(wf, 62, 11, max_lines=3)
            for j, line in enumerate(lines):
                c.key_text(8, line, 11, FG, where="t", pad=6 + 14 * j, weight="semibold")
            c.key_text(9, sp["branch"][:14] if sp["branch"] else "", 12, BLUE, where="t", pad=8, weight="semibold")
            c.key_text(9, sp["event"] or "", 10, DIM, where="b", pad=8, weight="semibold")
        prs = s.get("prs") or []
        if not prs:
            c.key_text(12, "no open PRs", 12, DIM, weight="semibold")
            c.key_text(11, f"{len(repos)} repos", 11, DIM, where="c", weight="semibold")
            c.key_text(13, "checked " + _age(now, s.get("checked", 0)) + " ago", 9, DIM, weight="semibold")
        for i, p in enumerate(prs[:5]):
            key = 10 + i
            c.key_text(key, f"#{p['number']}", 11, BLUE, where="tl", pad=4, weight="semibold")
            c.key_text(key, p["repo"], 9, DIM, where="tr", pad=4, weight="semibold")
            for j, line in enumerate(wrap_text(p["title"], 64, 10, max_lines=3)):
                c.key_text(key, line, 10, FG if not p["draft"] else DIM, where="t", pad=20 + 13 * j, weight="semibold")
        return c.slice()
