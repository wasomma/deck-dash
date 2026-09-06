"""GitHub Actions and pull requests for a list of repos, through the ``gh`` CLI (already logged in)."""

from __future__ import annotations

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .base import Poller

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _iso_to_epoch(s: str | None) -> float:
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def normalize_repos(entries: list) -> list[dict]:
    """Config entries may be 'owner/name' strings or {repo, label} tables."""
    out = []
    for e in entries:
        if isinstance(e, str):
            out.append({"repo": e, "label": e.split("/")[-1]})
        else:
            repo = str(e.get("repo", ""))
            out.append({"repo": repo, "label": str(e.get("label") or repo.split("/")[-1])})
    return out


def summarize_repo(repo: str, label: str, runs: dict | list | None, pulls: list | None) -> dict:
    """Reduce the REST payloads of one repo to what the tile shows."""
    if isinstance(runs, dict):
        runs = runs.get("workflow_runs") or []
    runs = runs or []
    pulls = pulls or []
    latest = runs[0] if runs else None
    if latest is None:
        status = "none"
    elif latest.get("status") != "completed":
        status = "running"
    else:
        c = latest.get("conclusion") or ""
        status = "ok" if c == "success" else "cancelled" if c in ("cancelled", "skipped", "neutral") else "fail"
    return {
        "repo": repo,
        "label": label,
        "status": status,
        "workflow": (latest or {}).get("name") or "",
        "title": (latest or {}).get("display_title") or "",
        "branch": (latest or {}).get("head_branch") or "",
        "event": (latest or {}).get("event") or "",
        "url": (latest or {}).get("html_url") or "",
        "at": _iso_to_epoch((latest or {}).get("updated_at") or (latest or {}).get("created_at")),
        "prs": [
            {
                "number": p.get("number"),
                "title": p.get("title") or "",
                "draft": bool(p.get("draft")),
                "url": p.get("html_url") or "",
                "at": _iso_to_epoch(p.get("updated_at")),
                "repo": label,
            }
            for p in pulls
        ],
    }


def _gh(args: list[str], timeout: float = 25) -> object:
    p = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[:160] or f"gh exit {p.returncode}")
    return json.loads(p.stdout or "null")


class GhPoller(Poller):
    def __init__(self, cfg: dict):
        c = cfg.get("ci", {})
        super().__init__("ci", float(c.get("poll_seconds", 120)))
        self.repos = normalize_repos(list(c.get("repos", [])))
        self.fast = float(c.get("poll_seconds_running", 30))
        self.base_interval = self.interval

    def _one(self, entry: dict) -> dict:
        repo, label = entry["repo"], entry["label"]
        try:
            runs = _gh([f"repos/{repo}/actions/runs?per_page=3"])
            pulls = _gh([f"repos/{repo}/pulls?state=open&per_page=20"])
            out = summarize_repo(repo, label, runs, pulls)
        except Exception as exc:  # noqa: BLE001 - one repo failing must not blank the others
            out = summarize_repo(repo, label, None, None)
            out["status"] = "error"
            out["title"] = f"{type(exc).__name__}: {exc}"[:80]
        return out

    def fetch(self) -> dict:
        if not self.repos:
            raise RuntimeError("no repos configured")
        with ThreadPoolExecutor(max_workers=min(6, len(self.repos))) as pool:
            repos = list(pool.map(self._one, self.repos))
        prs = sorted((p for r in repos for p in r["prs"]), key=lambda p: -p["at"])
        running = any(r["status"] == "running" for r in repos)
        self.interval = self.fast if running else self.base_interval
        return {
            "repos": repos,
            "prs": prs,
            "running": running,
            "failed": sum(1 for r in repos if r["status"] == "fail"),
            "checked": time.time(),
        }
