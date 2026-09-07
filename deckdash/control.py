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
import webbrowser
from multiprocessing.connection import Client, Listener
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PIPE = "deckdash"
SIM_PIPE = "deckdash-sim"
TIMEOUT_S = 3.0

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

def run_powershell(script: str, console: bool = False) -> int | None:
    """Start PowerShell on ``script`` detached from this process. Task Scheduler's job has the
    silent-breakaway flag, so the child leaves the job and survives the app's own restart."""
    if sys.platform != "win32":
        return None
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | (subprocess.CREATE_NEW_CONSOLE if console else subprocess.DETACHED_PROCESS)
    out = None if console else subprocess.DEVNULL
    proc = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                            creationflags=flags, stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True)
    log.info("detached powershell pid %d: %s", proc.pid, script)
    return proc.pid


def restart_script(root: Path) -> str:
    task = Path(root) / "tools" / "install_task.ps1"
    return f"& '{task}' -Stop; Start-Sleep -Seconds 2; & '{task}' -Start"


def calibrate_script(root: Path) -> str:
    task = Path(root) / "tools" / "install_task.ps1"
    tool = Path(root) / "tools" / "calibrate.py"
    python = Path(sys.executable).with_name("python.exe")  # a console, not pythonw: the tool prints its instructions
    return (f"& '{task}' -Stop; Start-Sleep -Seconds 1; & '{python}' '{tool}'; & '{task}' -Start; "
            "Write-Host 'deck-dash restarted; press Enter to close'; Read-Host | Out-Null")


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
