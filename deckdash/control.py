"""Control channel: a Windows named pipe that ``deckdash ctl``, the tray and the dashboard use.

The server thread accepts one request per connection and hands it to the render loop through
``App.submit``: commands run on the loop thread, between ticks, so every deck write stays on one
thread. stdlib ``multiprocessing.connection`` does the pipe work: no port, same user only,
nothing to configure. The helpers at the end start the Task Scheduler scripts (restart,
recalibrate) and the dashboard window in processes that outlive this one.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
import webbrowser
from multiprocessing.connection import Client, Listener
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

DEFAULT_PIPE = "deckdash"
SIM_PIPE = "deckdash-sim"
TIMEOUT_S = 3.0
RECV_TIMEOUT_S = 2.0  # a connected client that has not spoken by then is dropped: it must not own the accept loop

# name -> (min args, max args, usage)
COMMANDS: dict[str, tuple[int, int, str]] = {
    "status": (0, 0, "status"),
    "wake": (0, 0, "wake"),
    "scene": (1, 1, "scene NAME"),
    "next": (0, 0, "next"),
    "pause": (0, 0, "pause"),
    "resume": (0, 0, "resume"),
    "brightness": (1, 1, "brightness N|auto"),
    "toast": (2, 3, "toast KIND TITLE [DETAIL]"),
    "reload": (0, 0, "reload"),
    "quit": (0, 0, "quit"),
}


def pipe_address(name: str = DEFAULT_PIPE) -> str:
    if sys.platform == "win32":
        return "\\\\.\\pipe\\" + name
    return f"/tmp/{name}.sock"


def _family() -> str:
    return "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"


def validate(cmd: str, args: list[str]) -> str | None:
    """A usage error for a command line, or None when it is well-formed."""
    spec = COMMANDS.get(cmd)
    if spec is None:
        return f"unknown command '{cmd}' (known: {', '.join(COMMANDS)})"
    lo, hi, usage = spec
    if not lo <= len(args) <= hi:
        return f"usage: {usage}"
    return None


class ControlServer(threading.Thread):
    """Serves the pipe on a daemon thread; each command runs on the loop thread via ``app.submit``."""

    def __init__(self, app, address: str):
        super().__init__(name="control", daemon=True)
        self.app = app
        self.address = address
        self.ready = threading.Event()
        self.error: str | None = None
        self._listener: Listener | None = None
        self._stopping = False

    def run(self) -> None:
        try:
            self._listener = Listener(self.address, family=_family())
        except OSError as exc:
            self.error = str(exc)
            log.warning("control pipe %s unavailable: %s", self.address, exc)
            self.ready.set()
            return
        log.info("control pipe %s", self.address)
        self.ready.set()
        while not self._stopping:
            try:
                conn = self._listener.accept()
            except OSError:
                if self._stopping:
                    break
                continue
            if self._stopping:
                conn.close()
                break
            try:
                # The pipe's default ACL lets any local user open it read-only, and recv() waits
                # forever, so a peer that connects and never speaks would park this thread and take
                # the whole control channel down until a restart. Give it a deadline and move on.
                if not conn.poll(RECV_TIMEOUT_S):
                    log.warning("control client sent nothing within %g s; dropped", RECV_TIMEOUT_S)
                    continue
                cmd, args = conn.recv()
                args = [str(a) for a in args]
                err = validate(str(cmd), args)
                reply = {"ok": False, "error": err} if err else self.app.submit(str(cmd), args, timeout=TIMEOUT_S)
                conn.send(reply)
            except Exception as exc:  # noqa: BLE001 - a bad client must not take the server down
                log.warning("control request failed: %s", exc)
            finally:
                conn.close()
        try:
            self._listener.close()  # closed by the thread that owns it, after accept() has returned
        except OSError:
            pass

    def stop(self) -> None:
        self._stopping = True
        if self._listener is None:
            return
        try:
            Client(self.address, family=_family()).close()  # unblocks accept()
        except OSError:
            pass


def send(cmd: str, args: list[str] | None = None, pipe: str = DEFAULT_PIPE, timeout: float = TIMEOUT_S) -> dict:
    """One request to the running deck-dash. Always a dict with ``ok``; never raises."""
    args = [str(a) for a in (args or [])]
    err = validate(cmd, args)
    if err:
        return {"ok": False, "error": err}
    try:
        with Client(pipe_address(pipe), family=_family()) as conn:
            conn.send((cmd, args))
            if not conn.poll(timeout):
                return {"ok": False, "error": f"no reply within {timeout:g} s"}
            reply = conn.recv()
    except FileNotFoundError:
        return {"ok": False, "error": "deck-dash is not running (no control pipe)"}
    except (OSError, EOFError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return reply if isinstance(reply, dict) else {"ok": False, "error": f"bad reply: {reply!r}"}


# --- processes that must outlive this one ---------------------------------------------------

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PS_LOG = config.ROOT / "logs" / "powershell.log"


def run_powershell(script: str, console: bool = False) -> int | None:
    """Start PowerShell on ``script`` in a process that outlives this one.

    Not DETACHED_PROCESS: with no console PowerShell exits 0 without running ``-Command`` at all,
    so every restart from the tray, the dashboard and ``ctl restart`` reported a pid and did
    nothing (measured 2026-09-07). CREATE_NO_WINDOW hides the window and still runs the script,
    and survival does not depend on the flag anyway: the task's job carries silent-breakaway
    (limit flags 0x3000), so children leave the job on their own.

    Output goes to ``logs/powershell.log`` rather than DEVNULL. That is what made the failure
    silent: a restart that throws must leave a trace somewhere.
    """
    if sys.platform != "win32":
        return None
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | (subprocess.CREATE_NEW_CONSOLE if console else _NO_WINDOW)
    out = None
    if not console:
        try:
            PS_LOG.parent.mkdir(parents=True, exist_ok=True)
            out = open(PS_LOG, "a", encoding="utf-8", errors="replace")  # noqa: SIM115 - the child owns it
            out.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} {script}\n")
            out.flush()
        except OSError:
            out = subprocess.DEVNULL
    proc = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                            creationflags=flags, stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True)
    if hasattr(out, "close"):
        out.close()  # the child holds its own handle now
    log.info("powershell pid %d: %s", proc.pid, script)
    return proc.pid


def restart_script(root: Path) -> str:
    """Stop then start the task. The -Start is in a ``finally`` so a throwing -Stop (or a -Stop
    that killed the app before Start-ScheduledTask failed) still leaves the deck with an owner."""
    task = Path(root) / "tools" / "install_task.ps1"
    return f"try {{ & '{task}' -Stop; Start-Sleep -Seconds 2 }} finally {{ & '{task}' -Start }}"


def calibrate_script(root: Path) -> str:
    """Free the deck, run the calibration tool in its own console, then start the task again.
    The tool is interactive, so the restart is in a ``finally``: Ctrl+C or a crashing tool must not
    leave the deck dark. Closing the console window with the X still skips it (Windows kills the
    process outright) - the message says what to run then."""
    task = Path(root) / "tools" / "install_task.ps1"
    tool = Path(root) / "tools" / "calibrate.py"
    python = Path(sys.executable).with_name("python.exe")  # a console, not pythonw: the tool prints its instructions
    return (f"Write-Host 'If this window is closed with the X, run: {task} -Start'; "
            f"try {{ & '{task}' -Stop; Start-Sleep -Seconds 1; & '{python}' '{tool}' }} "
            f"finally {{ & '{task}' -Start; "
            "Write-Host 'deck-dash restarted; press Enter to close'; Read-Host | Out-Null }")


EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def open_window(url: str) -> str:
    """Open ``url`` as its own window: Edge in app mode (no browser chrome, own taskbar entry)
    when it is installed, else the default browser. Returns which one was used."""
    if sys.platform == "win32" and EDGE.exists():
        subprocess.Popen([str(EDGE), f"--app={url}"], close_fds=True,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        return "edge"
    webbrowser.open(url)
    return "browser"
