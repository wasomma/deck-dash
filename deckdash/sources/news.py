"""Headlines from a few RSS feeds (feedparser), interleaved so no source hogs the ticker."""

from __future__ import annotations

import calendar
import html
import re
import time

import feedparser
import requests

from .base import Poller

_WS = re.compile(r"\s+")


def clean_title(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return _WS.sub(" ", s).strip()


def parse_feed(source: str, content: bytes | str, limit: int = 8) -> list[dict]:
    f = feedparser.parse(content)
    items = []
    for e in f.entries[:limit]:
        title = clean_title(e.get("title", ""))
        link = e.get("link") or ""
        if not title or not link:
            continue
        tp = e.get("published_parsed") or e.get("updated_parsed")
        when = calendar.timegm(tp) if tp else 0.0
        items.append({"source": source, "title": title, "url": link, "at": when})
    return items


def interleave(groups: list[list[dict]]) -> list[dict]:
    out = []
    for i in range(max((len(g) for g in groups), default=0)):
        for g in groups:
            if i < len(g):
                out.append(g[i])
    return out


class NewsPoller(Poller):
    def __init__(self, cfg: dict):
        n = cfg.get("news", {})
        super().__init__("news", float(n.get("refresh_minutes", 10)) * 60)
        self.feeds = [dict(f) for f in n.get("feeds", [])]
        self.per_feed = int(n.get("per_feed", 8))
        self._cache: dict[str, list[dict]] = {}

    def fetch(self) -> dict:
        if not self.feeds:
            raise RuntimeError("no feeds")
        groups = []
        errors = []
        for f in self.feeds:
            name = f.get("name", "?")
            try:
                r = requests.get(f["url"], timeout=15, headers={"User-Agent": "deck-dash/0.1 (+rss reader)"})
                r.raise_for_status()
                items = parse_feed(name, r.content, self.per_feed)
                if items:
                    self._cache[name] = items
            except (requests.RequestException, KeyError) as exc:
                errors.append(f"{name}: {type(exc).__name__}")
            groups.append(self._cache.get(name, []))
        items = interleave(groups)
        if not items:
            raise RuntimeError("; ".join(errors) or "no items")
        return {"items": items, "errors": errors, "fetched": time.time()}
