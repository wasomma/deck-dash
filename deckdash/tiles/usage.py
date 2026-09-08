"""Claude usage: the context window and each plan limit as a bar.

The context window comes from ``claude_usage`` (the session transcript); the limit windows from
``claude_limits`` (the usage endpoint), which is also the only source of the per-model weekly.
How many rows there are therefore depends on the plan, so the key divides its space by the row
count rather than assuming three.
"""

from __future__ import annotations

from PIL import ImageDraw

from ..canvas import Canvas
from ..gfx import DIM, FG, PURPLE, TRACK, fit_size, fmt_duration, heat, hbar, new_key, text
from .base import Tile

ZOOM_LABEL = {"CTX": "CONTEXT", "5H": "5-HOUR", "WK": "WEEKLY"}  # the key is cramped; the zoom is not


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

    def _meters(self) -> list[dict]:
        """CTX first, then whatever windows the API returned - there may be a per-model one."""
        st = self.sources["claude_usage"].state
        rows = [{"label": "CTX", "pct": st.get("ctx_pct"), "resets_at": 0.0}]
        limits = self.sources["claude_limits"].state
        got = limits.get("buckets") or []
        rows += [{"label": b["label"], "pct": b["pct"], "resets_at": b.get("resets_at", 0.0),
                  "model": str(b.get("key", "")).startswith("model:"), "critical": b.get("critical", False)}
                 for b in got]
        if not got:  # nothing signed in yet: keep the two rows, dashed, so the key keeps its shape
            rows += [{"label": "5H", "pct": None, "resets_at": 0.0}, {"label": "WK", "pct": None, "resets_at": 0.0}]
        return rows

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

        meters = self._meters()
        stale = self._stale(st)
        known = [m["pct"] for m in meters if m["pct"] is not None]
        if known and not stale:
            worst = max(known)
            shown = f"{worst:.0f}%"
            # 100% is a digit wider than any other reading and would touch the CLAUDE label.
            text(d, (68, 8), shown, fit_size(shown, 28, 13), heat(worst / 100), anchor="rm")

        # Label and percentage on one line, the bar full width underneath: at arm's length the
        # bar length is what reads, so it gets the whole key rather than the gap between two texts.
        # The row count varies - a per-model window appears only on some plans - so the spacing
        # is divided out of the space below the header rather than fixed at three rows.
        step = min(16, 50 // max(1, len(meters)))
        small = step < 14
        for i, m in enumerate(meters):
            y = 20 + i * step
            value = m["pct"]
            text(d, (4, y), m["label"], 8 if small else 9, DIM, anchor="lm")
            frac = 0.0 if value is None else value / 100
            colour = TRACK if (value is None or stale) else heat(frac)
            shown = "-" if value is None else f"{value:.0f}%"
            text(d, (68, y), shown, 9 if small else 10, DIM if (value is None or stale) else FG, anchor="rm")
            top = y + (4 if small else 6)
            hbar(d, (4, top, 68, top + (4 if small else 5)), frac, colour)

        if stale:
            text(d, (68, 8), _age(st.get("age", 0.0)), 8, DIM, anchor="rm")
        return img

    def _row(self, c, row: int, group: list[dict], now: float, stale: bool) -> None:
        """One zoom row: a single window across the whole row, or two sharing it two keys each.

        Sharing beats stacking here - a window squeezed onto one spare key was how the Fable
        limit first appeared, and it was the smallest thing on a screen it was the reason for."""
        base = row * 5
        pairs = [(0, 1, 0, 1), (3, 4, 3, 4)] if len(group) > 1 else [(0, 2, 0, 4)]
        for m, (label_key, value_key, bar_a, bar_b) in zip(group, pairs):
            value = m["pct"]
            frac = 0.0 if value is None else value / 100
            colour = TRACK if (value is None or stale) else heat(frac)
            x0, _, x1, _ = c.span_box(base + bar_a, base + bar_b)
            _, _, _, y1 = c.rows_box(row, row)
            hbar(c.draw, (x0 + 4, y1 - 12, x1 - 4, y1 - 5), frac, colour)
            label = ZOOM_LABEL.get(m["label"], m["label"])
            c.key_text(base + label_key, label, 13, DIM, where="t", pad=8, weight="semibold")
            reset = _resets(now, m["resets_at"])
            if reset:
                c.key_text(base + label_key, reset, 10, DIM, where="t", pad=26, weight="semibold")
            c.key_text(base + value_key, "-" if value is None else f"{value:.0f}%",
                       28 if len(group) > 1 else 30, FG if not stale else DIM, dy=-6)

    def render_zoom(self, now):
        st = self.sources["claude_usage"].state
        c = Canvas(self.gap)
        if not st or st.get("source", "none") == "none":
            c.key_text(7, "no sessions", 14, DIM)
            c.key_text(12, "see docs/claude-hooks.md", 9, DIM, weight="semibold")
            return c.slice()

        stale = self._stale(st)
        meters = self._meters()

        # Row 0 is the context window and its detail. The limit windows split by kind rather than
        # by arrival order: the account-wide ones share row 1, the per-model ones row 2, so a
        # per-model limit gets a row of its own instead of whatever key happened to be spare.
        limits = meters[1:]
        wide = [m for m in limits if not m.get("model")]
        scoped = [m for m in limits if m.get("model")]
        if not scoped:            # no per-model window: one limit per row, as before
            wide, scoped = wide[:1], wide[1:2]
        self._row(c, 0, [meters[0]], now, stale)
        if wide:
            self._row(c, 1, wide[:2], now, stale)
        if scoped:
            self._row(c, 2, scoped[:2], now, stale)
        if not limits:
            c.key_text(12, "claude auth login", 12, DIM, dy=-6, weight="semibold")

        ctx_size = st.get("ctx_size") or 0.0
        if ctx_size:
            used = (st.get("ctx_in") or 0.0) + (st.get("ctx_out") or 0.0)
            c.key_text(3, f"{_tokens(used)} / {_tokens(ctx_size)}", 12, DIM, dy=-6, weight="semibold")
        model = st.get("model") or ""
        if model:
            c.key_text(4, model, fit_size(model, 58, 12, weight="semibold"), DIM, dy=-6, weight="semibold")

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
