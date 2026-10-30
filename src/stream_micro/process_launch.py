"""Shared launch policy for non-interactive background child processes.

Interactive session launchers deliberately use CREATE_NEW_CONSOLE instead.
Keep monitoring, discovery and stdio servers on this policy so new providers
cannot accidentally create command windows from the tray application.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def background_process_options() -> dict[str, Any]:
    if sys.platform != "win32":
        return {"creationflags": 0, "startupinfo": None}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }
