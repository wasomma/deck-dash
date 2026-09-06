"""Is the Windows session locked? Polled, no window message loop needed.

While the lock screen (or another secure desktop) is up, the interactive input desktop is
not ``Default``, or cannot be opened at all from the user's session. Either means locked.
"""

from __future__ import annotations

import ctypes
import sys

DESKTOP_SWITCHDESKTOP = 0x0100
UOI_NAME = 2


def session_locked() -> bool:
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    handle = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not handle:
        return True
    try:
        buf = ctypes.create_unicode_buffer(64)
        needed = ctypes.c_ulong(0)
        ok = user32.GetUserObjectInformationW(handle, UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed))
        if not ok:
            return False
        return buf.value.lower() != "default"
    finally:
        user32.CloseDesktop(handle)
