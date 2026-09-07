"""Windows PDH counters through ctypes (no extra package).

psutil reports the nominal clock on Windows (3.0 GHz on the i9-13900K whatever the cores are doing),
so the CPU tile's clock comes from ``\\Processor Information(_Total)\\% Processor Performance`` times
the nominal frequency counter. Rate counters need two collects; ``read()`` returns ``None`` until
the second one. A collect costs about 0.25 ms.
"""

from __future__ import annotations

import ctypes
import sys

PDH_FMT_DOUBLE = 0x00000200
PERF_PATH = r"\Processor Information(_Total)\% Processor Performance"
FREQ_PATH = r"\Processor Information(_Total)\Processor Frequency"


class _CounterValue(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_ulong), ("doubleValue", ctypes.c_double)]


class CpuClock:
    """Effective CPU clock in GHz. Opened lazily on the calling thread; never raises."""

    def __init__(self, nominal_mhz: float | None = None):
        self.nominal_mhz = nominal_mhz
        self.error: str | None = None
        self._pdh = None
        self._query = None
        self._perf = None
        self._freq = None

    def _open(self) -> bool:
        if self._query is not None:
            return True
        if self.error is not None or sys.platform != "win32":
            return False
        try:
            pdh = ctypes.windll.pdh
            query = ctypes.c_void_p()
            rc = pdh.PdhOpenQueryW(None, 0, ctypes.byref(query))
            if rc != 0:
                raise OSError(f"PdhOpenQuery 0x{rc & 0xFFFFFFFF:08X}")
            handles = []
            for path in (PERF_PATH, FREQ_PATH):
                h = ctypes.c_void_p()
                rc = pdh.PdhAddEnglishCounterW(query, path, 0, ctypes.byref(h))
                if rc != 0:
                    pdh.PdhCloseQuery(query)
                    raise OSError(f"PdhAddEnglishCounter({path}) 0x{rc & 0xFFFFFFFF:08X}")
                handles.append(h)
            pdh.PdhCollectQueryData(query)  # first sample; the rate needs a second one
            self._pdh, self._query = pdh, query
            self._perf, self._freq = handles
            return True
        except (OSError, AttributeError) as exc:
            self.error = str(exc)
            return False

    def _value(self, handle) -> float | None:
        v = _CounterValue()
        typ = ctypes.c_ulong()
        rc = self._pdh.PdhGetFormattedCounterValue(handle, PDH_FMT_DOUBLE, ctypes.byref(typ), ctypes.byref(v))
        return v.doubleValue if rc == 0 else None

    def read(self) -> float | None:
        """GHz, or ``None`` when the counters are unavailable or not yet warmed up."""
        if not self._open():
            return None
        if self._pdh.PdhCollectQueryData(self._query) != 0:
            return None
        perf = self._value(self._perf)
        if perf is None:
            return None
        mhz = self.nominal_mhz or self._value(self._freq)
        if not mhz:
            return None
        return perf / 100.0 * mhz / 1000.0

    def close(self) -> None:
        if self._pdh is not None and self._query is not None:
            self._pdh.PdhCloseQuery(self._query)
        self._query = None
