from __future__ import annotations

import argparse
import ctypes
import logging
import logging.handlers
import sys
import time
from pathlib import Path

from . import __version__, config
from .app import App
from .device import RealDeck, SimDeck

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger(__name__)

_mutex_handle = None


def setup_logging(verbose: bool) -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(log_dir / "deckdash.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:  # pythonw.exe has no console
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def single_instance(name: str = "Local\\deck-dash") -> bool:
    """Hold a named mutex so a second copy (manual run next to the scheduled task) exits early."""
    global _mutex_handle
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    _mutex_handle = kernel32.CreateMutexW(None, False, name)
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


_PRIORITY_NAMES = {0x40: "idle", 0x4000: "below normal", 0x20: "normal", 0x8000: "above normal", 0x80: "high", 0x100: "realtime"}


class _PowerThrottling(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]


class _MemoryPriority(ctypes.Structure):
    _fields_ = [("MemoryPriority", ctypes.c_ulong)]


class _JobBasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_ulong), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_ulong),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_ulong), ("SchedulingClass", ctypes.c_ulong)]


class _JobCpuRate(ctypes.Structure):
    _fields_ = [("ControlFlags", ctypes.c_ulong), ("CpuRate", ctypes.c_ulong)]


def normal_priority() -> str:
    """Normal CPU class, normal memory priority, no power throttling; say what was found and set.

    Task Scheduler launches a task at below-normal CPU priority (7) unless told otherwise, at memory
    priority 4, and as a windowless background process that Windows 11 may run with efficiency QoS
    (E-cores, coalesced timers). Measured on 2026-09-06: below normal starved the flush under load
    (150-320 ms spikes); with everything else equal, task-launched board mode logged 100-140 ms
    flushes several times a minute on an idle machine while a shell-launched copy logged none, and
    opting the live process out of power throttling ended them at once (0 in 2 min) where memory
    priority alone did not. ``install_task.ps1`` registers with priority 5; this covers the rest.
    """
    if sys.platform != "win32":
        return "n/a"
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = ctypes.c_void_p  # the pseudo handle is -1: keep all 64 bits
    k.GetPriorityClass.argtypes = [ctypes.c_void_p]
    k.GetPriorityClass.restype = ctypes.c_uint
    k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    k.GetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    k.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    proc = k.GetCurrentProcess()
    was_class = _PRIORITY_NAMES.get(k.GetPriorityClass(proc), "unknown")
    if k.GetPriorityClass(proc) in (0x4000, 0x40):
        k.SetPriorityClass(proc, 0x20)
    now_class = _PRIORITY_NAMES.get(k.GetPriorityClass(proc), "unknown")
    mem = _MemoryPriority(0)
    k.GetProcessInformation(proc, 0, ctypes.byref(mem), ctypes.sizeof(mem))  # 0 = ProcessMemoryPriority
    was_mem = mem.MemoryPriority
    if 0 < was_mem < 5:
        mem.MemoryPriority = 5  # MEMORY_PRIORITY_NORMAL
        k.SetProcessInformation(proc, 0, ctypes.byref(mem), ctypes.sizeof(mem))
        k.GetProcessInformation(proc, 0, ctypes.byref(mem), ctypes.sizeof(mem))
    throttle = _PowerThrottling(1, 0x1 | 0x4, 0)  # control execution speed + timer resolution; state 0 = never throttle
    no_throttle = bool(k.SetProcessInformation(proc, 4, ctypes.byref(throttle), ctypes.sizeof(throttle)))  # 4 = ProcessPowerThrottling
    return (f"priority {now_class} (was {was_class}), memory priority {mem.MemoryPriority} (was {was_mem}), "
            f"power throttling {'off' if no_throttle else 'unchanged'}")


def job_summary() -> str:
    """Limits of the job object this process runs in (Task Scheduler puts tasks in one), for the log."""
    if sys.platform != "win32":
        return "n/a"
    k = ctypes.windll.kernel32
    k.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
    basic = _JobBasicLimits()
    if not k.QueryInformationJobObject(None, 2, ctypes.byref(basic), ctypes.sizeof(basic), None):  # 2 = basic limits
        return "none"
    cpu = _JobCpuRate()
    rate = "n/a"
    if k.QueryInformationJobObject(None, 15, ctypes.byref(cpu), ctypes.sizeof(cpu), None):  # 15 = CPU rate control
        rate = f"0x{cpu.ControlFlags:x}/{cpu.CpuRate}"
    return f"limit flags 0x{basic.LimitFlags:x}, scheduling class {basic.SchedulingClass}, cpu rate control {rate}"


def open_with_retry(deck: RealDeck, retry_s: float = 10.0) -> None:
    """Wait for the deck rather than crash: at logon the USB stack may still be waking up."""
    attempt = 0
    while True:
        try:
            deck.open()
            return
        except Exception as exc:  # noqa: BLE001
            attempt += 1
            if attempt in (1, 6, 30) or attempt % 360 == 0:
                log.warning("deck not available (attempt %d): %s", attempt, exc)
            time.sleep(retry_s)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="deckdash", description="Stream Deck ambient info display")
    p.add_argument("--sim", action="store_true", help="no hardware: write sim/canvas.png instead")
    p.add_argument("--seconds", type=float, default=0.0, help="run for this long, then exit (0 = forever)")
    p.add_argument("--config", default=None, help="path to config.toml")
    p.add_argument("--hidapi", default=None, help="folder containing hidapi.dll (overrides config)")
    p.add_argument("--gap", type=int, default=None, help="bezel gap in px (overrides config)")
    p.add_argument("--ambient", default=None, metavar="SCENE", help="start in this ambient scene and return to it 5 s after any press")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"deckdash {__version__}")
    args = p.parse_args(argv)

    setup_logging(args.verbose)
    if not args.sim and not single_instance():
        log.error("another deck-dash is already running (scheduled task?); exiting")
        return 3
    cfg = config.load(args.config)
    dk = cfg.setdefault("deck", {})
    if args.gap is not None:
        dk["gap_px"] = args.gap
    if args.sim:
        deck = SimDeck(gap=int(dk.get("gap_px", 24)), out_dir=ROOT / "sim")
    else:
        deck = RealDeck(hidapi_dir=args.hidapi or dk.get("hidapi_dir"), brightness=int(dk.get("brightness", 80)))
        open_with_retry(deck)
    if args.ambient is not None:
        dk["idle_minutes"] = 5 / 60
    app = App(cfg, deck)
    if args.ambient is not None:
        app.forced_scene = args.ambient
        app.start_ambient(time.time(), args.ambient)
    log.info("deckdash %s starting (%s), %s; job: %s", __version__, "simulator" if args.sim else "hardware", normal_priority(), job_summary())
    app.run(max_seconds=args.seconds or None)
    return 0
