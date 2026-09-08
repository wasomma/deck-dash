"""Alerts: watch the sources for events worth interrupting for, render the 5 s toast, badge the tile.

Events: a CI run fails (and passes again), the miner drops off the LAN (and returns) or sets
a new best difficulty, a VPS service goes down (and comes back), a new bugcheck appears in
the System log (persisted across restarts, so a crash shows up the moment the PC is back),
a Claude session needs input.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from .canvas import Canvas
from .gfx import AMBER, BG, DIM, FG, GREEN, PURPLE, RED, fit_size, fmt_duration, lerp_color, text
from .tiles.ci import wrap_text

GOLD = (255, 214, 90)


@dataclass
class Alert:
    kind: str                   # CI, MINER, VPS, BSOD, CLAUDE
    title: str                  # FAILED, OFFLINE, NEEDS YOU ...
    subject: str                # app, bitaxe, mcp ...
    detail: str = ""
    color: tuple = RED
    tile: str = ""              # tile that carries the badge afterwards
    style: str = "alert"        # alert | good | record
    at: float = field(default_factory=time.time)


class AlertWatcher:
    def __init__(self, cfg: dict, sources: dict, state_path: Path | str | None = None):
        self.cfg = cfg
        self.sources = sources
        self.state_path = Path(state_path) if state_path else None
        self.persist = {"bsod_last": 0.0, "bitaxe_best": 0.0}
        if self.state_path and self.state_path.exists():
            try:
                self.persist.update(json.loads(self.state_path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass
        self.ci_prev: dict[str, str] = {}
        self.vps_prev: dict[str, str] = {}
        self.bitaxe_online: bool | None = None
        self.claude_seen: dict[str, float] = {}
        self.usage_over: dict[str, bool] = {}

    def _save(self) -> None:
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.persist), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    def _src(self, name: str):
        return self.sources.get(name)

    def check(self, now: float) -> list[Alert]:
        out: list[Alert] = []
        out += self._check_ci()
        out += self._check_bitaxe(now)
        out += self._check_vps()
        out += self._check_bsod(now)
        out += self._check_claude()
        out += self._check_claude_usage()
        return out

    def _check_ci(self) -> list[Alert]:
        src = self._src("ci")
        if src is None:
            return []
        out = []
        for r in src.state.get("repos", []):
            status, key = r["status"], r["repo"]
            if status == "error":
                continue
            prev = self.ci_prev.get(key)
            if prev is not None and status != prev:
                what = r.get("workflow") or r.get("title") or ""
                where = f"{what} on {r['branch']}" if r.get("branch") else what
                if status == "fail":
                    out.append(Alert("CI", "FAILED", r["label"], where, RED, "ci"))
                elif prev == "fail" and status == "ok":
                    out.append(Alert("CI", "PASSED", r["label"], where, GREEN, "ci", "good"))
            self.ci_prev[key] = status
        return out

    def _check_bitaxe(self, now: float) -> list[Alert]:
        src = self._src("bitaxe")
        if src is None:
            return []
        out = []
        online = src.last_ok > 0 and now - src.last_ok < 45
        if self.bitaxe_online is None:
            if online:
                self.bitaxe_online = True
        elif online != self.bitaxe_online:
            self.bitaxe_online = online
            if online:
                from .sources.bitaxe import fmt_hash

                out.append(Alert("MINER", "BACK", fmt_hash(src.state.get("hash_1m", 0.0)), "bitaxe answers again", GREEN, "bitaxe", "good"))
            else:
                out.append(Alert("MINER", "OFFLINE", "bitaxe", src.error or "no answer for 45 s", RED, "bitaxe"))
        best = float(src.state.get("best", 0.0) or 0.0)
        if best > 0:
            prev = float(self.persist.get("bitaxe_best", 0.0))
            if prev > 0 and best > prev:
                from .sources.bitaxe import fmt_diff

                out.append(Alert("MINER", "NEW BEST", fmt_diff(best), f"previous record {fmt_diff(prev)}", GOLD, "bitaxe", "record"))
            if best != prev:
                self.persist["bitaxe_best"] = best
                self._save()
        return out

    def _check_vps(self) -> list[Alert]:
        src = self._src("vps")
        if src is None:
            return []
        out = []
        labels = {s["name"]: s.get("label", s["name"]) for s in self.cfg.get("vps", {}).get("services", [])}
        for s in src.state.get("services", []):
            state, name = s["state"], s["name"]
            if state == "unknown":
                continue
            prev = self.vps_prev.get(name)
            if prev is not None and state != prev:
                label = labels.get(name, name)
                if state in ("down", "degraded") and prev == "ok":
                    out.append(Alert("VPS", state.upper(), label, f"unit {s.get('active') or '?'} · http {s.get('local_code') or s.get('public_code') or '-'}", RED if state == "down" else AMBER, "vps"))
                elif state == "ok" and prev in ("down", "degraded"):
                    out.append(Alert("VPS", "UP", label, "service answers again", GREEN, "vps", "good"))
            self.vps_prev[name] = state
        return out

    def _check_bsod(self, now: float) -> list[Alert]:
        src = self._src("bsod")
        if src is None:
            return []
        last = src.state.get("last")
        if not last:
            return []
        seen = float(self.persist.get("bsod_last", 0.0))
        if last["time"] <= seen:
            return []
        self.persist["bsod_last"] = last["time"]
        self._save()
        if seen == 0.0:
            return []  # first run ever: do not replay history
        kind = "BUGCHECK" if last["kind"] == "bsod" else "POWER LOSS"
        subject = f"0x{last['code']:X}" if last["code"] else "reboot"
        return [Alert("BSOD", kind, subject, f"{last.get('name', '')} · {fmt_duration(max(0, now - last['time']))} ago", RED if last["kind"] == "bsod" else AMBER, "bsod")]

    def _check_claude(self) -> list[Alert]:
        src = self._src("claude")
        if src is None:
            return []
        out = []
        for s in src.state.get("sessions", []):
            if s["state"] != "waiting":
                continue
            if s["since"] > self.claude_seen.get(s["id"], 0.0):
                self.claude_seen[s["id"]] = s["since"]
                out.append(Alert("CLAUDE", "NEEDS YOU", s["project"], s.get("message", ""), PURPLE, "claude"))
        return out

    def _check_claude_usage(self) -> list[Alert]:
        """One toast per limit as it crosses alert_pct, and one when it drops back under."""
        src = self._src("claude_usage")
        limit = float(self.cfg.get("claude_usage", {}).get("alert_pct", 90))
        if src is None or limit <= 0:
            return []
        st = src.state
        if not st.get("statusline", False):
            return []
        out = []
        for key, label in (("five_pct", "5-HOUR"), ("week_pct", "WEEKLY")):
            pct = st.get(key)
            if pct is None:
                continue
            over = pct >= limit
            was = self.usage_over.get(key)
            self.usage_over[key] = over
            if was is None or over == was:
                continue
            if over:
                out.append(Alert("CLAUDE", label, f"{pct:.0f}% used", "usage limit", AMBER if pct < 100 else RED, "usage"))
            else:
                out.append(Alert("CLAUDE", label, f"{pct:.0f}% used", "back under the line", GREEN, "usage", "good"))
        return out


# --- rendering -------------------------------------------------------------------------

def _icon(d: ImageDraw.ImageDraw, cx: float, cy: float, style: str, color, t: float) -> None:
    s = 26
    if style == "good":
        d.line([(cx - s * 0.6, cy), (cx - s * 0.15, cy + s * 0.45), (cx + s * 0.65, cy - s * 0.5)], fill=color, width=6, joint="curve")
    elif style == "record":
        pts = []
        for i in range(10):
            r = s * (1.0 if i % 2 == 0 else 0.42)
            a = math.radians(-90 + i * 36 + math.sin(t * 3) * 6)
            pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
        d.polygon(pts, fill=color)
    else:
        d.polygon([(cx, cy - s), (cx + s, cy + s * 0.8), (cx - s, cy + s * 0.8)], fill=color)
        d.rectangle([cx - 3, cy - s * 0.45, cx + 3, cy + s * 0.25], fill=BG)
        d.ellipse([cx - 3, cy + s * 0.38, cx + 3, cy + s * 0.62], fill=BG)


def render_toast(alert: Alert, gap: int, t: float, seed: int = 0) -> list[Image.Image]:
    """Full-deck toast at scene time ``t``: ripple or sparkle, icon, kind / title / subject, detail."""
    c = Canvas(gap)
    d = c.draw
    color = alert.color
    cx, cy = c.key_center(7)
    if alert.style == "record":
        import random

        rng = random.Random(seed + int(t * 2))
        for _ in range(60):
            x, y = rng.uniform(0, c.w), rng.uniform(0, c.h)
            r = rng.uniform(1, 3.5)
            col = lerp_color(color, FG, rng.random() * 0.7)
            d.ellipse([x - r, y - r, x + r, y + r], fill=col)
        for k in range(3):
            phase = (t * 0.8 + k / 3) % 1.0
            bx, by = c.w * (0.2 + 0.3 * k), c.h * 0.45
            for i in range(14):
                a = math.radians(i * 360 / 14)
                rr = phase * 90
                px, py = bx + math.cos(a) * rr, by + math.sin(a) * rr
                d.ellipse([px - 2, py - 2, px + 2, py + 2], fill=lerp_color(color, BG, phase))
    else:
        for i in range(3):
            r = (t * 110 + i * 85) % 300
            f = max(0.0, 1.0 - r / 300)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=lerp_color(BG, color, f * 0.9), width=4)
    band = c.row_box(1)
    d.rectangle(band, fill=lerp_color(BG, color, 0.18))
    pulse = 0.85 + 0.15 * math.sin(t * 6)
    _icon(d, *c.key_center(2), alert.style, lerp_color(color, FG, 0.15 * (1 - pulse)), t)
    c.key_text(6, alert.kind, fit_size(alert.kind, 60, 20), color)
    c.key_text(7, alert.title, fit_size(alert.title, 62, 18), FG)
    c.key_text(8, alert.subject, fit_size(alert.subject, 62, 18), FG)
    detail = alert.detail or time.strftime("%H:%M", time.localtime(alert.at))
    lines = wrap_text(detail, 62, 10, max_lines=15)
    for i, line in enumerate(lines):
        key, row = 10 + i // 3, i % 3
        if key > 14:
            break
        c.key_text(key, line, 10, DIM if alert.style != "record" else FG, where="t", pad=10 + 17 * row, weight="semibold")
    c.key_text(4, "press = ok", 9, DIM, where="br", pad=4, weight="semibold")
    return c.slice()


def draw_badge(img: Image.Image, color) -> Image.Image:
    """A small corner dot on a board tile that still has an unacknowledged alert."""
    out = img.copy()
    d = ImageDraw.Draw(out)
    d.ellipse([56, 2, 70, 16], fill=BG)
    d.ellipse([58, 4, 68, 14], fill=color)
    return out
