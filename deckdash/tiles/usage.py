"""Claude usage: context window, the 5-hour limit and the weekly all-models limit as three bars.

The numbers come from Claude Code's statusLine command (``tools/claude_status.py``). There is no
weekly Fable bar because that number is not in the statusLine payload; the zoom view says so
rather than leaving a silent gap.
"""

from __future__ import annotations

from PIL import ImageDraw

from ..canvas import Canvas
from ..gfx import DIM, FG, PURPLE, TRACK, fit_size, fmt_duration, heat, hbar, new_key, text
from .base import Tile

ROWS = (("CTX", "ctx_pct"), ("5H", "five_pct"), ("WK", "week_pct"))
ZOOM_ROWS = (("CONTEXT", "ctx_pct"), ("5-HOUR", "five_pct"), ("WEEKLY", "week_pct"))


def _tokens(n: float) -> str:
    for suffix, div in (("M", 1_000_000), ("k", 1000)):
        if n >= div:
            v = n / div
            return f"{v:.0f}{suffix}" if v >= 10 else f"{v:.1f}{suffix}"
    return f"{n:.0f}"


def _age(seconds: float) -> str:
    """Coarsest unit only: "2h old" clears the CLAUDE label on a 72 px key, "2h 0m old" does not."""
    return fmt_duration(seconds).split(" ")[0] + " old"


def _resets(now: float, at: float) -> str:
    if not at or at <= now:
        return ""
    return f"resets {fmt_duration(at - now)}"


class UsageTile(Tile):
    name = "usage"
    zoomable = True
    refresh = 2.0
    zoom_refresh = 2.0

    def __init__(self, cfg, sources):
        super().__init__(cfg, sources)
        self.stale_s = float(cfg.get("claude_usage", {}).get("stale_minutes", 30)) * 60

    def _meters(self, st: dict) -> list[tuple[str, float | None]]:
        return [(label, st.get(key)) for label, key in ROWS]

    def _stale(self, st: dict) -> bool:
        return float(st.get("age", 0.0)) > self.stale_s

    def render(self, now):
        src = self.sources["claude_usage"]
        st = src.state
        img = new_key()
        d = ImageDraw.Draw(img)
        text(d, (5, 8), "CLAUDE", 8, PURPLE, anchor="lm")

        if not st:
            return self.placeholder("CLAUDE", src.error or "loading")
        if st.get("source", "none") == "none":
            text(d, (36, 38), "no sessions", 10, DIM)
            text(d, (36, 52), "see docs", 8, DIM, weight="regular")
            self.refresh = 5.0
            return img

        meters = self._meters(st)
        stale = self._stale(st)
        known = [v for _, v in meters if v is not None]
        if known and not stale:
            worst = max(known)
            text(d, (68, 8), f"{worst:.0f}%", 13, heat(worst / 100), anchor="rm")

        # Label and percentage on one line, the bar full width underneath: at arm's length the
        # bar length is what reads, so it gets the whole key rather than the gap between two texts.
        for i, (label, value) in enumerate(meters):
            y = 22 + i * 16
            text(d, (4, y), label, 9, DIM, anchor="lm")
            frac = 0.0 if value is None else value / 100
            colour = TRACK if (value is None or stale) else heat(frac)
            shown = "-" if value is None else f"{value:.0f}%"
            text(d, (68, y), shown, 10, DIM if (value is None or stale) else FG, anchor="rm")
            hbar(d, (4, y + 6, 68, y + 11), frac, colour)

        if stale:
            text(d, (68, 8), _age(st.get("age", 0.0)), 8, DIM, anchor="rm")
        return img

    def render_zoom(self, now):
        st = self.sources["claude_usage"].state
        c = Canvas(self.gap)
        if not st or st.get("source", "none") == "none":
            c.key_text(7, "no sessions", 14, DIM)
            c.key_text(12, "see docs/claude-hooks.md", 9, DIM, weight="semibold")
            return c.slice()

        stale = self._stale(st)
        for row, (label, key) in enumerate(ZOOM_ROWS):
            value = st.get(key)
            frac = 0.0 if value is None else value / 100
            colour = TRACK if (value is None or stale) else heat(frac)
            x0, y0, x1, y1 = c.rows_box(row, row)
            hbar(c.draw, (x0 + 4, y1 - 12, x1 - 4, y1 - 5), frac, colour)
            c.key_text(row * 5, label, 13, DIM, where="t", pad=8, weight="semibold")
            c.key_text(row * 5 + 2, "-" if value is None else f"{value:.0f}%", 30, FG if not stale else DIM, dy=-6)

        ctx_size = st.get("ctx_size") or 0.0
        if ctx_size:
            used = (st.get("ctx_in") or 0.0) + (st.get("ctx_out") or 0.0)
            c.key_text(3, f"{_tokens(used)} / {_tokens(ctx_size)}", 12, DIM, dy=-6, weight="semibold")
        model = st.get("model") or ""
        if model:
            c.key_text(4, model, fit_size(model, 58, 12, weight="semibold"), DIM, dy=-6, weight="semibold")

        c.key_text(9, _resets(now, st.get("five_reset") or 0.0), 11, DIM, dy=-6, weight="semibold")
        if st.get("week_pct") is None:  # Desktop never runs statusLine, so the limits need the API
            c.key_text(13, "run: claude auth", 11, DIM, dy=-6, weight="semibold")
        c.key_text(14, _resets(now, st.get("week_reset") or 0.0), 11, DIM, dy=-6, weight="semibold")

        # Name the session the key is reporting, and say how many others are live: with several
        # sessions running, an unlabelled percentage does not say which one it belongs to.
        project = st.get("project") or ""
        others = max(0, len(st.get("sessions") or []) - 1)
        if project:
            c.key_text(1, project, fit_size(project, 58, 12, weight="semibold"), DIM, dy=-14, weight="semibold")
        if others:
            c.key_text(1, f"+{others} more session" + ("s" if others > 1 else ""), 9, DIM, dy=2, weight="semibold")
        if stale:
            c.key_text(6, _age(st.get("age", 0.0)), 12, DIM, dy=-6, weight="semibold")
        return c.slice()
