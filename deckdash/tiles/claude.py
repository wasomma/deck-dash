from __future__ import annotations

import math

from PIL import ImageDraw

from ..canvas import Canvas
from ..gfx import AMBER, BLUE, DIM, FG, GREEN, PURPLE, TRACK, fit_size, fmt_duration, lerp_color, new_key, text
from .base import Tile
from .ci import wrap_text

STATE_COLOR = {"busy": BLUE, "waiting": AMBER, "idle": GREEN}
STATE_WORD = {"busy": "working", "waiting": "needs you", "idle": "done"}


def _age(now: float, t: float) -> str:
    return fmt_duration(max(0.0, now - t)).split(" ")[0] if t else ""


class ClaudeTile(Tile):
    """One dot per live Claude Code session; amber pulses while one waits for Wes."""

    name = "claude"
    zoomable = True
    zoom_refresh = 0.5

    def __init__(self, cfg, sources):
        super().__init__(cfg, sources)
        self.anim = 1.0 / float(cfg.get("deck", {}).get("anim_fps", 8))
        self.show_s = float(cfg.get("claude", {}).get("show_minutes", 15)) * 60

    def active(self, now: float) -> bool:
        """Overlay its key while a session works, waits, or finished recently."""
        st = self.sources["claude"].state
        for s in st.get("sessions", []):
            if s["state"] in ("busy", "waiting") or now - float(s.get("last", s["since"])) < self.show_s:
                return True
        return False

    def _dot(self, s: dict, now: float):
        col = STATE_COLOR.get(s["state"], TRACK)
        if s["state"] == "waiting" and s["since"] > s.get("seen", 0.0):
            return lerp_color((80, 50, 10), col, 0.5 + 0.5 * math.sin(now * 7))
        if s["state"] == "busy":
            return lerp_color((20, 40, 80), col, 0.6 + 0.4 * math.sin(now * 2.5))
        return col

    def render(self, now):
        src = self.sources["claude"]
        st = src.state
        img = new_key()
        d = ImageDraw.Draw(img)
        text(d, (5, 8), "CLAUDE", 8, PURPLE, anchor="lm")
        if not st:
            text(d, (36, 36), "hooks off" if (src.error or "").startswith("hooks") else "loading", 10, DIM)
            text(d, (36, 50), "see docs", 8, DIM, weight="regular")
            self.refresh = 5.0
            return img
        sessions = st.get("sessions", [])
        self.refresh = self.anim if any(s["state"] in ("busy", "waiting") for s in sessions) else 2.0
        if not sessions:
            text(d, (36, 36), "no sessions", 10, DIM)
            return img
        n = min(len(sessions), 8)
        cols = 4 if n > 3 else n
        for i, s in enumerate(sessions[:8]):
            r, cidx = divmod(i, cols)
            cx = 36 + (cidx - (cols - 1) / 2) * 16
            cy = 26 + r * 18
            col = self._dot(s, now)
            d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=col)
        waiting, busy = st.get("waiting", 0), st.get("busy", 0)
        if waiting:
            foot, fcol = f"{waiting} waiting", AMBER
        elif busy:
            foot, fcol = f"{busy} working", BLUE
        else:
            foot, fcol = f"{len(sessions)} idle", DIM
        text(d, (36, 62), foot, 9, fcol, weight="semibold")
        top = sessions[0]
        text(d, (36, 50), top["project"], fit_size(top["project"], 64, 9, weight="semibold"), DIM, weight="semibold")
        return img

    def on_press(self) -> None:
        src = self.sources["claude"]
        ack = getattr(src, "ack", None)
        if callable(ack):
            ack()

    def render_zoom(self, now):
        """One key per session: project, state, age, and the last message wrapped below."""
        st = self.sources["claude"].state
        c = Canvas(self.gap)
        sessions = (st.get("sessions") or [])[:15]
        if not sessions:
            c.key_text(7, "no sessions", 13, DIM)
            c.key_text(12, "hooks: docs/claude-hooks.md" if not st else "", 8, DIM, weight="semibold")
            return c.slice()
        for i, s in enumerate(sessions):
            col = STATE_COLOR.get(s["state"], TRACK)
            x0, y0, x1, y1 = c.key_box(i)
            c.draw.rectangle((x0, y0, x1 - 1, y0 + 3), fill=col)
            c.key_text(i, s["project"], fit_size(s["project"], 62, 12, weight="semibold"), FG, where="t", pad=7, weight="semibold")
            c.key_text(i, STATE_WORD.get(s["state"], s["state"]), 11, col, where="t", pad=22, weight="semibold")
            c.key_text(i, _age(now, s["since"]), 10, DIM, where="tr", pad=36, weight="semibold")
            for j, line in enumerate(wrap_text(s.get("message", ""), 58, 9, max_lines=2)):
                c.key_text(i, line, 9, DIM, where="t", pad=48 + 11 * j, weight="semibold")
        return c.slice()
